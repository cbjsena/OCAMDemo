from __future__ import annotations

import contextlib
import io
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable

from algorithms.yongs.only_virtual2 import solver as mip_utility_solver
from algorithms.wsgoh.utils_mip import (
    PositionKey,
    _clone_solution,
    _fixed_position_keys,
    _position_schedules,
    _selectable_position_keys,
)
from ocam.models import (
    CascadingSolution,
    Idle,
    InLaneSail,
    InstanceData,
    OutLaneSail,
    PhaseIn,
    PhaseOut,
    PortStay,
    TransshipmentLoad,
    TransshipmentUnload,
    VesselScheduleEvent,
)
from ocam.utils import (
    event_end_port_code,
    event_end_time,
    event_start_port_code,
    event_start_time,
    init_utils,
    lookup_distance,
)
from ocam.validation import can_follow_event, evaluate_solution, validate_solution

IMPROVEMENT_TOLERANCE = 1e-6
MAX_TS_CANDIDATES = 80
MAX_POSITION_SHIFT_TARGETS = 8
MAX_POSITION_SHIFT_CANDIDATES = 80
POSITION_SHIFT_RADIUS = 2
MAX_POSITION_SHIFT_ACCEPTED = 8


@dataclass(frozen=True)
class PositionShiftTarget:
    vessel_code: str
    position_key: PositionKey
    first_index: int
    last_index: int
    score: float


def finalize_v10_solution(
    instance_data: InstanceData,
    solution: CascadingSolution,
    *,
    label: str = "mip_twostage_v10",
    enable_mixed_position_shift: bool = False,
) -> tuple[CascadingSolution, dict[str, Any]]:
    init_utils(instance_data)
    original = _clone_solution(solution)
    validate_solution(original, instance_data)
    original_cost = _silent_total_cost(original, instance_data)
    stats: dict[str, Any] = {
        "label": label,
        "original_cost": original_cost,
        "cleanup_initial_status": "not_run",
        "cleanup_final_status": "not_run",
        "ts_chains_seen": 0,
        "ts_candidates_tested": 0,
        "ts_accepted": 0,
        "ts_rejected_validation": 0,
        "ts_rejected_no_improvement": 0,
        "ts_best_delta": 0.0,
        "position_shift_enabled": False,
        "position_shift_skipped_reason": "",
        "position_shift_targets": 0,
        "position_shift_target_vessels": 0,
        "position_shift_candidates_tested": 0,
        "position_shift_accepted": 0,
        "position_shift_rejected_validation": 0,
        "position_shift_rejected_no_improvement": 0,
        "position_shift_best_delta": 0.0,
    }

    current, cleanup_stats = cleanup_declared_positions(instance_data, original)
    stats.update({f"cleanup_initial_{key}": value for key, value in cleanup_stats.items()})
    stats["cleanup_initial_status"] = cleanup_stats["status"]
    current_cost = _silent_total_cost(current, instance_data)

    current, current_cost, ts_stats = improve_ts_timing_by_slack_reallocation(
        instance_data,
        current,
        current_cost,
    )
    stats.update(ts_stats)

    is_zero_virtual = _virtual_portstay_count(current) == 0 and len(current.virtual_vessel_schedules) == 0
    if is_zero_virtual or enable_mixed_position_shift:
        stats["position_shift_enabled"] = True
        current, current_cost, shift_stats = local_position_shift(instance_data, current, current_cost)
        stats.update(shift_stats)
    else:
        stats["position_shift_skipped_reason"] = "mixed_fallback_solution"

    current, cleanup_stats = cleanup_declared_positions(instance_data, current)
    stats.update({f"cleanup_final_{key}": value for key, value in cleanup_stats.items()})
    stats["cleanup_final_status"] = cleanup_stats["status"]
    validate_solution(current, instance_data)
    final_cost = _silent_total_cost(current, instance_data)
    if final_cost > original_cost + IMPROVEMENT_TOLERANCE:
        stats["final_status"] = "reverted_cost_increase"
        stats["final_cost"] = original_cost
        stats["total_delta"] = 0.0
        return original, stats

    stats["final_status"] = "ok"
    stats["final_cost"] = final_cost
    stats["total_delta"] = original_cost - final_cost
    return current, stats


def cleanup_declared_positions(
    instance_data: InstanceData,
    solution: CascadingSolution,
) -> tuple[CascadingSolution, dict[str, Any]]:
    stats: dict[str, Any] = {"status": "no_op", "changed": False, "rejected_reason": ""}
    lane_view = solution.to_lane_view()
    selected_positions = set(lane_view.keys())
    declared: list[dict[str, Any]] = []
    for lane in instance_data.service_lanes:
        lane_code = lane["lane_code"]
        for version in lane["versions"]:
            available_positions = version["available_positions"] or []
            if not available_positions:
                continue
            proforma_name = version["proforma_name"]
            available_set = {int(position_no) for position_no in available_positions}
            selected = sorted(
                position_no
                for selected_lane, selected_proforma, position_no in selected_positions
                if selected_lane == lane_code
                and selected_proforma == proforma_name
                and int(position_no) in available_set
            )
            required = int(version["own_vessel_count"])
            if len(selected) != required:
                stats["status"] = "rejected"
                stats["rejected_reason"] = (
                    f"{lane_code}/{proforma_name}: selected={len(selected)} required={required}"
                )
                return _clone_solution(solution), stats
            for position_no in selected:
                declared.append(
                    {
                        "lane_code": lane_code,
                        "proforma_name": proforma_name,
                        "declared_position_no": int(position_no),
                    }
                )

    current_payload = sorted(
        (item.lane_code, item.proforma_name, item.declared_position_no)
        for item in solution.declared_positions
    )
    new_payload = sorted(
        (item["lane_code"], item["proforma_name"], item["declared_position_no"])
        for item in declared
    )
    if current_payload == new_payload:
        return _clone_solution(solution), stats

    candidate = _make_solution(solution, declared_positions=declared)
    try:
        validate_solution(candidate, instance_data)
    except Exception as exc:
        stats["status"] = "rejected"
        stats["rejected_reason"] = type(exc).__name__
        return _clone_solution(solution), stats
    stats["status"] = "changed"
    stats["changed"] = True
    return candidate, stats


def improve_ts_timing_by_slack_reallocation(
    instance_data: InstanceData,
    solution: CascadingSolution,
    current_cost: float,
) -> tuple[CascadingSolution, float, dict[str, Any]]:
    current = _clone_solution(solution)
    stats = {
        "ts_chains_seen": 0,
        "ts_candidates_tested": 0,
        "ts_accepted": 0,
        "ts_rejected_validation": 0,
        "ts_rejected_no_improvement": 0,
        "ts_best_delta": 0.0,
    }
    for chain in _ts_chains(current):
        if stats["ts_candidates_tested"] >= MAX_TS_CANDIDATES:
            break
        stats["ts_chains_seen"] += 1
        for phase_in_time in _phase_in_candidate_times(chain):
            if stats["ts_candidates_tested"] >= MAX_TS_CANDIDATES:
                break
            if phase_in_time == chain["phase_in"].phase_in_time:
                continue
            stats["ts_candidates_tested"] += 1
            candidate = _retime_ts_chain(current, chain, phase_in_time)
            if candidate is None:
                stats["ts_rejected_validation"] += 1
                continue
            try:
                validate_solution(candidate, instance_data)
            except Exception:
                stats["ts_rejected_validation"] += 1
                continue
            candidate_cost = _silent_total_cost(candidate, instance_data)
            delta = current_cost - candidate_cost
            stats["ts_best_delta"] = max(stats["ts_best_delta"], delta)
            if delta > IMPROVEMENT_TOLERANCE:
                current = candidate
                current_cost = candidate_cost
                stats["ts_accepted"] += 1
                break
            stats["ts_rejected_no_improvement"] += 1
    return current, current_cost, stats


def local_position_shift(
    instance_data: InstanceData,
    solution: CascadingSolution,
    current_cost: float,
) -> tuple[CascadingSolution, float, dict[str, Any]]:
    stats = {
        "position_shift_targets": 0,
        "position_shift_target_vessels": 0,
        "position_shift_candidates_tested": 0,
        "position_shift_accepted": 0,
        "position_shift_rejected_validation": 0,
        "position_shift_rejected_no_improvement": 0,
        "position_shift_best_delta": 0.0,
    }
    selectable = _selectable_position_keys(instance_data)
    fixed = _fixed_position_keys(instance_data)
    assigned = _assigned_start_locks(instance_data)
    position_schedules = _position_schedules(selectable, instance_data)
    current = _clone_solution(solution)

    while (
        stats["position_shift_candidates_tested"] < MAX_POSITION_SHIFT_CANDIDATES
        and stats["position_shift_accepted"] < MAX_POSITION_SHIFT_ACCEPTED
    ):
        target_vessels = _ts_vessel_codes(current)
        stats["position_shift_target_vessels"] = max(
            stats["position_shift_target_vessels"],
            len(target_vessels),
        )
        if not target_vessels:
            stats["position_shift_skipped_reason"] = "no_ts_vessels"
            break
        selected_positions = _selected_positions(current)
        targets = _position_shift_targets(
            current,
            selectable,
            fixed,
            assigned,
            selected_positions,
            target_vessels,
        )
        stats["position_shift_targets"] = max(stats["position_shift_targets"], len(targets))
        accepted = False
        for target in targets[:MAX_POSITION_SHIFT_TARGETS]:
            if stats["position_shift_candidates_tested"] >= MAX_POSITION_SHIFT_CANDIDATES:
                break
            for neighbor in _neighbor_positions(target.position_key, selectable):
                if stats["position_shift_candidates_tested"] >= MAX_POSITION_SHIFT_CANDIDATES:
                    break
                if neighbor in selected_positions or neighbor in assigned or neighbor in fixed:
                    continue
                service_schedule = position_schedules.get(neighbor)
                if service_schedule is None:
                    continue
                stats["position_shift_candidates_tested"] += 1
                candidate = _replace_position_block(current, target, service_schedule)
                if candidate is None:
                    stats["position_shift_rejected_validation"] += 1
                    continue
                candidate, _ = cleanup_declared_positions(instance_data, candidate)
                try:
                    validate_solution(candidate, instance_data)
                except Exception:
                    stats["position_shift_rejected_validation"] += 1
                    continue
                candidate_cost = _silent_total_cost(candidate, instance_data)
                delta = current_cost - candidate_cost
                stats["position_shift_best_delta"] = max(stats["position_shift_best_delta"], delta)
                if delta > IMPROVEMENT_TOLERANCE:
                    current = candidate
                    current_cost = candidate_cost
                    stats["position_shift_accepted"] += 1
                    accepted = True
                    break
                stats["position_shift_rejected_no_improvement"] += 1
            if accepted:
                break
        if not accepted:
            break
    return current, current_cost, stats


def _silent_total_cost(solution: CascadingSolution, instance_data: InstanceData) -> float:
    with contextlib.redirect_stdout(io.StringIO()):
        evaluated = evaluate_solution(solution, instance_data)
    if evaluated is None:
        return 0.0
    return float(evaluated["total_cost"])


def _virtual_portstay_count(solution: CascadingSolution) -> int:
    return sum(
        1
        for schedule in solution.virtual_vessel_schedules.values()
        for event in schedule
        if isinstance(event, PortStay)
    )


def _make_solution(
    base: CascadingSolution,
    *,
    declared_positions: list[dict[str, Any]] | None = None,
    vessel_schedules: dict[str, list[Any]] | None = None,
    virtual_vessel_schedules: dict[str, list[Any]] | None = None,
) -> CascadingSolution:
    actual_payload = {
        vessel_code: {"events": [event.to_dict() for event in schedule]}
        for vessel_code, schedule in (
            vessel_schedules
            if vessel_schedules is not None
            else {code: list(schedule) for code, schedule in base.vessel_schedules.items()}
        ).items()
    }
    virtual_payload = {
        vessel_code: {"events": [event.to_dict() for event in schedule]}
        for vessel_code, schedule in (
            virtual_vessel_schedules
            if virtual_vessel_schedules is not None
            else {code: list(schedule) for code, schedule in base.virtual_vessel_schedules.items()}
        ).items()
    }
    declared_payload = (
        declared_positions
        if declared_positions is not None
        else [declared_position.to_dict() for declared_position in base.declared_positions]
    )
    return CascadingSolution(
        declared_positions=declared_payload,
        vessel_schedules=actual_payload,
        virtual_vessel_schedules=virtual_payload,
        num_virtual_vessels_used=len(virtual_payload),
    )


def _clone_schedule(schedule: Iterable[Any]) -> list[VesselScheduleEvent]:
    return [VesselScheduleEvent.coerce(event.to_dict()) for event in schedule]


def _event_position_key(event: Any) -> PositionKey | None:
    if all(hasattr(event, attr) for attr in ("lane_code", "proforma_name", "position_no")):
        return (event.lane_code, event.proforma_name, int(event.position_no))
    return None


def _selected_positions(solution: CascadingSolution) -> set[PositionKey]:
    return set(solution.to_lane_view().keys())


def _assigned_start_locks(instance_data: InstanceData) -> dict[PositionKey, str]:
    assigned: dict[PositionKey, str] = {}
    for lane in instance_data.service_lanes:
        lane_code = lane["lane_code"]
        for version in lane["versions"]:
            proforma_name = version["proforma_name"]
            for assignment in version.get("vessel_assignments", []):
                assigned[(lane_code, proforma_name, int(assignment["position_no"]))] = assignment["vessel_code"]
    return assigned


def _ts_chains(solution: CascadingSolution) -> list[dict[str, Any]]:
    chains: list[dict[str, Any]] = []
    lane_view = solution.to_lane_view()
    for lane_events in lane_view.values():
        for index in range(1, len(lane_events) - 2):
            unload = lane_events[index - 1]
            phase_out = lane_events[index]
            phase_in = lane_events[index + 1]
            load = lane_events[index + 2]
            if not (
                isinstance(unload.event, TransshipmentUnload)
                and isinstance(phase_out.event, PhaseOut)
                and isinstance(phase_in.event, PhaseIn)
                and isinstance(load.event, TransshipmentLoad)
            ):
                continue
            if phase_out.vessel_code == phase_in.vessel_code:
                continue
            chains.append(
                {
                    "outgoing_vessel": phase_out.vessel_code,
                    "incoming_vessel": phase_in.vessel_code,
                    "unload": unload.event,
                    "phase_out": phase_out.event,
                    "phase_in": phase_in.event,
                    "load": load.event,
                }
            )
    return chains


def _ts_vessel_codes(solution: CascadingSolution) -> set[str]:
    vessel_codes: set[str] = set()
    for chain in _ts_chains(solution):
        vessel_codes.add(chain["outgoing_vessel"])
        vessel_codes.add(chain["incoming_vessel"])
    return vessel_codes


def _phase_in_candidate_times(chain: dict[str, Any]) -> list[datetime]:
    phase_out_time = chain["phase_out"].phase_out_time
    phase_in_time = chain["phase_in"].phase_in_time
    load_end = chain["load"].load_end
    earliest = phase_out_time + timedelta(days=1)
    latest = min(phase_out_time + timedelta(days=7), load_end)
    if earliest > latest:
        return []
    candidates = {
        phase_in_time,
        earliest,
        latest,
        max(earliest, min(latest, phase_in_time - timedelta(hours=6))),
        max(earliest, min(latest, phase_in_time + timedelta(hours=6))),
    }
    return sorted(candidates)


def _retime_ts_chain(
    solution: CascadingSolution,
    chain: dict[str, Any],
    new_phase_in_time: datetime,
) -> CascadingSolution | None:
    incoming_vessel = chain["incoming_vessel"]
    schedules = {code: _clone_schedule(schedule) for code, schedule in solution.vessel_schedules.items()}
    virtual_schedules = {
        code: _clone_schedule(schedule) for code, schedule in solution.virtual_vessel_schedules.items()
    }
    target_map = virtual_schedules if incoming_vessel in virtual_schedules else schedules
    schedule = target_map.get(incoming_vessel)
    if schedule is None:
        return None

    phase_in_index = _find_matching_event_index(schedule, chain["phase_in"])
    load_index = _find_matching_event_index(schedule, chain["load"])
    if phase_in_index is None or load_index is None or load_index != phase_in_index + 1:
        return None
    load_event = schedule[load_index]
    if not isinstance(load_event, TransshipmentLoad):
        return None
    if new_phase_in_time > load_event.load_end:
        return None

    phase_in = schedule[phase_in_index]
    if not isinstance(phase_in, PhaseIn):
        return None
    schedule[phase_in_index] = PhaseIn(
        lane_code=phase_in.lane_code,
        proforma_name=phase_in.proforma_name,
        position_no=phase_in.position_no,
        phase_in_port_code=phase_in.phase_in_port_code,
        phase_in_port_seq=phase_in.phase_in_port_seq,
        phase_in_time=new_phase_in_time,
    )
    schedule[load_index] = TransshipmentLoad(
        lane_code=load_event.lane_code,
        proforma_name=load_event.proforma_name,
        position_no=load_event.position_no,
        ts_port_code=load_event.ts_port_code,
        ts_port_seq=load_event.ts_port_seq,
        load_start=new_phase_in_time,
        load_end=load_event.load_end,
    )

    if phase_in_index > 0:
        bridge = _replacement_bridge(schedule[phase_in_index - 1], schedule[phase_in_index])
        if bridge is None:
            return None
        if bridge:
            schedule[phase_in_index - 1] = bridge[0]
    if not _schedule_is_consistent(schedule):
        return None
    target_map[incoming_vessel] = schedule
    return _make_solution(
        solution,
        vessel_schedules=schedules,
        virtual_vessel_schedules=virtual_schedules,
    )


def _find_matching_event_index(schedule: list[Any], needle: Any) -> int | None:
    needle_payload = needle.to_dict()
    for index, event in enumerate(schedule):
        if event is needle or event.to_dict() == needle_payload:
            return index
    return None


def _replacement_bridge(previous: Any, current: Any) -> list[Any] | None:
    start = event_start_time(previous)
    end = event_start_time(current)
    if event_end_time(previous) == end and event_end_port_code(previous) == event_start_port_code(current):
        return []
    if start > end:
        return None
    if not isinstance(previous, (Idle, OutLaneSail)):
        return None
    bridge = _make_bridge(
        event_start_port_code(previous),
        start,
        event_start_port_code(current),
        end,
    )
    return [] if bridge is None else [bridge]


def _make_bridge(from_port: str, start: datetime, to_port: str, end: datetime) -> Any | None:
    if start > end:
        return None
    if start == end:
        return None if from_port != to_port else None
    if from_port != to_port:
        hours = (end - start).total_seconds() / 3600.0
        if hours <= 0:
            return None
        if lookup_distance(from_port, to_port) / (hours + 1e-5) > 20.0:
            return None
    return mip_utility_solver.out_sail_or_idle(
        from_port_code=from_port,
        sea_sail_start=start,
        sea_sail_end=end,
        to_port_code=to_port,
    )


def _position_shift_targets(
    solution: CascadingSolution,
    selectable: set[PositionKey],
    fixed: set[PositionKey],
    assigned: dict[PositionKey, str],
    selected_positions: set[PositionKey],
    target_vessels: set[str],
) -> list[PositionShiftTarget]:
    targets: list[PositionShiftTarget] = []
    for vessel_code, schedule_obj in solution.vessel_schedules.items():
        if vessel_code not in target_vessels:
            continue
        schedule = list(schedule_obj)
        index = 0
        while index < len(schedule):
            position_key = _event_position_key(schedule[index])
            if position_key is None:
                index += 1
                continue
            first = index
            while index + 1 < len(schedule) and _event_position_key(schedule[index + 1]) == position_key:
                index += 1
            last = index
            block = schedule[first : last + 1]
            index += 1
            if position_key not in selectable or position_key not in selected_positions:
                continue
            if position_key in fixed or position_key in assigned:
                continue
            if any(not isinstance(event, (InLaneSail, PortStay)) for event in block):
                continue
            score = _adjacent_outlane_distance_score(schedule, first, last)
            if score <= 0:
                continue
            targets.append(
                PositionShiftTarget(
                    vessel_code=vessel_code,
                    position_key=position_key,
                    first_index=first,
                    last_index=last,
                    score=score,
                )
            )
    targets.sort(key=lambda item: (-item.score, item.vessel_code, item.position_key))
    return targets


def _adjacent_outlane_distance_score(schedule: list[Any], first_index: int, last_index: int) -> float:
    score = 0.0
    for index in (first_index - 1, last_index + 1):
        if 0 <= index < len(schedule) and isinstance(schedule[index], OutLaneSail):
            score += float(lookup_distance(schedule[index].from_port_code, schedule[index].to_port_code))
    return score


def _neighbor_positions(position_key: PositionKey, selectable: set[PositionKey]) -> list[PositionKey]:
    lane_code, proforma_name, position_no = position_key
    neighbors: list[PositionKey] = []
    for offset in range(1, POSITION_SHIFT_RADIUS + 1):
        for candidate_no in (position_no - offset, position_no + offset):
            candidate = (lane_code, proforma_name, candidate_no)
            if candidate in selectable:
                neighbors.append(candidate)
    return neighbors


def _replace_position_block(
    solution: CascadingSolution,
    target: PositionShiftTarget,
    new_service_schedule: list[Any],
) -> CascadingSolution | None:
    vessel_schedules = {code: _clone_schedule(schedule) for code, schedule in solution.vessel_schedules.items()}
    schedule = vessel_schedules.get(target.vessel_code)
    if schedule is None:
        return None
    if target.last_index >= len(schedule):
        return None
    if any(isinstance(event, (PhaseIn, PhaseOut, TransshipmentLoad, TransshipmentUnload)) for event in new_service_schedule):
        return None

    prefix = _clone_schedule(schedule[: target.first_index])
    suffix = _clone_schedule(schedule[target.last_index + 1 :])
    if not prefix:
        return None
    connected = _connect_blocks(prefix, _clone_schedule(new_service_schedule), suffix)
    if connected is None:
        return None
    if not _schedule_is_consistent(connected):
        return None
    vessel_schedules[target.vessel_code] = connected
    return _make_solution(solution, vessel_schedules=vessel_schedules)


def _connect_blocks(prefix: list[Any], service: list[Any], suffix: list[Any]) -> list[Any] | None:
    if not service:
        return None
    result = list(prefix)
    bridge = _make_bridge(
        event_end_port_code(result[-1]),
        event_end_time(result[-1]),
        event_start_port_code(service[0]),
        event_start_time(service[0]),
    )
    if bridge is None and (
        event_end_time(result[-1]) != event_start_time(service[0])
        or event_end_port_code(result[-1]) != event_start_port_code(service[0])
    ):
        return None
    if bridge is not None:
        result.append(bridge)
    result.extend(service)
    if suffix:
        bridge = _make_bridge(
            event_end_port_code(result[-1]),
            event_end_time(result[-1]),
            event_start_port_code(suffix[0]),
            event_start_time(suffix[0]),
        )
        if bridge is None and (
            event_end_time(result[-1]) != event_start_time(suffix[0])
            or event_end_port_code(result[-1]) != event_start_port_code(suffix[0])
        ):
            return None
        if bridge is not None:
            result.append(bridge)
        result.extend(suffix)
    return result


def _schedule_is_consistent(schedule: list[Any]) -> bool:
    for previous, current in zip(schedule, schedule[1:]):
        if event_end_time(previous) != event_start_time(current):
            return False
        if event_end_port_code(previous) != event_start_port_code(current):
            return False
        if not can_follow_event(previous, current):
            return False
    for event in schedule:
        if not isinstance(event, (InLaneSail, OutLaneSail)):
            continue
        duration_hours = (event_end_time(event) - event_start_time(event)).total_seconds() / 3600.0
        if duration_hours <= 0:
            return False
        if lookup_distance(event.from_port_code, event.to_port_code) / (duration_hours + 1e-5) > 20.0:
            return False
    return True
