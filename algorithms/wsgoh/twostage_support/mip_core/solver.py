from __future__ import annotations

import contextlib
import io
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from algorithms.yongs.only_virtual2 import solver as initial_solver
from ocam.models import (
    CascadingSolution,
    Delivery,
    DryDock,
    Idle,
    InLaneEvent,
    InLaneSail,
    InstanceData,
    OutLaneSail,
    PhaseIn,
    PhaseOut,
    PortStay,
    Redelivery,
    TransshipmentLoad,
    TransshipmentUnload,
    VesselScheduleEvent,
)
from ocam.utils import event_end_port_code, event_end_time, event_start_port_code, event_start_time, lookup_distance
from ocam.validation import can_follow_event, evaluate_solution, validate_solution

DESCRIPTION = "Two-stage support: core restricted-pattern MIP utilities seeded by yongs/only_virtual2."

CoverageKey = tuple[str, str, int, int, str, datetime, datetime, datetime, datetime]
PositionKey = tuple[str, str, int]

MAX_SWITCH_DEPTH = 2
TOP_K_SWITCH_PATTERNS_PER_FRAGMENT = 3
TOP_K_INITIAL_VIRTUAL_SWITCH_PATTERNS_PER_FRAGMENT = 12
TOP_K_PARTIAL_VIRTUAL_TAKEOVER_PATTERNS_PER_SPLIT = 6
MAX_PATTERNS_PER_VESSEL = 80
MAX_TOTAL_PATTERNS = 6000


@dataclass(frozen=True)
class Pattern:
    pattern_id: str
    vessel_code: str
    is_virtual: bool
    schedule_payload: dict[str, Any]
    coverage_keys: frozenset[CoverageKey]
    cost: float
    requires_pattern_ids: frozenset[str] = field(default_factory=frozenset)
    depth: int = 0
    priority: float = 0.0
    source_fragment_id: str | None = None
    target_position_key: PositionKey | None = None
    dropped_position_key: PositionKey | None = None
    split_mode: str | None = None


@dataclass
class ServiceFragment:
    fragment_id: str
    position_key: PositionKey
    schedule: list[Any]
    source: str
    depth: int
    priority: float
    requires_pattern_id: str | None = None


@dataclass
class SwitchCandidate:
    vessel_code: str
    schedule: list[Any]
    dropped_suffix: list[Any]
    score: float
    split_mode: str
    dropped_position_key: PositionKey | None


@dataclass
class SplitCandidate:
    prefix: list[Any]
    suffix: list[Any]
    mode: str
    slack_hours: float


@dataclass
class CandidateSearchResult:
    candidates: list[SwitchCandidate] = field(default_factory=list)
    rejections: Counter[str] = field(default_factory=Counter)


@dataclass
class PruneStats:
    input_count: int
    schedule_duplicate_pruned: int = 0
    coverage_duplicate_pruned: int = 0
    vessel_cap_pruned: int = 0
    total_cap_pruned: int = 0
    orphan_pruned: int = 0


def _load_gurobi():
    try:
        import gurobipy as gp
        from gurobipy import GRB
    except ImportError as exc:
        raise RuntimeError(
            "wsgoh/twostage_support/mip_core requires gurobipy at runtime. "
            "Install Gurobi's Python package or run OCAM in an environment where gurobipy is available."
        ) from exc
    return gp, GRB


def _make_initial_solution(instance_data: InstanceData, timelimit: int) -> CascadingSolution:
    initial_solver.NUM_VIRTUAL_VESSELS_USED = 0
    initial_solver.USED_VIRTUAL_VESSEL_CODES.clear()
    return initial_solver.algorithm(instance_data, timelimit)


def _clone_solution(solution: CascadingSolution) -> CascadingSolution:
    return CascadingSolution(
        declared_positions=[declared_position.to_dict() for declared_position in solution.declared_positions],
        vessel_schedules=solution.vessel_schedules.to_dict(),
        virtual_vessel_schedules=solution.virtual_vessel_schedules.to_dict(),
        num_virtual_vessels_used=solution.num_virtual_vessels_used,
    )


def _evaluate_total_cost(solution: CascadingSolution, instance_data: InstanceData) -> float:
    objective = evaluate_solution(solution, instance_data)
    if objective is None:
        raise ValueError("wsgoh/twostage_support/mip_core: evaluate_solution returned None for a non-empty solution.")
    return float(objective["total_cost"])


def _coverage_key(event: PortStay) -> CoverageKey:
    return (
        event.lane_code,
        event.proforma_name,
        event.position_no,
        event.port_seq,
        event.port_code,
        event.pilot_in_start,
        event.berthing_start,
        event.berthing_end,
        event.pilot_out_end,
    )


def _safe_name(value: object) -> str:
    text = str(value)
    return "".join(char if char.isalnum() else "_" for char in text)


def _pattern_family(pattern: Pattern | str) -> str:
    pattern_id = pattern.pattern_id if isinstance(pattern, Pattern) else pattern
    parts = pattern_id.split(":")
    if parts[0].startswith("actual-switch-d"):
        return "actual-switch"
    if parts[0].startswith("actual-partial-virtual"):
        return "actual-partial-virtual"
    if parts[0].startswith("virtual-suffix-fallback-d"):
        return "virtual-suffix-fallback"
    if parts[0].startswith("virtual-partial-prefix"):
        return "virtual-partial-prefix"
    return parts[0]


def _format_counts(counts: Counter[str]) -> str:
    if not counts:
        return "(none)"
    return ", ".join(f"{key}={counts[key]}" for key in sorted(counts))


def _format_position_key(position_key: PositionKey | None) -> str:
    if position_key is None:
        return "-"
    return f"{position_key[0]}/{position_key[1]}/{position_key[2]}"


def _coverage_position_counts(pattern: Pattern) -> Counter[PositionKey]:
    return Counter((key[0], key[1], key[2]) for key in pattern.coverage_keys)


def _format_coverage_positions(pattern: Pattern) -> str:
    counts = _coverage_position_counts(pattern)
    if not counts:
        return "-"
    return ", ".join(
        f"{_format_position_key(position_key)}:{counts[position_key]}"
        for position_key in sorted(counts)
    )


def _clone_schedule(schedule: list[Any]) -> list[Any]:
    return [VesselScheduleEvent.coerce(event.to_dict()) for event in schedule]


def _schedule_payload_signature(schedule_payload: dict[str, Any]) -> tuple[tuple[tuple[str, Any], ...], ...]:
    return tuple(tuple(sorted(event_payload.items())) for event_payload in schedule_payload["events"])


def _declared_position_keys(instance_data: InstanceData, solution: CascadingSolution) -> set[PositionKey]:
    declared_by_version: dict[tuple[str, str], set[int]] = {}
    for declared_position in solution.declared_positions:
        key = (declared_position.lane_code, declared_position.proforma_name)
        declared_by_version.setdefault(key, set()).add(declared_position.declared_position_no)

    declared_keys: set[PositionKey] = set()
    for lane in instance_data.service_lanes:
        lane_code = lane["lane_code"]
        for version in lane["versions"]:
            proforma_name = version["proforma_name"]
            version_positions = version["declared_positions"] or sorted(
                declared_by_version.get((lane_code, proforma_name), set())
            )
            for position_no in version_positions:
                declared_keys.add((lane_code, proforma_name, int(position_no)))
    return declared_keys


def _fixed_position_keys(instance_data: InstanceData) -> set[PositionKey]:
    fixed_keys: set[PositionKey] = set()
    for lane in instance_data.service_lanes:
        lane_code = lane["lane_code"]
        for version in lane["versions"]:
            proforma_name = version["proforma_name"]
            for position_no in version["declared_positions"] or []:
                fixed_keys.add((lane_code, proforma_name, int(position_no)))
    return fixed_keys


def _selectable_position_keys(instance_data: InstanceData) -> set[PositionKey]:
    selectable_keys: set[PositionKey] = set()
    for lane in instance_data.service_lanes:
        lane_code = lane["lane_code"]
        for version in lane["versions"]:
            proforma_name = version["proforma_name"]
            for position_no in version["available_positions"] or []:
                selectable_keys.add((lane_code, proforma_name, int(position_no)))
    return selectable_keys


def _initial_selectable_position_values(instance_data: InstanceData, solution: CascadingSolution) -> dict[PositionKey, int]:
    selected_positions = {
        (position.lane_code, position.proforma_name, position.declared_position_no)
        for position in solution.declared_positions
    }
    values: dict[PositionKey, int] = {}
    for lane in instance_data.service_lanes:
        lane_code = lane["lane_code"]
        for version in lane["versions"]:
            proforma_name = version["proforma_name"]
            for position_no in version["available_positions"] or []:
                key = (lane_code, proforma_name, int(position_no))
                values[key] = 1 if key in selected_positions else 0
    return values


def _declared_position_payload(position_keys: set[PositionKey]) -> list[dict[str, Any]]:
    return [
        {
            "lane_code": lane_code,
            "proforma_name": proforma_name,
            "declared_position_no": position_no,
        }
        for lane_code, proforma_name, position_no in sorted(position_keys)
    ]


def _required_coverage_keys(solution: CascadingSolution, position_keys: set[PositionKey]) -> set[CoverageKey]:
    required_keys: set[CoverageKey] = set()
    for lane_key, lane_events in solution.to_lane_view().items():
        if lane_key not in position_keys:
            continue
        for lane_event in lane_events:
            if isinstance(lane_event.event, PortStay):
                required_keys.add(_coverage_key(lane_event.event))
    return required_keys


def _schedule_coverage_keys(schedule, position_keys: set[PositionKey]) -> frozenset[CoverageKey]:
    keys: set[CoverageKey] = set()
    for event in schedule:
        if not isinstance(event, PortStay):
            continue
        if (event.lane_code, event.proforma_name, event.position_no) in position_keys:
            keys.add(_coverage_key(event))
    return frozenset(keys)


def _make_position_schedule(position_key: PositionKey, instance_data: InstanceData):
    lane_code, proforma_name, position_no = position_key
    return initial_solver.make_inlane_schedule(
        lane_code,
        proforma_name,
        position_no,
        instance_data.planning_horizon["start"],
        instance_data.planning_horizon["end"],
    )


def _position_schedules(position_keys: set[PositionKey], instance_data: InstanceData) -> dict[PositionKey, list[Any]]:
    return {position_key: _make_position_schedule(position_key, instance_data) for position_key in sorted(position_keys)}


def _silent_evaluate_total_cost(solution: CascadingSolution, instance_data: InstanceData) -> float:
    with contextlib.redirect_stdout(io.StringIO()):
        objective = evaluate_solution(solution, instance_data)
    return 0.0 if objective is None else float(objective["total_cost"])


def _estimate_pattern_cost(
    instance_data: InstanceData,
    declared_positions_payload: list[dict[str, Any]],
    vessel_code: str,
    schedule_payload: dict[str, Any],
    is_virtual: bool,
) -> float:
    solution = CascadingSolution(
        declared_positions=declared_positions_payload if is_virtual else [],
        vessel_schedules={} if is_virtual else {vessel_code: schedule_payload},
        virtual_vessel_schedules={vessel_code: schedule_payload} if is_virtual else {},
        num_virtual_vessels_used=1 if is_virtual else 0,
    )
    try:
        isolated_cost = max(0.0, _silent_evaluate_total_cost(solution, instance_data))
    except Exception:
        isolated_cost = 1.0 if is_virtual else 0.0

    return isolated_cost + _transshipment_unload_cost(instance_data, schedule_payload)


def _transshipment_unload_cost(instance_data: InstanceData, schedule_payload: dict[str, Any]) -> float:
    ts_cost_by_key: dict[tuple[str, str, str], float] = {}
    for row in instance_data.transshipment_cost:
        year_month = row["year_month"]
        lane_code = row["lane_code"]
        for port in row["ports"]:
            ts_cost_by_key[(year_month, lane_code, port["port_code"])] = float(port["ts_cost"])

    cost = 0.0
    for event_payload in schedule_payload["events"]:
        if event_payload.get("status") != TransshipmentUnload.status:
            continue
        event = TransshipmentUnload.from_dict(event_payload)
        key = (event.unload_start.strftime("%Y%m"), event.lane_code, event.ts_port_code)
        try:
            cost += ts_cost_by_key[key]
        except KeyError as exc:
            raise ValueError(
                "wsgoh/twostage_support/mip_core: missing transshipment cost for "
                f"year_month={key[0]!r}, lane={key[1]!r}, port={key[2]!r}."
            ) from exc
    return cost


def _build_patterns(
    instance_data: InstanceData,
    initial_solution: CascadingSolution,
    coverage_positions: set[PositionKey],
    selectable_positions: set[PositionKey],
    coverage_schedules: dict[PositionKey, list[Any]],
    selectable_schedules: dict[PositionKey, list[Any]],
) -> list[Pattern]:
    selected_positions = {
        (position.lane_code, position.proforma_name, position.declared_position_no)
        for position in initial_solution.declared_positions
    }
    all_selectable_position_payload = _declared_position_payload(selectable_positions)
    patterns: list[Pattern] = []

    for vessel_code, schedule in initial_solution.vessel_schedules.items():
        schedule_payload = schedule.to_dict()
        patterns.append(
            Pattern(
                pattern_id=f"actual:{vessel_code}",
                vessel_code=vessel_code,
                is_virtual=False,
                schedule_payload=schedule_payload,
                coverage_keys=_schedule_coverage_keys(schedule, coverage_positions),
                cost=_estimate_pattern_cost(
                    instance_data,
                    all_selectable_position_payload,
                    vessel_code,
                    schedule_payload,
                    is_virtual=False,
                ),
            )
        )

    for vessel_code, schedule in initial_solution.virtual_vessel_schedules.items():
        schedule_payload = schedule.to_dict()
        patterns.append(
            Pattern(
                pattern_id=f"virtual:{vessel_code}",
                vessel_code=vessel_code,
                is_virtual=True,
                schedule_payload=schedule_payload,
                coverage_keys=_schedule_coverage_keys(schedule, coverage_positions),
                cost=_estimate_pattern_cost(
                    instance_data,
                    all_selectable_position_payload,
                    vessel_code,
                    schedule_payload,
                    is_virtual=True,
                ),
            )
        )

    for position_key, schedule in coverage_schedules.items():
        vessel_code = f"MIPVIRTUAL_{position_key[0]}_{position_key[1]}_{position_key[2]}"
        schedule_payload = {"events": [event.to_dict() for event in schedule]}
        patterns.append(
            Pattern(
                pattern_id=f"virtual-fallback:{position_key[0]}:{position_key[1]}:{position_key[2]}",
                vessel_code=vessel_code,
                is_virtual=True,
                schedule_payload=schedule_payload,
                coverage_keys=_schedule_coverage_keys(schedule, coverage_positions),
                cost=_estimate_pattern_cost(
                    instance_data,
                    all_selectable_position_payload,
                    vessel_code,
                    schedule_payload,
                    is_virtual=True,
                ),
            )
        )

    virtual_replacement_count = 0
    fixed_positions = coverage_positions - selectable_positions
    for virtual_code, virtual_schedule in initial_solution.virtual_vessel_schedules.items():
        position_key = _position_key_from_schedule(virtual_schedule)
        if position_key is None:
            continue
        for vessel_code, vessel_schedule in initial_solution.vessel_schedules.items():
            candidate_schedule = _try_insert_service_schedule(
                instance_data,
                vessel_code,
                list(vessel_schedule),
                position_key,
                list(virtual_schedule),
            )
            if candidate_schedule is not None:
                schedule_payload = {"events": [event.to_dict() for event in candidate_schedule]}
                virtual_replacement_count += 1
                patterns.append(
                    Pattern(
                        pattern_id=(
                            "actual-replace-virtual:"
                            f"{vessel_code}:{virtual_code}:{position_key[0]}:{position_key[1]}:"
                            f"{position_key[2]}:{virtual_replacement_count}"
                        ),
                        vessel_code=vessel_code,
                        is_virtual=False,
                        schedule_payload=schedule_payload,
                        coverage_keys=_schedule_coverage_keys(candidate_schedule, coverage_positions),
                        cost=_estimate_pattern_cost(
                            instance_data,
                            all_selectable_position_payload,
                            vessel_code,
                            schedule_payload,
                            is_virtual=False,
                        ),
                    )
                )

            if _schedule_coverage_keys(vessel_schedule, fixed_positions):
                continue
            candidate_schedule = _try_assign_service_as_primary(
                instance_data,
                vessel_code,
                list(vessel_schedule),
                position_key,
                list(virtual_schedule),
            )
            if candidate_schedule is None:
                continue
            schedule_payload = {"events": [event.to_dict() for event in candidate_schedule]}
            virtual_replacement_count += 1
            patterns.append(
                Pattern(
                    pattern_id=(
                        "actual-primary-virtual:"
                        f"{vessel_code}:{virtual_code}:{position_key[0]}:{position_key[1]}:"
                        f"{position_key[2]}:{virtual_replacement_count}"
                    ),
                    vessel_code=vessel_code,
                    is_virtual=False,
                    schedule_payload=schedule_payload,
                    coverage_keys=_schedule_coverage_keys(candidate_schedule, coverage_positions),
                    cost=_estimate_pattern_cost(
                        instance_data,
                        all_selectable_position_payload,
                        vessel_code,
                        schedule_payload,
                        is_virtual=False,
                    ),
                )
            )

    actual_candidate_count = 0
    for vessel_code, schedule in initial_solution.vessel_schedules.items():
        for position_key, service_schedule in selectable_schedules.items():
            candidate_schedule = _try_insert_service_schedule(
                instance_data,
                vessel_code,
                list(schedule),
                position_key,
                service_schedule,
            )
            if candidate_schedule is None:
                continue
            schedule_payload = {"events": [event.to_dict() for event in candidate_schedule]}
            actual_candidate_count += 1
            patterns.append(
                Pattern(
                    pattern_id=(
                        "actual-candidate:"
                        f"{vessel_code}:{position_key[0]}:{position_key[1]}:{position_key[2]}:{actual_candidate_count}"
                    ),
                    vessel_code=vessel_code,
                    is_virtual=False,
                    schedule_payload=schedule_payload,
                    coverage_keys=_schedule_coverage_keys(candidate_schedule, coverage_positions),
                    cost=_estimate_pattern_cost(
                        instance_data,
                        all_selectable_position_payload,
                        vessel_code,
                        schedule_payload,
                        is_virtual=False,
                    ),
                )
            )

    v2_counts = Counter(_pattern_family(pattern) for pattern in patterns)
    deep_patterns = _deep_cascade_patterns(
        instance_data,
        initial_solution,
        coverage_positions,
        selectable_schedules,
        all_selectable_position_payload,
    )
    patterns.extend(deep_patterns)
    partial_patterns = _partial_virtual_rescue_patterns(
        instance_data,
        initial_solution,
        coverage_positions,
        all_selectable_position_payload,
    )
    patterns.extend(partial_patterns)
    raw_counts = Counter(_pattern_family(pattern) for pattern in patterns)
    raw_total = len(patterns)
    patterns, prune_stats = _prune_patterns(patterns)
    pruned_counts = Counter(_pattern_family(pattern) for pattern in patterns)
    max_depth = max((pattern.depth for pattern in patterns), default=0)
    print(
        "wsgoh/twostage_support/mip_core pattern generation\n"
        f"- v2 families: {_format_counts(v2_counts)}\n"
        f"- deep cascade generated: {len(deep_patterns)}\n"
        f"- partial virtual rescue generated: {len(partial_patterns)}\n"
        f"- raw patterns: {raw_total} ({_format_counts(raw_counts)})\n"
        f"- retained patterns: {len(patterns)} ({_format_counts(pruned_counts)})\n"
        f"- pruned schedule duplicates: {prune_stats.schedule_duplicate_pruned}\n"
        f"- pruned coverage duplicates: {prune_stats.coverage_duplicate_pruned}\n"
        f"- pruned by vessel cap: {prune_stats.vessel_cap_pruned}\n"
        f"- pruned by total cap: {prune_stats.total_cap_pruned}\n"
        f"- pruned orphan children: {prune_stats.orphan_pruned}\n"
        f"- max retained cascade depth: {max_depth}\n"
        f"- caps: depth={MAX_SWITCH_DEPTH}, top_k={TOP_K_SWITCH_PATTERNS_PER_FRAGMENT}, "
        f"initial_virtual_top_k={TOP_K_INITIAL_VIRTUAL_SWITCH_PATTERNS_PER_FRAGMENT}, "
        f"per_vessel={MAX_PATTERNS_PER_VESSEL}, total={MAX_TOTAL_PATTERNS}"
    )
    return patterns


def _position_key_from_schedule(schedule) -> PositionKey | None:
    for event in schedule:
        if isinstance(event, PortStay):
            return (event.lane_code, event.proforma_name, event.position_no)
    return None


def _vessel_is_compatible(instance_data: InstanceData, vessel_code: str, position_key: PositionKey) -> bool:
    lane_code, proforma_name, _ = position_key
    version = next(
        version
        for lane in instance_data.service_lanes
        if lane["lane_code"] == lane_code
        for version in lane["versions"]
        if version["proforma_name"] == proforma_name
    )
    vessel = next(vessel for vessel in instance_data.vessels if vessel["vessel_code"] == vessel_code)
    required_capacity_teu = version["required_capacity_teu"]
    required_reefer_plug = version["required_reefer_plug"]
    return (
        required_capacity_teu * 0.95 <= vessel["capacity_teu"] <= required_capacity_teu * 1.05
        and required_reefer_plug <= vessel["reefer_plug"]
    )


def _can_reposition(
    from_port_code: str,
    from_time: datetime,
    to_port_code: str,
    to_time: datetime,
) -> bool:
    if from_port_code == to_port_code:
        return from_time <= to_time
    sea_time = (to_time - from_time).total_seconds() / 3600
    if sea_time <= 0:
        return False
    return lookup_distance(from_port_code, to_port_code) / (sea_time + 1e-5) <= 20


def _try_insert_service_schedule(
    instance_data: InstanceData,
    vessel_code: str,
    vessel_schedule: list[Any],
    position_key: PositionKey,
    service_schedule: list[Any],
) -> list[Any] | None:
    if not _vessel_is_compatible(instance_data, vessel_code, position_key):
        return None

    service_start = event_start_time(service_schedule[0])
    service_end = event_end_time(service_schedule[-1])
    service_start_port = event_start_port_code(service_schedule[0])
    service_end_port = event_end_port_code(service_schedule[-1])

    overlapping = [
        (index, event)
        for index, event in enumerate(vessel_schedule)
        if event_end_time(event) >= service_start and event_start_time(event) <= service_end
    ]
    if not overlapping:
        return None
    if any(not isinstance(event, (Idle, OutLaneSail)) for _, event in overlapping):
        return None

    first_index = overlapping[0][0]
    last_index = overlapping[-1][0]
    prefix = vessel_schedule[:first_index]
    suffix = vessel_schedule[last_index + 1 :]

    if not prefix:
        return None

    depart_event = prefix[-1]
    depart_time = event_end_time(depart_event)
    depart_port = event_end_port_code(depart_event)
    if not _can_reposition(depart_port, depart_time, service_start_port, service_start):
        return None

    candidate = list(prefix)
    candidate.append(
        initial_solver.out_sail_or_idle(
            from_port_code=depart_port,
            sea_sail_start=depart_time,
            sea_sail_end=service_start,
            to_port_code=service_start_port,
        )
    )
    candidate.extend(service_schedule)

    if suffix:
        return_time = event_start_time(suffix[0])
        return_port = event_start_port_code(suffix[0])
        if not _can_reposition(service_end_port, service_end, return_port, return_time):
            return None
        candidate.append(
            initial_solver.out_sail_or_idle(
                from_port_code=service_end_port,
                sea_sail_start=service_end,
                sea_sail_end=return_time,
                to_port_code=return_port,
            )
        )
        candidate.extend(suffix)
    elif service_end < instance_data.planning_horizon["end"]:
        candidate.append(
            Idle(
                port_code=service_end_port,
                idle_start=service_end,
                idle_end=instance_data.planning_horizon["end"],
            )
        )

    if _schedule_signature(candidate) == _schedule_signature(vessel_schedule):
        return None
    if not _is_vessel_schedule_consistent(candidate):
        return None
    return candidate


def _try_assign_service_as_primary(
    instance_data: InstanceData,
    vessel_code: str,
    vessel_schedule: list[Any],
    position_key: PositionKey,
    service_schedule: list[Any],
) -> list[Any] | None:
    if not _vessel_is_compatible(instance_data, vessel_code, position_key):
        return None

    protected_events = [
        event for event in vessel_schedule if isinstance(event, (Delivery, DryDock, Redelivery))
    ]
    if not protected_events:
        return None

    service_start = event_start_time(service_schedule[0])
    service_end = event_end_time(service_schedule[-1])
    for event in protected_events:
        if isinstance(event, Delivery) and service_start < event.delivery_time:
            return None
        if isinstance(event, Redelivery) and service_end > event.redelivery_time:
            return None
        if isinstance(event, DryDock):
            overlaps_drydock = event.dock_in < service_end and service_start < event.dock_out
            if overlaps_drydock:
                return None

    blocks = [[event] for event in protected_events] + [list(service_schedule)]
    blocks.sort(key=lambda block: event_start_time(block[0]))

    candidate: list[Any] = []
    for block in blocks:
        block_start = event_start_time(block[0])
        block_start_port = event_start_port_code(block[0])
        if candidate:
            depart_time = event_end_time(candidate[-1])
            depart_port = event_end_port_code(candidate[-1])
            if depart_time > block_start:
                return None
            if not _can_reposition(depart_port, depart_time, block_start_port, block_start):
                return None
            candidate.append(
                initial_solver.out_sail_or_idle(
                    from_port_code=depart_port,
                    sea_sail_start=depart_time,
                    sea_sail_end=block_start,
                    to_port_code=block_start_port,
                )
            )
        candidate.extend(block)

    if not isinstance(candidate[-1], Redelivery) and event_end_time(candidate[-1]) < instance_data.planning_horizon["end"]:
        candidate.append(
            Idle(
                port_code=event_end_port_code(candidate[-1]),
                idle_start=event_end_time(candidate[-1]),
                idle_end=instance_data.planning_horizon["end"],
            )
        )

    if _schedule_signature(candidate) == _schedule_signature(vessel_schedule):
        return None
    if not _is_vessel_schedule_consistent(candidate):
        return None
    return candidate


def _has_portstay(schedule: list[Any]) -> bool:
    return any(isinstance(event, PortStay) for event in schedule)


def _inlane_blocks(schedule: list[Any]) -> list[tuple[int, int, list[Any]]]:
    blocks: list[tuple[int, int, list[Any]]] = []
    start_index: int | None = None
    for index, event in enumerate(schedule):
        if isinstance(event, InLaneEvent):
            if start_index is None:
                start_index = index
            continue
        if start_index is not None:
            blocks.append((start_index, index - 1, schedule[start_index:index]))
            start_index = None
    if start_index is not None:
        blocks.append((start_index, len(schedule) - 1, schedule[start_index:]))
    return blocks


def _enumerate_inlane_splits(
    schedule: list[Any],
    target_time: datetime,
    target_port_code: str,
) -> list[SplitCandidate]:
    if not schedule:
        return []

    ts_work = timedelta(hours=initial_solver.TS_WORK_HOUR)
    ts_slack = timedelta(hours=initial_solver.TS_SLACK_HOUR)
    splits: list[SplitCandidate] = []

    last_event = schedule[-1]
    last_port = event_end_port_code(last_event)
    last_time = event_end_time(last_event)
    direct_slack_hours = (target_time - last_time).total_seconds() / 3600
    if _can_reposition(last_port, last_time, target_port_code, target_time):
        splits.append(
            SplitCandidate(
                prefix=_clone_schedule(schedule),
                suffix=[],
                mode="complete_service",
                slack_hours=direct_slack_hours,
            )
        )

    for port_stay_index, port_stay in enumerate(schedule):
        if not isinstance(port_stay, PortStay):
            continue
        if port_stay.port_code in ("EGSUZ", "EGSCA", "PAPCA"):
            continue

        if port_stay_index + 1 < len(schedule):
            next_event = schedule[port_stay_index + 1]
            if isinstance(next_event, InLaneSail):
                unload_start = port_stay.pilot_out_end
                phase_out_time = unload_start + ts_work
                if _can_reposition(port_stay.port_code, phase_out_time, target_port_code, target_time):
                    phase_in_time = phase_out_time + ts_slack
                    load_end = phase_in_time + ts_work
                    sea_time_inlane = (next_event.sea_sail_end - load_end).total_seconds() / 3600
                    distance_inlane = lookup_distance(next_event.from_port_code, next_event.to_port_code)
                    if sea_time_inlane > 0 and distance_inlane / (sea_time_inlane + 1e-5) <= 20:
                        prefix = _clone_schedule(schedule[: port_stay_index + 1])
                        suffix_tail = _clone_schedule(schedule[port_stay_index + 1 :])
                        if suffix_tail and isinstance(suffix_tail[0], InLaneSail):
                            suffix_tail[0].sea_sail_start = load_end
                        prefix.extend(
                            [
                                TransshipmentUnload(
                                    lane_code=port_stay.lane_code,
                                    proforma_name=port_stay.proforma_name,
                                    position_no=port_stay.position_no,
                                    ts_port_code=port_stay.port_code,
                                    ts_port_seq=port_stay.port_seq,
                                    unload_start=unload_start,
                                    unload_end=phase_out_time,
                                ),
                                PhaseOut(
                                    lane_code=port_stay.lane_code,
                                    proforma_name=port_stay.proforma_name,
                                    position_no=port_stay.position_no,
                                    phase_out_port_code=port_stay.port_code,
                                    phase_out_port_seq=port_stay.port_seq,
                                    phase_out_time=phase_out_time,
                                ),
                            ]
                        )
                        suffix = [
                            PhaseIn(
                                lane_code=port_stay.lane_code,
                                proforma_name=port_stay.proforma_name,
                                position_no=port_stay.position_no,
                                phase_in_port_code=port_stay.port_code,
                                phase_in_port_seq=port_stay.port_seq,
                                phase_in_time=phase_in_time,
                            ),
                            TransshipmentLoad(
                                lane_code=port_stay.lane_code,
                                proforma_name=port_stay.proforma_name,
                                position_no=port_stay.position_no,
                                ts_port_code=port_stay.port_code,
                                ts_port_seq=port_stay.port_seq,
                                load_start=phase_in_time,
                                load_end=load_end,
                            ),
                        ] + suffix_tail
                        splits.append(
                            SplitCandidate(
                                prefix=prefix,
                                suffix=suffix,
                                mode=f"after_portstay:{port_stay.port_code}:{port_stay.port_seq}",
                                slack_hours=(target_time - phase_out_time).total_seconds() / 3600,
                            )
                        )

        if port_stay_index == 0:
            continue
        prev_event = schedule[port_stay_index - 1]
        if not isinstance(prev_event, InLaneSail):
            continue

        unload_start = port_stay.pilot_in_start - ts_work - ts_slack - ts_work
        phase_out_time = unload_start + ts_work
        sea_time_inlane = (unload_start - prev_event.sea_sail_start).total_seconds() / 3600
        distance_inlane = lookup_distance(prev_event.from_port_code, prev_event.to_port_code)
        if sea_time_inlane <= 0 or distance_inlane / (sea_time_inlane + 1e-5) > 20:
            continue
        if not _can_reposition(prev_event.to_port_code, phase_out_time, target_port_code, target_time):
            continue

        phase_in_time = phase_out_time + ts_slack
        prefix = _clone_schedule(schedule[:port_stay_index])
        if not prefix or not isinstance(prefix[-1], InLaneSail):
            continue
        prefix[-1].sea_sail_end = unload_start
        prefix.extend(
            [
                TransshipmentUnload(
                    lane_code=port_stay.lane_code,
                    proforma_name=port_stay.proforma_name,
                    position_no=port_stay.position_no,
                    ts_port_code=prev_event.to_port_code,
                    ts_port_seq=prev_event.to_port_seq,
                    unload_start=unload_start,
                    unload_end=phase_out_time,
                ),
                PhaseOut(
                    lane_code=port_stay.lane_code,
                    proforma_name=port_stay.proforma_name,
                    position_no=port_stay.position_no,
                    phase_out_port_code=prev_event.to_port_code,
                    phase_out_port_seq=prev_event.to_port_seq,
                    phase_out_time=phase_out_time,
                ),
            ]
        )
        suffix = [
            PhaseIn(
                lane_code=port_stay.lane_code,
                proforma_name=port_stay.proforma_name,
                position_no=port_stay.position_no,
                phase_in_port_code=prev_event.to_port_code,
                phase_in_port_seq=prev_event.to_port_seq,
                phase_in_time=phase_in_time,
            ),
            TransshipmentLoad(
                lane_code=port_stay.lane_code,
                proforma_name=port_stay.proforma_name,
                position_no=port_stay.position_no,
                ts_port_code=prev_event.to_port_code,
                ts_port_seq=prev_event.to_port_seq,
                load_start=phase_in_time,
                load_end=phase_in_time + ts_work,
            ),
        ] + _clone_schedule(schedule[port_stay_index:])
        splits.append(
            SplitCandidate(
                prefix=prefix,
                suffix=suffix,
                mode=f"before_portstay:{port_stay.port_code}:{port_stay.port_seq}",
                slack_hours=(target_time - phase_out_time).total_seconds() / 3600,
            )
        )

    valid_splits: list[SplitCandidate] = []
    seen_signatures: set[tuple[Any, ...]] = set()
    for split in splits:
        if not split.prefix:
            continue
        if not _is_vessel_schedule_consistent(split.prefix):
            continue
        if split.suffix and not _is_vessel_schedule_consistent(split.suffix):
            continue
        signature = (_schedule_signature(split.prefix), _schedule_signature(split.suffix))
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)
        valid_splits.append(split)

    valid_splits.sort(key=lambda split: (len(split.suffix), -split.slack_hours, split.mode))
    return valid_splits


def _enumerate_virtual_handover_splits(schedule: list[Any]) -> list[SplitCandidate]:
    if not schedule:
        return []

    ts_work = timedelta(hours=initial_solver.TS_WORK_HOUR)
    ts_slack = timedelta(hours=initial_solver.TS_SLACK_HOUR)
    splits: list[SplitCandidate] = []

    for port_stay_index, port_stay in enumerate(schedule):
        if not isinstance(port_stay, PortStay):
            continue
        if port_stay.port_code in ("EGSUZ", "EGSCA", "PAPCA"):
            continue

        if port_stay_index + 1 < len(schedule):
            next_event = schedule[port_stay_index + 1]
            if isinstance(next_event, InLaneSail):
                unload_start = port_stay.pilot_out_end
                phase_out_time = unload_start + ts_work
                phase_in_time = phase_out_time + ts_slack
                load_end = phase_in_time + ts_work
                sea_time_inlane = (next_event.sea_sail_end - load_end).total_seconds() / 3600
                distance_inlane = lookup_distance(next_event.from_port_code, next_event.to_port_code)
                if sea_time_inlane > 0 and distance_inlane / (sea_time_inlane + 1e-5) <= 20:
                    prefix = _clone_schedule(schedule[: port_stay_index + 1])
                    suffix_tail = _clone_schedule(schedule[port_stay_index + 1 :])
                    if suffix_tail and isinstance(suffix_tail[0], InLaneSail):
                        suffix_tail[0].sea_sail_start = load_end
                    prefix.extend(
                        [
                            TransshipmentUnload(
                                lane_code=port_stay.lane_code,
                                proforma_name=port_stay.proforma_name,
                                position_no=port_stay.position_no,
                                ts_port_code=port_stay.port_code,
                                ts_port_seq=port_stay.port_seq,
                                unload_start=unload_start,
                                unload_end=phase_out_time,
                            ),
                            PhaseOut(
                                lane_code=port_stay.lane_code,
                                proforma_name=port_stay.proforma_name,
                                position_no=port_stay.position_no,
                                phase_out_port_code=port_stay.port_code,
                                phase_out_port_seq=port_stay.port_seq,
                                phase_out_time=phase_out_time,
                            ),
                        ]
                    )
                    suffix = [
                        PhaseIn(
                            lane_code=port_stay.lane_code,
                            proforma_name=port_stay.proforma_name,
                            position_no=port_stay.position_no,
                            phase_in_port_code=port_stay.port_code,
                            phase_in_port_seq=port_stay.port_seq,
                            phase_in_time=phase_in_time,
                        ),
                        TransshipmentLoad(
                            lane_code=port_stay.lane_code,
                            proforma_name=port_stay.proforma_name,
                            position_no=port_stay.position_no,
                            ts_port_code=port_stay.port_code,
                            ts_port_seq=port_stay.port_seq,
                            load_start=phase_in_time,
                            load_end=load_end,
                        ),
                    ] + suffix_tail
                    splits.append(
                        SplitCandidate(
                            prefix=prefix,
                            suffix=suffix,
                            mode=f"virtual_after_portstay:{port_stay.port_code}:{port_stay.port_seq}",
                            slack_hours=0.0,
                        )
                    )

        if port_stay_index == 0:
            continue
        prev_event = schedule[port_stay_index - 1]
        if not isinstance(prev_event, InLaneSail):
            continue

        unload_start = port_stay.pilot_in_start - ts_work - ts_slack - ts_work
        phase_out_time = unload_start + ts_work
        sea_time_inlane = (unload_start - prev_event.sea_sail_start).total_seconds() / 3600
        distance_inlane = lookup_distance(prev_event.from_port_code, prev_event.to_port_code)
        if sea_time_inlane <= 0 or distance_inlane / (sea_time_inlane + 1e-5) > 20:
            continue

        phase_in_time = phase_out_time + ts_slack
        prefix = _clone_schedule(schedule[:port_stay_index])
        if not prefix or not isinstance(prefix[-1], InLaneSail):
            continue
        prefix[-1].sea_sail_end = unload_start
        prefix.extend(
            [
                TransshipmentUnload(
                    lane_code=port_stay.lane_code,
                    proforma_name=port_stay.proforma_name,
                    position_no=port_stay.position_no,
                    ts_port_code=prev_event.to_port_code,
                    ts_port_seq=prev_event.to_port_seq,
                    unload_start=unload_start,
                    unload_end=phase_out_time,
                ),
                PhaseOut(
                    lane_code=port_stay.lane_code,
                    proforma_name=port_stay.proforma_name,
                    position_no=port_stay.position_no,
                    phase_out_port_code=prev_event.to_port_code,
                    phase_out_port_seq=prev_event.to_port_seq,
                    phase_out_time=phase_out_time,
                ),
            ]
        )
        suffix = [
            PhaseIn(
                lane_code=port_stay.lane_code,
                proforma_name=port_stay.proforma_name,
                position_no=port_stay.position_no,
                phase_in_port_code=prev_event.to_port_code,
                phase_in_port_seq=prev_event.to_port_seq,
                phase_in_time=phase_in_time,
            ),
            TransshipmentLoad(
                lane_code=port_stay.lane_code,
                proforma_name=port_stay.proforma_name,
                position_no=port_stay.position_no,
                ts_port_code=prev_event.to_port_code,
                ts_port_seq=prev_event.to_port_seq,
                load_start=phase_in_time,
                load_end=phase_in_time + ts_work,
            ),
        ] + _clone_schedule(schedule[port_stay_index:])
        splits.append(
            SplitCandidate(
                prefix=prefix,
                suffix=suffix,
                mode=f"virtual_before_portstay:{port_stay.port_code}:{port_stay.port_seq}",
                slack_hours=0.0,
            )
        )

    valid_splits: list[SplitCandidate] = []
    seen_signatures: set[tuple[Any, ...]] = set()
    for split in splits:
        if not _has_portstay(split.prefix) or not _has_portstay(split.suffix):
            continue
        if not _is_vessel_schedule_consistent(split.prefix):
            continue
        if not _is_vessel_schedule_consistent(split.suffix):
            continue
        signature = (_schedule_signature(split.prefix), _schedule_signature(split.suffix))
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)
        valid_splits.append(split)

    valid_splits.sort(
        key=lambda split: (
            event_start_time(split.suffix[0]),
            -len(_schedule_coverage_keys(split.suffix, {_position_key_from_schedule(split.suffix)}))
            if _position_key_from_schedule(split.suffix) is not None
            else 0,
            split.mode,
        )
    )
    return valid_splits


def _switch_candidates_for_fragment(
    instance_data: InstanceData,
    vessel_code: str,
    vessel_schedule: list[Any],
    fragment: ServiceFragment,
    coverage_positions: set[PositionKey],
) -> CandidateSearchResult:
    result = CandidateSearchResult()
    if not fragment.schedule:
        result.rejections["empty_fragment_schedule"] += 1
        return result
    if not _vessel_is_compatible(instance_data, vessel_code, fragment.position_key):
        result.rejections["capacity_mismatch"] += 1
        return result

    target_start = event_start_time(fragment.schedule[0])
    target_end = event_end_time(fragment.schedule[-1])
    target_start_port = event_start_port_code(fragment.schedule[0])
    target_end_port = event_end_port_code(fragment.schedule[-1])
    target_coverage_count = len(_schedule_coverage_keys(fragment.schedule, {fragment.position_key}))
    inlane_blocks = _inlane_blocks(vessel_schedule)
    if not inlane_blocks:
        result.rejections["no_inlane_blocks"] += 1
        return result

    for first_index, last_index, block in inlane_blocks:
        block_positions = {
            (event.lane_code, event.proforma_name, event.position_no)
            for event in block
            if isinstance(event, PortStay)
        }
        if fragment.position_key in block_positions:
            result.rejections["same_position"] += 1
            continue
        if event_start_time(block[0]) > target_start:
            result.rejections["target_too_early"] += 1
            continue

        split_candidates = _enumerate_inlane_splits(block, target_start, target_start_port)
        if not split_candidates:
            result.rejections["no_feasible_split"] += 1
            continue

        for split in split_candidates:
            prefix = split.prefix
            dropped_suffix = split.suffix
            depart_event = prefix[-1]
            depart_time = event_end_time(depart_event)
            depart_port = event_end_port_code(depart_event)
            if depart_time > target_start:
                result.rejections["target_too_early"] += 1
                continue
            if not _can_reposition(depart_port, depart_time, target_start_port, target_start):
                result.rejections["reposition_infeasible"] += 1
                continue

            before_block = _clone_schedule(vessel_schedule[:first_index])
            after_block = _clone_schedule(vessel_schedule[last_index + 1 :])
            target_schedule = _clone_schedule(fragment.schedule)

            candidate = before_block + prefix
            candidate.append(
                initial_solver.out_sail_or_idle(
                    from_port_code=depart_port,
                    sea_sail_start=depart_time,
                    sea_sail_end=target_start,
                    to_port_code=target_start_port,
                )
            )
            candidate.extend(target_schedule)

            if after_block:
                return_time = event_start_time(after_block[0])
                return_port = event_start_port_code(after_block[0])
                if target_end > return_time:
                    result.rejections["reconnect_infeasible"] += 1
                    continue
                if not _can_reposition(target_end_port, target_end, return_port, return_time):
                    result.rejections["reconnect_infeasible"] += 1
                    continue
                candidate.append(
                    initial_solver.out_sail_or_idle(
                        from_port_code=target_end_port,
                        sea_sail_start=target_end,
                        sea_sail_end=return_time,
                        to_port_code=return_port,
                    )
                )
                candidate.extend(after_block)
            elif target_end < instance_data.planning_horizon["end"]:
                candidate.append(
                    Idle(
                        port_code=target_end_port,
                        idle_start=target_end,
                        idle_end=instance_data.planning_horizon["end"],
                    )
                )

            if _schedule_signature(candidate) == _schedule_signature(vessel_schedule):
                result.rejections["duplicate_schedule"] += 1
                continue
            if not _is_vessel_schedule_consistent(candidate):
                result.rejections["schedule_inconsistency"] += 1
                continue

            dropped_coverage_count = len(_schedule_coverage_keys(dropped_suffix, coverage_positions))
            dropped_position_key = _position_key_from_schedule(dropped_suffix)
            score = (
                fragment.priority
                + target_coverage_count * 10.0
                - dropped_coverage_count
                + min(max(split.slack_hours, 0.0), 240.0) / 240.0
            )
            result.candidates.append(
                SwitchCandidate(
                    vessel_code=vessel_code,
                    schedule=candidate,
                    dropped_suffix=_clone_schedule(dropped_suffix),
                    score=score,
                    split_mode=split.mode,
                    dropped_position_key=dropped_position_key,
                )
            )

    return result


def _fragment_priority(source: str, schedule: list[Any], position_key: PositionKey) -> float:
    coverage_count = len(_schedule_coverage_keys(schedule, {position_key}))
    source_bonus = {
        "initial_virtual": 10000.0,
        "initial_virtual_suffix": 9000.0,
        "dropped_suffix": 5000.0,
        "selectable": 1000.0,
    }.get(source, 0.0)
    return source_bonus + coverage_count


def _make_fragment(
    fragment_id: str,
    position_key: PositionKey,
    schedule: list[Any],
    source: str,
    depth: int,
    requires_pattern_id: str | None = None,
) -> ServiceFragment:
    return ServiceFragment(
        fragment_id=fragment_id,
        position_key=position_key,
        schedule=_clone_schedule(schedule),
        source=source,
        depth=depth,
        priority=_fragment_priority(source, schedule, position_key),
        requires_pattern_id=requires_pattern_id,
    )


def _partial_virtual_rescue_patterns(
    instance_data: InstanceData,
    initial_solution: CascadingSolution,
    coverage_positions: set[PositionKey],
    declared_positions_payload: list[dict[str, Any]],
) -> list[Pattern]:
    patterns: list[Pattern] = []
    rescue_diagnostics: dict[str, tuple[PositionKey, int, int, int, Counter[str]]] = {}
    actual_count = 0
    suffix_count = 0

    for virtual_code, virtual_schedule in initial_solution.virtual_vessel_schedules.items():
        position_key = _position_key_from_schedule(virtual_schedule)
        if position_key is None:
            continue

        handover_splits = _enumerate_virtual_handover_splits(list(virtual_schedule))
        accepted_total = 0
        retained_total = 0
        rejections: Counter[str] = Counter()

        for split_index, split in enumerate(handover_splits, start=1):
            prefix_pattern_id = f"virtual-partial-prefix:{virtual_code}:{split_index}:{_safe_name(split.mode)}"
            suffix_fragment = _make_fragment(
                fragment_id=f"initial-virtual-suffix:{virtual_code}:{split_index}:{_safe_name(split.mode)}",
                position_key=position_key,
                schedule=split.suffix,
                source="initial_virtual_suffix",
                depth=1,
                requires_pattern_id=prefix_pattern_id,
            )

            switch_candidates: list[SwitchCandidate] = []
            for vessel_code, vessel_schedule in initial_solution.vessel_schedules.items():
                search_result = _switch_candidates_for_fragment(
                    instance_data,
                    vessel_code,
                    list(vessel_schedule),
                    suffix_fragment,
                    coverage_positions,
                )
                switch_candidates.extend(search_result.candidates)
                rejections.update(search_result.rejections)
            switch_candidates.sort(key=lambda candidate: candidate.score, reverse=True)
            retained_candidates = switch_candidates[:TOP_K_PARTIAL_VIRTUAL_TAKEOVER_PATTERNS_PER_SPLIT]
            accepted_total += len(switch_candidates)
            retained_total += len(retained_candidates)
            if not retained_candidates:
                continue

            prefix_vessel_code = f"MIPVIRTUAL_PARTIAL_PREFIX_{_safe_name(virtual_code)}_{split_index}"
            prefix_payload = {"events": [event.to_dict() for event in split.prefix]}
            patterns.append(
                Pattern(
                    pattern_id=prefix_pattern_id,
                    vessel_code=prefix_vessel_code,
                    is_virtual=True,
                    schedule_payload=prefix_payload,
                    coverage_keys=_schedule_coverage_keys(split.prefix, coverage_positions),
                    cost=_estimate_pattern_cost(
                        instance_data,
                        declared_positions_payload,
                        prefix_vessel_code,
                        prefix_payload,
                        is_virtual=True,
                    ),
                    depth=1,
                    priority=suffix_fragment.priority,
                    source_fragment_id=f"initial-virtual-prefix:{virtual_code}:{split_index}",
                    target_position_key=position_key,
                    split_mode=split.mode,
                )
            )

            for candidate in retained_candidates:
                actual_count += 1
                schedule_payload = {"events": [event.to_dict() for event in candidate.schedule]}
                pattern_id = (
                    "actual-partial-virtual:"
                    f"{candidate.vessel_code}:{virtual_code}:{position_key[0]}:{position_key[1]}:"
                    f"{position_key[2]}:{split_index}:{actual_count}"
                )
                patterns.append(
                    Pattern(
                        pattern_id=pattern_id,
                        vessel_code=candidate.vessel_code,
                        is_virtual=False,
                        schedule_payload=schedule_payload,
                        coverage_keys=_schedule_coverage_keys(candidate.schedule, coverage_positions),
                        cost=_estimate_pattern_cost(
                            instance_data,
                            declared_positions_payload,
                            candidate.vessel_code,
                            schedule_payload,
                            is_virtual=False,
                        ),
                        requires_pattern_ids=frozenset({prefix_pattern_id}),
                        depth=1,
                        priority=candidate.score,
                        source_fragment_id=suffix_fragment.fragment_id,
                        target_position_key=position_key,
                        dropped_position_key=candidate.dropped_position_key,
                        split_mode=candidate.split_mode,
                    )
                )

                dropped_position_key = _position_key_from_schedule(candidate.dropped_suffix)
                dropped_coverage = _schedule_coverage_keys(candidate.dropped_suffix, coverage_positions)
                if dropped_position_key is None or not dropped_coverage:
                    continue

                suffix_count += 1
                suffix_payload = {"events": [event.to_dict() for event in candidate.dropped_suffix]}
                suffix_vessel_code = (
                    "MIPVIRTUAL_PARTIAL_DROPPED_"
                    f"{_safe_name(candidate.vessel_code)}_{suffix_count}"
                )
                patterns.append(
                    Pattern(
                        pattern_id=(
                            "virtual-suffix-fallback-dpartial:"
                            f"{candidate.vessel_code}:{_safe_name(virtual_code)}:{split_index}:{suffix_count}"
                        ),
                        vessel_code=suffix_vessel_code,
                        is_virtual=True,
                        schedule_payload=suffix_payload,
                        coverage_keys=dropped_coverage,
                        cost=_estimate_pattern_cost(
                            instance_data,
                            declared_positions_payload,
                            suffix_vessel_code,
                            suffix_payload,
                            is_virtual=True,
                        ),
                        requires_pattern_ids=frozenset({pattern_id}),
                        depth=1,
                        priority=_fragment_priority("dropped_suffix", candidate.dropped_suffix, dropped_position_key),
                        source_fragment_id=(
                            f"partial-dropped-suffix:{candidate.vessel_code}:"
                            f"{dropped_position_key[0]}:{dropped_position_key[1]}:{dropped_position_key[2]}"
                        ),
                        target_position_key=dropped_position_key,
                    )
                )

        rescue_diagnostics[virtual_code] = (
            position_key,
            len(handover_splits),
            accepted_total,
            retained_total,
            rejections,
        )

    if rescue_diagnostics:
        print("wsgoh/twostage_support/mip_core partial virtual rescue diagnostics")
        for virtual_code, (position_key, split_count, accepted_count, retained_count, rejections) in sorted(
            rescue_diagnostics.items()
        ):
            print(
                f"- {virtual_code} target={_format_position_key(position_key)} "
                f"splits={split_count} accepted={accepted_count} retained={retained_count} "
                f"rejections={_format_counts(rejections)}"
            )

    return patterns


def _deep_cascade_patterns(
    instance_data: InstanceData,
    initial_solution: CascadingSolution,
    coverage_positions: set[PositionKey],
    selectable_schedules: dict[PositionKey, list[Any]],
    declared_positions_payload: list[dict[str, Any]],
) -> list[Pattern]:
    patterns: list[Pattern] = []
    initial_fragments: list[ServiceFragment] = []

    for virtual_code, virtual_schedule in initial_solution.virtual_vessel_schedules.items():
        position_key = _position_key_from_schedule(virtual_schedule)
        if position_key is None:
            continue
        initial_fragments.append(
            _make_fragment(
                fragment_id=f"initial-virtual:{virtual_code}",
                position_key=position_key,
                schedule=list(virtual_schedule),
                source="initial_virtual",
                depth=0,
            )
        )

    for position_key, schedule in selectable_schedules.items():
        initial_fragments.append(
            _make_fragment(
                fragment_id=f"selectable:{position_key[0]}:{position_key[1]}:{position_key[2]}",
                position_key=position_key,
                schedule=schedule,
                source="selectable",
                depth=0,
            )
        )

    queue = sorted(initial_fragments, key=lambda fragment: fragment.priority, reverse=True)
    seen_fragment_signatures = {
        (fragment.position_key, _schedule_signature(fragment.schedule), fragment.requires_pattern_id)
        for fragment in queue
    }
    initial_virtual_diagnostics: dict[str, tuple[PositionKey, int, int, Counter[str]]] = {}
    switch_count = 0
    suffix_count = 0

    for depth in range(1, MAX_SWITCH_DEPTH + 1):
        next_queue: list[ServiceFragment] = []
        for fragment in queue:
            switch_candidates: list[SwitchCandidate] = []
            fragment_rejections: Counter[str] = Counter()
            for vessel_code, vessel_schedule in initial_solution.vessel_schedules.items():
                search_result = _switch_candidates_for_fragment(
                    instance_data,
                    vessel_code,
                    list(vessel_schedule),
                    fragment,
                    coverage_positions,
                )
                switch_candidates.extend(search_result.candidates)
                fragment_rejections.update(search_result.rejections)
            switch_candidates.sort(key=lambda candidate: candidate.score, reverse=True)
            top_k = (
                TOP_K_INITIAL_VIRTUAL_SWITCH_PATTERNS_PER_FRAGMENT
                if fragment.source == "initial_virtual"
                else TOP_K_SWITCH_PATTERNS_PER_FRAGMENT
            )
            retained_candidates = switch_candidates[:top_k]
            if fragment.source == "initial_virtual":
                initial_virtual_diagnostics[fragment.fragment_id] = (
                    fragment.position_key,
                    len(switch_candidates),
                    len(retained_candidates),
                    fragment_rejections,
                )

            for candidate in retained_candidates:
                switch_count += 1
                schedule_payload = {"events": [event.to_dict() for event in candidate.schedule]}
                safe_fragment_id = _safe_name(fragment.fragment_id)
                pattern_id = f"actual-switch-d{depth}:{candidate.vessel_code}:{safe_fragment_id}:{switch_count}"
                requires = (
                    frozenset({fragment.requires_pattern_id})
                    if fragment.requires_pattern_id is not None
                    else frozenset()
                )
                pattern = Pattern(
                    pattern_id=pattern_id,
                    vessel_code=candidate.vessel_code,
                    is_virtual=False,
                    schedule_payload=schedule_payload,
                    coverage_keys=_schedule_coverage_keys(candidate.schedule, coverage_positions),
                    cost=_estimate_pattern_cost(
                        instance_data,
                        declared_positions_payload,
                        candidate.vessel_code,
                        schedule_payload,
                        is_virtual=False,
                    ),
                    requires_pattern_ids=requires,
                    depth=depth,
                    priority=candidate.score,
                    source_fragment_id=fragment.fragment_id,
                    target_position_key=fragment.position_key,
                    dropped_position_key=candidate.dropped_position_key,
                    split_mode=candidate.split_mode,
                )
                patterns.append(pattern)

                dropped_position_key = _position_key_from_schedule(candidate.dropped_suffix)
                if depth >= MAX_SWITCH_DEPTH or dropped_position_key is None:
                    continue
                dropped_coverage = _schedule_coverage_keys(candidate.dropped_suffix, coverage_positions)
                if not dropped_coverage:
                    continue

                suffix_count += 1
                suffix_fragment = _make_fragment(
                    fragment_id=(
                        f"suffix-d{depth}:{candidate.vessel_code}:"
                        f"{dropped_position_key[0]}:{dropped_position_key[1]}:{dropped_position_key[2]}:{suffix_count}"
                    ),
                    position_key=dropped_position_key,
                    schedule=candidate.dropped_suffix,
                    source="dropped_suffix",
                    depth=depth,
                    requires_pattern_id=pattern_id,
                )
                fragment_signature = (
                    suffix_fragment.position_key,
                    _schedule_signature(suffix_fragment.schedule),
                    suffix_fragment.requires_pattern_id,
                )
                if fragment_signature not in seen_fragment_signatures:
                    seen_fragment_signatures.add(fragment_signature)
                    next_queue.append(suffix_fragment)

                virtual_code = (
                    "MIPVIRTUAL_SUFFIX_"
                    f"{_safe_name(candidate.vessel_code)}_{_safe_name(str(suffix_count))}"
                )
                suffix_payload = {"events": [event.to_dict() for event in candidate.dropped_suffix]}
                patterns.append(
                    Pattern(
                        pattern_id=(
                            "virtual-suffix-fallback-d"
                            f"{depth}:{candidate.vessel_code}:{_safe_name(str(suffix_count))}"
                        ),
                        vessel_code=virtual_code,
                        is_virtual=True,
                        schedule_payload=suffix_payload,
                        coverage_keys=dropped_coverage,
                        cost=_estimate_pattern_cost(
                            instance_data,
                            declared_positions_payload,
                            virtual_code,
                            suffix_payload,
                            is_virtual=True,
                        ),
                        requires_pattern_ids=frozenset({pattern_id}),
                        depth=depth,
                        priority=suffix_fragment.priority,
                        source_fragment_id=suffix_fragment.fragment_id,
                        target_position_key=dropped_position_key,
                    )
                )

        queue = sorted(next_queue, key=lambda fragment: fragment.priority, reverse=True)
        if not queue:
            break

    if initial_virtual_diagnostics:
        print("wsgoh/twostage_support/mip_core initial virtual diagnostics")
        for fragment_id, (position_key, accepted_count, retained_count, rejections) in sorted(
            initial_virtual_diagnostics.items()
        ):
            print(
                f"- {fragment_id} target={_format_position_key(position_key)} "
                f"accepted={accepted_count} retained={retained_count} "
                f"rejections={_format_counts(rejections)}"
            )

    return patterns


def _prune_patterns(patterns: list[Pattern]) -> tuple[list[Pattern], PruneStats]:
    stats = PruneStats(input_count=len(patterns))
    by_signature: dict[tuple[Any, ...], Pattern] = {}
    for pattern in patterns:
        signature = (
            pattern.vessel_code,
            pattern.is_virtual,
            pattern.requires_pattern_ids,
            _schedule_payload_signature(pattern.schedule_payload),
        )
        incumbent = by_signature.get(signature)
        if incumbent is None or pattern.cost < incumbent.cost:
            by_signature[signature] = pattern
    stats.schedule_duplicate_pruned = len(patterns) - len(by_signature)

    by_coverage: dict[tuple[Any, ...], Pattern] = {}
    for pattern in by_signature.values():
        signature = (
            pattern.vessel_code,
            pattern.is_virtual,
            pattern.requires_pattern_ids,
            pattern.coverage_keys,
            _pattern_family(pattern),
        )
        incumbent = by_coverage.get(signature)
        if incumbent is None or pattern.cost < incumbent.cost:
            by_coverage[signature] = pattern
    stats.coverage_duplicate_pruned = len(by_signature) - len(by_coverage)

    retained = list(by_coverage.values())
    by_vessel: dict[str, list[Pattern]] = {}
    virtual_patterns: list[Pattern] = []
    for pattern in retained:
        if pattern.is_virtual:
            virtual_patterns.append(pattern)
        else:
            by_vessel.setdefault(pattern.vessel_code, []).append(pattern)

    pruned: list[Pattern] = virtual_patterns
    for vessel_patterns in by_vessel.values():
        baseline = [pattern for pattern in vessel_patterns if pattern.pattern_id.startswith("actual:")]
        candidates = [pattern for pattern in vessel_patterns if not pattern.pattern_id.startswith("actual:")]
        candidates.sort(key=lambda pattern: (-pattern.priority, pattern.cost, pattern.pattern_id))
        pruned.extend(baseline + candidates[: max(0, MAX_PATTERNS_PER_VESSEL - len(baseline))])
    stats.vessel_cap_pruned = len(retained) - len(pruned)

    pruned, orphan_count = _drop_orphan_patterns_with_count(pruned)
    stats.orphan_pruned += orphan_count
    if len(pruned) <= MAX_TOTAL_PATTERNS:
        return sorted(pruned, key=lambda pattern: pattern.pattern_id), stats

    baseline = [pattern for pattern in pruned if pattern.pattern_id.startswith(("actual:", "virtual:"))]
    baseline_ids = {pattern.pattern_id for pattern in baseline}
    candidates = [pattern for pattern in pruned if pattern.pattern_id not in baseline_ids]
    candidates.sort(key=lambda pattern: (-pattern.priority, pattern.cost, pattern.pattern_id))
    capped = baseline + candidates[: max(0, MAX_TOTAL_PATTERNS - len(baseline))]
    stats.total_cap_pruned = len(pruned) - len(capped)
    capped, orphan_count = _drop_orphan_patterns_with_count(capped)
    stats.orphan_pruned += orphan_count
    return sorted(capped, key=lambda pattern: pattern.pattern_id), stats


def _drop_orphan_patterns_with_count(patterns: list[Pattern]) -> tuple[list[Pattern], int]:
    retained = list(patterns)
    dropped = 0
    while True:
        retained_ids = {pattern.pattern_id for pattern in retained}
        next_retained = [
            pattern
            for pattern in retained
            if pattern.requires_pattern_ids.issubset(retained_ids)
        ]
        if len(next_retained) == len(retained):
            return next_retained, dropped
        dropped += len(retained) - len(next_retained)
        retained = next_retained


def _schedule_signature(schedule: list[Any]) -> tuple[tuple[tuple[str, Any], ...], ...]:
    return tuple(tuple(sorted(event.to_dict().items())) for event in schedule)


def _is_vessel_schedule_consistent(schedule: list[Any]) -> bool:
    for prev_event, next_event in zip(schedule, schedule[1:]):
        if event_end_time(prev_event) != event_start_time(next_event):
            return False
        if event_end_port_code(prev_event) != event_start_port_code(next_event):
            return False
        if not can_follow_event(prev_event, next_event):
            return False
    for event in schedule:
        if not isinstance(event, (OutLaneSail,)):
            continue
        duration_hours = (event_end_time(event) - event_start_time(event)).total_seconds() / 3600
        if duration_hours <= 0:
            return False
        if lookup_distance(event.from_port_code, event.to_port_code) / (duration_hours + 1e-5) > 20:
            return False
    return True


def _status_name(GRB, status: int) -> str:
    names = {}
    for name in (
        "LOADED",
        "OPTIMAL",
        "INFEASIBLE",
        "INF_OR_UNBD",
        "UNBOUNDED",
        "CUTOFF",
        "ITERATION_LIMIT",
        "NODE_LIMIT",
        "TIME_LIMIT",
        "SOLUTION_LIMIT",
        "INTERRUPTED",
        "NUMERIC",
        "SUBOPTIMAL",
        "INPROGRESS",
        "USER_OBJ_LIMIT",
        "WORK_LIMIT",
        "MEM_LIMIT",
    ):
        code = getattr(GRB, name, None)
        if code is not None:
            names[code] = name
    return names.get(status, f"STATUS_{status}")


def _solve_restricted_master(
    gp,
    GRB,
    instance_data: InstanceData,
    initial_solution: CascadingSolution,
    timelimit: int,
) -> CascadingSolution:
    fixed_positions = _fixed_position_keys(instance_data)
    selectable_positions = _selectable_position_keys(instance_data)
    coverage_positions = fixed_positions | selectable_positions
    initial_selectable_values = _initial_selectable_position_values(instance_data, initial_solution)
    coverage_schedules = _position_schedules(coverage_positions, instance_data)
    selectable_schedules = {
        position_key: coverage_schedules[position_key]
        for position_key in selectable_positions
    }
    fixed_coverage = _required_coverage_keys(initial_solution, fixed_positions)
    selectable_coverage: dict[CoverageKey, PositionKey] = {}
    for position_key, schedule in selectable_schedules.items():
        for coverage_key in _schedule_coverage_keys(schedule, {position_key}):
            selectable_coverage[coverage_key] = position_key
    required_coverage = fixed_coverage | set(selectable_coverage)
    patterns = _build_patterns(
        instance_data,
        initial_solution,
        coverage_positions,
        selectable_positions,
        coverage_schedules,
        selectable_schedules,
    )

    if not patterns:
        raise ValueError("wsgoh/twostage_support/mip_core: initial solution produced no patterns.")
    if not required_coverage:
        raise ValueError("wsgoh/twostage_support/mip_core: initial solution produced no required coverage keys.")

    model = gp.Model("wsgoh_twostage_mip_core_deep_cascade")
    model.Params.OutputFlag = 0
    if timelimit > 0:
        model.Params.TimeLimit = timelimit

    y = {
        pattern.pattern_id: model.addVar(vtype=GRB.BINARY, name=f"y_{_safe_name(pattern.pattern_id)}")
        for pattern in patterns
    }

    z = {
        key: model.addVar(
            vtype=GRB.BINARY,
            lb=0,
            ub=1,
            name=f"z_{_safe_name(key[0])}_{_safe_name(key[1])}_{key[2]}",
        )
        for key in sorted(selectable_positions)
    }

    actual_patterns_by_vessel: dict[str, list[str]] = {}
    patterns_by_coverage: dict[CoverageKey, list[str]] = {}
    pattern_ids = {pattern.pattern_id for pattern in patterns}
    for pattern in patterns:
        if not pattern.is_virtual:
            actual_patterns_by_vessel.setdefault(pattern.vessel_code, []).append(pattern.pattern_id)
        for coverage_key in pattern.coverage_keys:
            patterns_by_coverage.setdefault(coverage_key, []).append(pattern.pattern_id)
        missing_requirements = pattern.requires_pattern_ids - pattern_ids
        if missing_requirements:
            raise ValueError(
                f"wsgoh/twostage_support/mip_core: pattern {pattern.pattern_id!r} has missing parent patterns "
                f"{sorted(missing_requirements)!r}."
            )

    for vessel in instance_data.vessels:
        vessel_code = vessel["vessel_code"]
        vessel_pattern_ids = actual_patterns_by_vessel.get(vessel_code, [])
        if not vessel_pattern_ids:
            raise ValueError(f"wsgoh/twostage_support/mip_core: missing actual-vessel pattern for {vessel_code!r}.")
        model.addConstr(
            gp.quicksum(y[pattern_id] for pattern_id in vessel_pattern_ids) == 1,
            name=f"one_pattern_{_safe_name(vessel_code)}",
        )

    for pattern in patterns:
        for required_pattern_id in sorted(pattern.requires_pattern_ids):
            model.addConstr(
                y[pattern.pattern_id] <= y[required_pattern_id],
                name=f"requires_{_safe_name(pattern.pattern_id)}_{_safe_name(required_pattern_id)}",
            )

    for lane in instance_data.service_lanes:
        lane_code = lane["lane_code"]
        for version in lane["versions"]:
            available_positions = version["available_positions"] or []
            if not available_positions:
                continue
            proforma_name = version["proforma_name"]
            required_selectable_count = int(version["own_vessel_count"]) - len(version["declared_positions"])
            model.addConstr(
                gp.quicksum(z[(lane_code, proforma_name, int(position_no))] for position_no in available_positions)
                == required_selectable_count,
                name=f"declare_{_safe_name(lane_code)}_{_safe_name(proforma_name)}",
            )

    for coverage_key in sorted(required_coverage):
        position_key = (coverage_key[0], coverage_key[1], coverage_key[2])
        rhs = z[position_key] if coverage_key in selectable_coverage else 1.0
        model.addConstr(
            gp.quicksum(y[pattern_id] for pattern_id in patterns_by_coverage.get(coverage_key, [])) == rhs,
            name=(
                "cover_"
                f"{_safe_name(coverage_key[0])}_{_safe_name(coverage_key[1])}_"
                f"{coverage_key[2]}_{coverage_key[3]}_{int(coverage_key[5].timestamp())}"
            ),
        )

    pattern_cost_expr = gp.quicksum(pattern.cost * y[pattern.pattern_id] for pattern in patterns)
    model.setObjective(pattern_cost_expr, GRB.MINIMIZE)

    for pattern in patterns:
        y[pattern.pattern_id].Start = 1.0 if pattern.pattern_id.startswith(("actual:", "virtual:")) else 0.0
    for key, variable in z.items():
        variable.Start = float(initial_selectable_values.get(key, 0))

    model.optimize()
    status = _status_name(GRB, model.Status)
    family_counts = Counter(_pattern_family(pattern) for pattern in patterns)
    depth_counts = Counter(str(pattern.depth) for pattern in patterns)
    print(
        "wsgoh/twostage_support/mip_core Gurobi deep-cascade pattern master\n"
        f"- status: {status} ({model.Status})\n"
        f"- patterns: {len(patterns)}\n"
        f"- pattern families: {_format_counts(family_counts)}\n"
        f"- pattern depths: {_format_counts(depth_counts)}\n"
        f"- coupled child patterns: {sum(1 for pattern in patterns if pattern.requires_pattern_ids)}\n"
        f"- fixed coverage events: {len(fixed_coverage)}\n"
        f"- selectable coverage events: {len(selectable_coverage)}"
    )
    if model.SolCount < 1:
        raise RuntimeError(f"wsgoh/twostage_support/mip_core: Gurobi did not return a feasible solution. status={status}.")

    selected_pattern_ids = {
        pattern.pattern_id for pattern in patterns if y[pattern.pattern_id].X > 0.5
    }
    selected_patterns = [
        pattern for pattern in patterns if pattern.pattern_id in selected_pattern_ids
    ]
    selected_virtual_patterns = [pattern for pattern in selected_patterns if pattern.is_virtual]
    selected_partial_actual_patterns = [
        pattern for pattern in selected_patterns if _pattern_family(pattern) == "actual-partial-virtual"
    ]
    selected_family_counts = Counter(
        _pattern_family(pattern)
        for pattern in selected_patterns
    )
    selected_depth_counts = Counter(
        str(pattern.depth)
        for pattern in selected_patterns
    )
    print(
        f"- selected patterns: {len(selected_pattern_ids)}\n"
        f"- selected pattern families: {_format_counts(selected_family_counts)}\n"
        f"- selected pattern depths: {_format_counts(selected_depth_counts)}\n"
        f"- selected virtual patterns: {len(selected_virtual_patterns)}\n"
        f"- selected virtual PortStay events: {sum(len(pattern.coverage_keys) for pattern in selected_virtual_patterns)}\n"
        f"- selected partial virtual rescues: {len(selected_partial_actual_patterns)}\n"
        f"- rescued partial virtual PortStay events: {sum(len(pattern.coverage_keys) for pattern in selected_partial_actual_patterns)}\n"
        f"- selected positions: {sum(1 for variable in z.values() if variable.X > 0.5)}\n"
        f"- MIP pattern objective: {float(model.ObjVal):.6f}"
    )
    print("- selected pattern IDs:")
    for pattern in sorted(selected_patterns, key=lambda item: item.pattern_id):
        print(f"  - {pattern.pattern_id}")

    selected_switches = [
        pattern
        for pattern in selected_patterns
        if _pattern_family(pattern) in {"actual-switch", "actual-partial-virtual"}
    ]
    if selected_switches:
        print("- selected switch/rescue details:")
        for pattern in sorted(selected_switches, key=lambda item: item.pattern_id):
            parents = ",".join(sorted(pattern.requires_pattern_ids)) or "-"
            print(
                f"  - {pattern.pattern_id}: vessel={pattern.vessel_code}, "
                f"family={_pattern_family(pattern)}, "
                f"target_fragment={pattern.source_fragment_id or '-'}, "
                f"target={_format_position_key(pattern.target_position_key)}, "
                f"dropped={_format_position_key(pattern.dropped_position_key)}, "
                f"split={pattern.split_mode or '-'}, parent={parents}, cost={pattern.cost:.6f}"
            )

    if selected_virtual_patterns:
        print("- selected virtual coverage:")
        for pattern in sorted(selected_virtual_patterns, key=lambda item: item.pattern_id):
            parents = ",".join(sorted(pattern.requires_pattern_ids)) or "-"
            print(
                f"  - {pattern.pattern_id}: vessel={pattern.vessel_code}, "
                f"coverage={_format_coverage_positions(pattern)}, parent={parents}, cost={pattern.cost:.6f}"
            )

    selected_actual_schedules: dict[str, dict[str, Any]] = {}
    selected_virtual_schedules: dict[str, dict[str, Any]] = {}
    for pattern in selected_patterns:
        if pattern.is_virtual:
            selected_virtual_schedules[pattern.vessel_code] = pattern.schedule_payload
        else:
            selected_actual_schedules[pattern.vessel_code] = pattern.schedule_payload

    missing_actual_vessels = sorted(
        vessel["vessel_code"]
        for vessel in instance_data.vessels
        if vessel["vessel_code"] not in selected_actual_schedules
    )
    if missing_actual_vessels:
        raise RuntimeError(f"wsgoh/twostage_support/mip_core: selected solution is missing actual vessels: {missing_actual_vessels!r}.")

    selected_declared_positions = [
        {
            "lane_code": lane_code,
            "proforma_name": proforma_name,
            "declared_position_no": position_no,
        }
        for (lane_code, proforma_name, position_no), variable in sorted(z.items())
        if variable.X > 0.5
    ]

    return CascadingSolution(
        declared_positions=selected_declared_positions,
        vessel_schedules=selected_actual_schedules,
        virtual_vessel_schedules=selected_virtual_schedules,
        num_virtual_vessels_used=len(selected_virtual_schedules),
    )


def algorithm(instance_data: InstanceData, timelimit: int) -> CascadingSolution:
    gp, GRB = _load_gurobi()

    initial_solution = _make_initial_solution(instance_data, timelimit)
    validate_solution(initial_solution, instance_data)
    initial_cost = _evaluate_total_cost(initial_solution, instance_data)
    print(
        "wsgoh/twostage_support/mip_core initial solution\n"
        f"- actual vessel schedules: {len(initial_solution.vessel_schedules)}\n"
        f"- virtual vessel schedules: {len(initial_solution.virtual_vessel_schedules)}\n"
        f"- declared positions: {len(initial_solution.declared_positions)}\n"
        f"- evaluated total cost: {initial_cost:.6f}"
    )

    solution = _solve_restricted_master(gp, GRB, instance_data, initial_solution, timelimit)
    validate_solution(solution, instance_data)
    final_cost = _evaluate_total_cost(solution, instance_data)
    print(
        "wsgoh/twostage_support/mip_core final solution\n"
        f"- evaluated total cost: {final_cost:.6f}\n"
        f"- virtual vessel schedules: {len(solution.virtual_vessel_schedules)} "
        f"(initial {len(initial_solution.virtual_vessel_schedules)})"
    )

    return _clone_solution(solution)
