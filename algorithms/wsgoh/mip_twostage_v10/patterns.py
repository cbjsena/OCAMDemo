from __future__ import annotations

from collections import Counter
from typing import Any

from algorithms.wsgoh.utils_mip import (
    CoverageKey,
    Pattern,
    PositionKey,
    _drop_orphan_patterns_with_count,
    _estimate_pattern_cost,
    _position_key_from_schedule,
    _safe_name,
    _schedule_coverage_keys,
    _schedule_payload_signature,
    _try_assign_service_as_primary,
    _try_insert_service_schedule,
)
from algorithms.wsgoh.twostage_support.cascade_chain import solver as lite_chain_solver
from ocam.models import Delivery, DryDock, Idle, InstanceData, Redelivery, VesselScheduleEvent, CascadingSolution

from .config import (
    MAX_BASE_ACTUAL_CANDIDATE_PER_POSITION,
    MAX_BASE_ACTUAL_CANDIDATE_TOTAL_PER_SEED,
    MAX_BASE_ACTUAL_PRIMARY_PER_TARGET,
    MAX_BASE_ACTUAL_PRIMARY_TOTAL_PER_SEED,
    MAX_BASE_SCREENED_PER_TARGET,
    MAX_CANDIDATE_VESSELS_PER_HOLE,
    MAX_CASCADE_DEPTH,
    MAX_CHAIN_PATTERNS,
    MAX_HANDOVER_VARIANTS_PER_PAIR,
    MAX_PATTERNS_PER_VESSEL,
    MAX_TARGET_HOLES_PER_ROUND,
    _PATTERN_COST_CACHE,
)
from .types import CoverageContext, PoolPruneStats
from .utils import (
    _can_reposition_ports,
    _connection_events,
    _family_label,
    _schedule_is_consistent,
)

def _make_pattern(
    *,
    instance_data: InstanceData,
    context: CoverageContext,
    pattern_id: str,
    vessel_code: str,
    is_virtual: bool,
    schedule: list[Any],
    requires_pattern_ids: frozenset[str] = frozenset(),
    depth: int = 0,
    priority: float = 0.0,
    source_fragment_id: str | None = None,
    target_position_key: PositionKey | None = None,
    split_mode: str | None = None,
) -> Pattern:
    schedule_payload = {"events": [event.to_dict() for event in schedule]}
    cost_cache_key = (
        "__virtual__" if is_virtual else vessel_code,
        is_virtual,
        _schedule_payload_signature(schedule_payload),
    )
    cost = _PATTERN_COST_CACHE.get(cost_cache_key)
    if cost is None:
        cost = _estimate_pattern_cost(
            instance_data,
            context.declared_positions_payload,
            vessel_code,
            schedule_payload,
            is_virtual=is_virtual,
        )
        _PATTERN_COST_CACHE[cost_cache_key] = cost
    return Pattern(
        pattern_id=pattern_id,
        vessel_code=vessel_code,
        is_virtual=is_virtual,
        schedule_payload=schedule_payload,
        coverage_keys=_schedule_coverage_keys(schedule, context.coverage_positions),
        cost=cost,
        requires_pattern_ids=requires_pattern_ids,
        depth=depth,
        priority=priority,
        source_fragment_id=source_fragment_id,
        target_position_key=target_position_key,
        split_mode=split_mode,
    )

def _build_seed_patterns(
    instance_data: InstanceData,
    seed_solution: CascadingSolution,
    context: CoverageContext,
    seed_name: str,
    *,
    canonical: bool,
) -> list[Pattern]:
    patterns: list[Pattern] = []
    safe_seed = _safe_name(seed_name)
    actual_priority = 950_000.0
    virtual_priority = 930_000.0

    for vessel_code, schedule in seed_solution.vessel_schedules.items():
        pattern_id = f"actual-seed:{safe_seed}:{vessel_code}"
        patterns.append(
            _make_pattern(
                instance_data=instance_data,
                context=context,
                pattern_id=pattern_id,
                vessel_code=vessel_code,
                is_virtual=False,
                schedule=list(schedule),
                priority=actual_priority,
                source_fragment_id=f"seed:{seed_name}",
            )
        )

    for vessel_code, schedule in seed_solution.virtual_vessel_schedules.items():
        pattern_id = f"virtual-seed:{safe_seed}:{vessel_code}"
        virtual_vessel_code = f"MIPTWOSTAGE_{safe_seed}_{vessel_code}"
        patterns.append(
            _make_pattern(
                instance_data=instance_data,
                context=context,
                pattern_id=pattern_id,
                vessel_code=virtual_vessel_code,
                is_virtual=True,
                schedule=list(schedule),
                priority=virtual_priority,
                source_fragment_id=f"seed:{seed_name}",
            )
        )

    fixed_positions = context.coverage_positions - context.selectable_positions
    replacement_count = 0
    for virtual_code, virtual_schedule in seed_solution.virtual_vessel_schedules.items():
        if replacement_count >= MAX_BASE_ACTUAL_PRIMARY_TOTAL_PER_SEED:
            break
        position_key = _position_key_from_schedule(virtual_schedule)
        if position_key is None:
            continue
        accepted_for_target = 0
        screened_for_target = 0
        for vessel_code, vessel_schedule in seed_solution.vessel_schedules.items():
            if accepted_for_target >= MAX_BASE_ACTUAL_PRIMARY_PER_TARGET:
                break
            if replacement_count >= MAX_BASE_ACTUAL_PRIMARY_TOTAL_PER_SEED:
                break
            if screened_for_target >= MAX_BASE_SCREENED_PER_TARGET:
                break
            if _schedule_coverage_keys(vessel_schedule, fixed_positions):
                continue
            if not _vessel_matches_position(instance_data, vessel_code, position_key):
                continue
            screened_for_target += 1
            primary_schedule = _try_assign_service_as_primary(
                instance_data,
                vessel_code,
                list(vessel_schedule),
                position_key,
                list(virtual_schedule),
            )
            if primary_schedule is None:
                continue
            replacement_count += 1
            patterns.append(
                _make_pattern(
                    instance_data=instance_data,
                    context=context,
                    pattern_id=(
                        "actual-primary-virtual:"
                        f"{safe_seed}:{vessel_code}:{virtual_code}:{position_key[0]}:"
                        f"{position_key[1]}:{position_key[2]}:{replacement_count}"
                    ),
                    vessel_code=vessel_code,
                    is_virtual=False,
                    schedule=primary_schedule,
                    priority=760_000.0,
                    source_fragment_id=f"seed-initial-virtual:{seed_name}:{virtual_code}",
                    target_position_key=position_key,
                )
            )
            accepted_for_target += 1

    insertion_count = 0
    for position_key, service_schedule in context.selectable_schedules.items():
        if insertion_count >= MAX_BASE_ACTUAL_CANDIDATE_TOTAL_PER_SEED:
            break
        accepted_for_position = 0
        screened_for_position = 0
        for vessel_code, schedule in seed_solution.vessel_schedules.items():
            if accepted_for_position >= MAX_BASE_ACTUAL_CANDIDATE_PER_POSITION:
                break
            if insertion_count >= MAX_BASE_ACTUAL_CANDIDATE_TOTAL_PER_SEED:
                break
            if screened_for_position >= MAX_BASE_SCREENED_PER_TARGET:
                break
            if not _vessel_matches_position(instance_data, vessel_code, position_key):
                continue
            screened_for_position += 1
            candidate = _try_insert_service_schedule(
                instance_data,
                vessel_code,
                list(schedule),
                position_key,
                service_schedule,
            )
            if candidate is None:
                continue
            insertion_count += 1
            patterns.append(
                _make_pattern(
                    instance_data=instance_data,
                    context=context,
                    pattern_id=(
                        "actual-candidate:"
                        f"{safe_seed}:{vessel_code}:{position_key[0]}:{position_key[1]}:"
                        f"{position_key[2]}:{insertion_count}"
                    ),
                    vessel_code=vessel_code,
                    is_virtual=False,
                    schedule=candidate,
                    priority=95_000.0,
                    source_fragment_id=f"seed-selectable:{seed_name}:{position_key[0]}:{position_key[1]}:{position_key[2]}",
                    target_position_key=position_key,
                )
            )
            accepted_for_position += 1

    return patterns

def _build_virtual_full_fallback_patterns(
    instance_data: InstanceData,
    context: CoverageContext,
) -> list[Pattern]:
    patterns: list[Pattern] = []
    for position_key, schedule in context.coverage_schedules.items():
        patterns.append(
            _make_pattern(
                instance_data=instance_data,
                context=context,
                pattern_id=f"virtual-fallback:{position_key[0]}:{position_key[1]}:{position_key[2]}",
                vessel_code=f"MIPTWOSTAGE_FULL_{position_key[0]}_{position_key[1]}_{position_key[2]}",
                is_virtual=True,
                schedule=schedule,
                priority=900_000.0,
                target_position_key=position_key,
                source_fragment_id="virtual-full-fallback",
            )
        )
    return patterns

def _build_empty_actual_patterns(
    instance_data: InstanceData,
    context: CoverageContext,
) -> list[Pattern]:
    patterns: list[Pattern] = []
    for vessel in instance_data.vessels:
        vessel_code = vessel["vessel_code"]
        schedule = _build_empty_actual_schedule(instance_data, vessel)
        if schedule is None:
            continue
        patterns.append(
            _make_pattern(
                instance_data=instance_data,
                context=context,
                pattern_id=f"actual-empty:{vessel_code}",
                vessel_code=vessel_code,
                is_virtual=False,
                schedule=schedule,
                priority=100_000.0,
                source_fragment_id="obligation-only-empty-coverage",
            )
        )
    return patterns

def _build_empty_actual_schedule(
    instance_data: InstanceData,
    vessel: dict[str, Any],
) -> list[VesselScheduleEvent] | None:
    if vessel.get("current_assignment") is not None:
        return None

    planning_start = instance_data.planning_horizon["start"]
    planning_end = instance_data.planning_horizon["end"]
    available_from = vessel.get("available_from")
    available_from_port_code = vessel.get("available_from_port_code")
    if available_from_port_code is None:
        return None

    schedule: list[VesselScheduleEvent] = []
    if available_from is not None and planning_start <= available_from <= planning_end:
        schedule.append(
            Delivery(
                delivery_port_code=available_from_port_code,
                delivery_time=available_from,
            )
        )
        current_time = available_from
    else:
        current_time = planning_start
    current_port = available_from_port_code

    next_dock_in = vessel.get("next_dock_in")
    next_dock_out = vessel.get("next_dock_out")
    next_dock_port_code = vessel.get("next_dock_port_code")
    if (
        next_dock_in is None
        or next_dock_out is None
        or next_dock_port_code is None
        or next_dock_in > planning_end
        or next_dock_in <= current_time
    ):
        next_dock_in = None
        next_dock_out = None
        next_dock_port_code = None

    available_to = vessel.get("available_to")
    redelivery_port_code = vessel.get("available_to_port_code")
    if (
        available_to is None
        or redelivery_port_code is None
        or available_to > planning_end
        or available_to <= current_time
    ):
        available_to = None
        redelivery_port_code = None

    if next_dock_in is not None and available_to is not None and next_dock_in >= available_to:
        next_dock_in = None
        next_dock_out = None
        next_dock_port_code = None
    if next_dock_out is not None and available_to is not None and next_dock_out >= available_to:
        available_to = None
        redelivery_port_code = None

    if next_dock_in is not None and next_dock_out is not None and next_dock_port_code is not None:
        if not _can_reposition_ports(current_port, current_time, next_dock_port_code, next_dock_in):
            return None
        schedule.extend(_connection_events(current_port, current_time, next_dock_port_code, next_dock_in))
        schedule.append(
            DryDock(
                dock_port_code=next_dock_port_code,
                dock_in=next_dock_in,
                dock_out=next_dock_out,
            )
        )
        current_port = next_dock_port_code
        current_time = next_dock_out

    if available_to is not None and redelivery_port_code is not None:
        if not _can_reposition_ports(current_port, current_time, redelivery_port_code, available_to):
            return None
        schedule.extend(_connection_events(current_port, current_time, redelivery_port_code, available_to))
        schedule.append(
            Redelivery(
                redelivery_port_code=redelivery_port_code,
                redelivery_time=available_to,
            )
        )
    elif current_time < planning_end:
        schedule.append(
            Idle(
                port_code=current_port,
                idle_start=current_time,
                idle_end=planning_end,
            )
        )

    if not schedule:
        return None
    return schedule if _schedule_is_consistent(schedule) else None

def _protected_pattern_ids(patterns: list[Pattern], selected_pattern_ids: set[str]) -> set[str]:
    protected = set(selected_pattern_ids)
    for pattern in patterns:
        if pattern.pattern_id.startswith(("actual-empty:", "virtual-fallback:")):
            protected.add(pattern.pattern_id)
        protected.update(pattern.requires_pattern_ids)
    return protected

def _prefer_pattern(candidate: Pattern, incumbent: Pattern, protected_ids: set[str]) -> Pattern:
    candidate_key = (
        candidate.pattern_id in protected_ids,
        candidate.priority,
        -candidate.cost,
    )
    incumbent_key = (
        incumbent.pattern_id in protected_ids,
        incumbent.priority,
        -incumbent.cost,
    )
    return candidate if candidate_key > incumbent_key else incumbent

def _pattern_prune_signature(pattern: Pattern) -> tuple[Any, ...]:
    return (
        pattern.vessel_code,
        pattern.is_virtual,
        pattern.requires_pattern_ids,
        pattern.coverage_keys,
        _family_label(pattern),
        pattern.source_fragment_id,
        pattern.target_position_key,
        pattern.split_mode,
    )

def _prune_pattern_pool(
    patterns: list[Pattern],
    selected_pattern_ids: set[str],
    *,
    max_total_patterns: int,
) -> tuple[list[Pattern], PoolPruneStats]:
    protected_ids = _protected_pattern_ids(patterns, selected_pattern_ids)
    stats = PoolPruneStats(input_count=len(patterns), retained_count=0)

    by_schedule: dict[tuple[Any, ...], Pattern] = {}
    for pattern in patterns:
        signature = (
            pattern.vessel_code,
            pattern.is_virtual,
            pattern.requires_pattern_ids,
            _schedule_payload_signature(pattern.schedule_payload),
        )
        incumbent = by_schedule.get(signature)
        by_schedule[signature] = pattern if incumbent is None else _prefer_pattern(pattern, incumbent, protected_ids)
    stats.schedule_duplicate_pruned = len(patterns) - len(by_schedule)

    by_coverage: dict[tuple[Any, ...], Pattern] = {}
    for pattern in by_schedule.values():
        signature = _pattern_prune_signature(pattern)
        incumbent = by_coverage.get(signature)
        by_coverage[signature] = pattern if incumbent is None else _prefer_pattern(pattern, incumbent, protected_ids)
    stats.coverage_duplicate_pruned = len(by_schedule) - len(by_coverage)

    retained = list(by_coverage.values())
    actual_by_vessel: dict[str, list[Pattern]] = {}
    virtual_patterns: list[Pattern] = []
    for pattern in retained:
        if pattern.is_virtual:
            virtual_patterns.append(pattern)
        else:
            actual_by_vessel.setdefault(pattern.vessel_code, []).append(pattern)

    capped: list[Pattern] = list(virtual_patterns)
    for vessel_patterns in actual_by_vessel.values():
        protected = [pattern for pattern in vessel_patterns if pattern.pattern_id in protected_ids]
        candidates = [pattern for pattern in vessel_patterns if pattern.pattern_id not in protected_ids]
        candidates.sort(key=lambda pattern: (-pattern.priority, pattern.cost, pattern.pattern_id))
        capped.extend(protected + candidates[: max(0, MAX_PATTERNS_PER_VESSEL - len(protected))])
    stats.vessel_cap_pruned = len(retained) - len(capped)

    capped, orphan_count = _drop_orphan_patterns_with_count(capped)
    stats.orphan_pruned += orphan_count
    if len(capped) > max_total_patterns:
        protected = [pattern for pattern in capped if pattern.pattern_id in protected_ids]
        candidates = [pattern for pattern in capped if pattern.pattern_id not in protected_ids]
        candidates.sort(key=lambda pattern: (-pattern.priority, pattern.cost, pattern.pattern_id))
        capped = protected + candidates[: max(0, max_total_patterns - len(protected))]
        stats.total_cap_pruned = len(retained) - len(capped)
        capped, orphan_count = _drop_orphan_patterns_with_count(capped)
        stats.orphan_pruned += orphan_count

    stats.retained_count = len(capped)
    return sorted(capped, key=lambda pattern: pattern.pattern_id), stats

def _virtual_fragments(solution: CascadingSolution) -> list[tuple[str, PositionKey, list[Any]]]:
    fragments: list[tuple[str, PositionKey, list[Any]]] = []
    for virtual_code, schedule in solution.virtual_vessel_schedules.items():
        position_key = _position_key_from_schedule(schedule)
        if position_key is not None:
            fragments.append((virtual_code, position_key, list(schedule)))
    return fragments

def _position_requirements(instance_data: InstanceData, position_key: PositionKey) -> tuple[float, float]:
    lane_code, proforma_name, _ = position_key
    for lane in instance_data.service_lanes:
        if lane["lane_code"] != lane_code:
            continue
        for version in lane["versions"]:
            if version["proforma_name"] == proforma_name:
                return float(version["required_capacity_teu"]), float(version["required_reefer_plug"])
    return 0.0, 0.0

def _vessel_matches_position(instance_data: InstanceData, vessel_code: str, position_key: PositionKey) -> bool:
    required_capacity_teu, required_reefer_plug = _position_requirements(instance_data, position_key)
    vessel = next((item for item in instance_data.vessels if item["vessel_code"] == vessel_code), None)
    if vessel is None:
        return False
    return (
        required_capacity_teu * 0.95 <= float(vessel["capacity_teu"]) <= required_capacity_teu * 1.05
        and required_reefer_plug <= float(vessel["reefer_plug"])
    )

def _configure_lite_chain_limits() -> None:
    lite_chain_solver.MAX_CASCADE_DEPTH = MAX_CASCADE_DEPTH
    lite_chain_solver.MAX_TARGET_HOLES_PER_ROUND = MAX_TARGET_HOLES_PER_ROUND
    lite_chain_solver.MAX_CANDIDATE_VESSELS_PER_HOLE = MAX_CANDIDATE_VESSELS_PER_HOLE
    lite_chain_solver.MAX_HANDOVER_OPTIONS_PER_VESSEL_HOLE = MAX_HANDOVER_VARIANTS_PER_PAIR
    lite_chain_solver.MAX_CHAIN_PATTERNS_PER_ROUND = MAX_CHAIN_PATTERNS

def _generate_chain_patterns_from_seeds(
    instance_data: InstanceData,
    context: CoverageContext,
    seed_solutions: dict[str, CascadingSolution],
) -> tuple[list[Pattern], list[Pattern], Counter[str]]:
    _configure_lite_chain_limits()
    actual_patterns: list[Pattern] = []
    virtual_patterns: list[Pattern] = []
    diagnostics: Counter[str] = Counter()
    round_index = 0
    for seed_name, solution in seed_solutions.items():
        round_index += 1
        generated, seed_diagnostics = lite_chain_solver.generate_chain_patterns(
            instance_data,
            context,
            solution,
            round_index,
        )
        diagnostics.update({f"{seed_name}_{key}": value for key, value in seed_diagnostics.items()})
        for pattern in generated:
            if pattern.is_virtual:
                virtual_patterns.append(pattern)
            else:
                actual_patterns.append(pattern)
            if len(actual_patterns) >= MAX_CHAIN_PATTERNS:
                break
        if len(actual_patterns) >= MAX_CHAIN_PATTERNS:
            break
    diagnostics["cascade_chain_actual"] = len(actual_patterns)
    diagnostics["cascade_chain_virtual"] = len(virtual_patterns)
    return actual_patterns[:MAX_CHAIN_PATTERNS], virtual_patterns, diagnostics

def _actual_only_patterns(patterns: list[Pattern]) -> list[Pattern]:
    actual_ids = {pattern.pattern_id for pattern in patterns if not pattern.is_virtual}
    retained = [
        pattern
        for pattern in patterns
        if not pattern.is_virtual and pattern.requires_pattern_ids.issubset(actual_ids)
    ]
    retained, _ = _drop_orphan_patterns_with_count(retained)
    return retained

def _warm_start_z_values(
    context: CoverageContext,
    patterns: list[Pattern],
    warm_start_ids: set[str],
) -> dict[PositionKey, int]:
    values = {key: 0 for key in context.selectable_positions}
    for pattern in patterns:
        if pattern.pattern_id not in warm_start_ids:
            continue
        for coverage_key in pattern.coverage_keys:
            position_key = (coverage_key[0], coverage_key[1], coverage_key[2])
            if position_key in values:
                values[position_key] = 1
    return values

def _seed_actual_pattern_ids(patterns: list[Pattern], seed_name: str) -> set[str]:
    prefix = f"actual-seed:{_safe_name(seed_name)}:"
    return {pattern.pattern_id for pattern in patterns if pattern.pattern_id.startswith(prefix)}

def _seed_all_pattern_ids(patterns: list[Pattern], seed_name: str) -> set[str]:
    safe_seed = _safe_name(seed_name)
    return {
        pattern.pattern_id
        for pattern in patterns
        if pattern.pattern_id.startswith(f"actual-seed:{safe_seed}:")
        or pattern.pattern_id.startswith(f"virtual-seed:{safe_seed}:")
    }

def _complete_actual_warm_start(
    patterns: list[Pattern],
    warm_start_ids: set[str],
    instance_data: InstanceData,
) -> set[str]:
    completed = set(warm_start_ids)
    selected_by_vessel = {
        pattern.vessel_code
        for pattern in patterns
        if not pattern.is_virtual and pattern.pattern_id in completed
    }
    by_vessel: dict[str, list[Pattern]] = {}
    for pattern in patterns:
        if not pattern.is_virtual:
            by_vessel.setdefault(pattern.vessel_code, []).append(pattern)
    for vessel in instance_data.vessels:
        vessel_code = vessel["vessel_code"]
        if vessel_code in selected_by_vessel:
            continue
        candidates = by_vessel.get(vessel_code, [])
        if candidates:
            completed.add(min(candidates, key=lambda pattern: (pattern.cost, pattern.pattern_id)).pattern_id)
    return completed
