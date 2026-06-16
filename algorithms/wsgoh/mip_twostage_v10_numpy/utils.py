from __future__ import annotations

import time
from collections import Counter
from hashlib import blake2b
from typing import Any, Iterable

from algorithms.wsgoh.heuristic_yongs.solver import VariantResult
from algorithms.yongs.only_virtual2 import solver as mip_utility_solver
from algorithms.wsgoh.utils_mip import (
    CoverageKey,
    Pattern,
    PositionKey,
    _declared_position_payload,
    _fixed_position_keys,
    _format_coverage_positions,
    _pattern_family,
    _position_schedules,
    _safe_name,
    _schedule_coverage_keys,
    _selectable_position_keys,
)
from ocam.models import CascadingSolution, Idle, InstanceData, PortStay, VesselScheduleEvent
from ocam.utils import event_end_port_code, event_end_time, event_start_port_code, event_start_time, lookup_distance
from ocam.validation import can_follow_event

from .types import CoverageContext, PoolPruneStats

def _load_gurobi():
    try:
        import gurobipy as gp
        from gurobipy import GRB
    except ImportError as exc:
        raise RuntimeError(
            "wsgoh/mip_twostage_v10_numpy requires gurobipy at runtime. "
            "Install Gurobi's Python package or activate an environment with gurobipy available."
        ) from exc
    return gp, GRB

def _format_seed_result_lines(
    seed_results: Iterable[VariantResult],
    seed_costs: dict[str, float],
    canonical_seed_name: str,
) -> str:
    lines = []
    for result in seed_results:
        marker = "*" if result.variant.name == canonical_seed_name else "-"
        lines.append(
            "  "
            f"{marker} {result.variant.name}: "
            f"cost={seed_costs[result.variant.name]:.6f}, "
            f"virtual PortStay={result.metrics.virtual_portstay_count}, "
            f"virtual vessels={result.metrics.virtual_vessel_count}, "
            f"declared={len(result.metrics.declared_position_signature)}"
        )
    return "\n".join(lines)

def _build_coverage_context(instance_data: InstanceData) -> CoverageContext:
    fixed_positions = _fixed_position_keys(instance_data)
    selectable_positions = _selectable_position_keys(instance_data)
    coverage_positions = fixed_positions | selectable_positions
    coverage_schedules = _position_schedules(coverage_positions, instance_data)
    selectable_schedules = {
        position_key: coverage_schedules[position_key]
        for position_key in sorted(selectable_positions)
    }
    fixed_coverage: set[CoverageKey] = set()
    for position_key in sorted(fixed_positions):
        fixed_coverage.update(_schedule_coverage_keys(coverage_schedules[position_key], {position_key}))
    selectable_coverage: dict[CoverageKey, PositionKey] = {}
    for position_key, schedule in sorted(selectable_schedules.items()):
        for coverage_key in _schedule_coverage_keys(schedule, {position_key}):
            selectable_coverage[coverage_key] = position_key

    return CoverageContext(
        fixed_positions=fixed_positions,
        selectable_positions=selectable_positions,
        coverage_positions=coverage_positions,
        initial_selectable_values={position_key: 0 for position_key in sorted(selectable_positions)},
        coverage_schedules=coverage_schedules,
        selectable_schedules=selectable_schedules,
        fixed_coverage=fixed_coverage,
        selectable_coverage=selectable_coverage,
        required_coverage=fixed_coverage | set(selectable_coverage),
        declared_positions_payload=_declared_position_payload(selectable_positions),
    )

def _gurobi_name(prefix: str, value: object, max_length: int = 240) -> str:
    safe_value = _safe_name(value)
    name = f"{prefix}_{safe_value}"
    if len(name) <= max_length:
        return name
    digest = blake2b(name.encode("utf-8"), digest_size=8).hexdigest()
    head_length = max_length - len(prefix) - len(digest) - 2
    return f"{prefix}_{safe_value[:head_length]}_{digest}"

def _family_label(pattern: Pattern | str) -> str:
    pattern_id = pattern.pattern_id if isinstance(pattern, Pattern) else pattern
    family = _pattern_family(pattern_id)
    if family.startswith("actual-candidate"):
        return "actual-candidate"
    if family.startswith("actual-primary-virtual"):
        return "actual-primary-virtual"
    if family == "actual-empty":
        return "actual-empty"
    if family.startswith("cascade_chain_actual"):
        return "cascade-chain-actual"
    if family.startswith("cascade_chain_virtual_target_prefix"):
        return "virtual-prefix"
    if family.startswith("cascade_chain_virtual_source"):
        return "virtual-suffix"
    if family == "virtual-fallback":
        return "virtual-full"
    if family == "virtual":
        return "baseline virtual"
    return family

def _family_counts(patterns: Iterable[Pattern]) -> Counter[str]:
    return Counter(_family_label(pattern) for pattern in patterns)

def _coverage_impossibility_summary(context: CoverageContext, patterns: list[Pattern]) -> str:
    patterns_by_coverage: dict[CoverageKey, int] = {}
    for pattern in patterns:
        for coverage_key in pattern.coverage_keys:
            patterns_by_coverage[coverage_key] = patterns_by_coverage.get(coverage_key, 0) + 1

    fixed_missing = [key for key in sorted(context.fixed_coverage) if patterns_by_coverage.get(key, 0) == 0]
    selectable_positions_without_any = []
    for position_key, schedule in sorted(context.selectable_schedules.items()):
        coverage_keys = _schedule_coverage_keys(schedule, {position_key})
        if coverage_keys and all(patterns_by_coverage.get(key, 0) == 0 for key in coverage_keys):
            selectable_positions_without_any.append(position_key)
    return (
        f"fixed_missing_events={len(fixed_missing)}, "
        f"selectable_positions_without_any={len(selectable_positions_without_any)}"
    )

def _policy_status(GRB, model, status: str) -> str:
    if model.SolCount > 0:
        if model.Status == GRB.TIME_LIMIT:
            return "TIME_LIMIT_WITH_INCUMBENT"
        return "FEASIBLE"
    if status in {"INFEASIBLE", "INF_OR_UNBD"}:
        return "INFEASIBLE"
    if model.Status == GRB.TIME_LIMIT:
        return "TIME_LIMIT_WITHOUT_INCUMBENT"
    return status

def _virtual_portstay_count(solution: CascadingSolution) -> int:
    return sum(
        1
        for schedule in solution.virtual_vessel_schedules.values()
        for event in schedule
        if isinstance(event, PortStay)
    )

def _schedule_is_consistent(schedule: list[VesselScheduleEvent]) -> bool:
    if not schedule:
        return False
    for previous, current in zip(schedule, schedule[1:]):
        if event_end_time(previous) > event_start_time(current):
            return False
        if event_end_time(previous) == event_start_time(current) and event_end_port_code(previous) == event_start_port_code(current):
            continue
        if not can_follow_event(previous, current):
            return False
    return True

def _connection_events(
    from_port: str,
    from_time,
    to_port: str,
    to_time,
) -> list[VesselScheduleEvent]:
    if from_time >= to_time:
        return []
    return [
        mip_utility_solver.out_sail_or_idle(
            from_port_code=from_port,
            sea_sail_start=from_time,
            sea_sail_end=to_time,
            to_port_code=to_port,
        )
    ]

def _can_reposition_ports(from_port: str, from_time, to_port: str, to_time) -> bool:
    if from_port == to_port:
        return from_time <= to_time
    if from_time >= to_time:
        return False
    duration_hours = (to_time - from_time).total_seconds() / 3600
    if duration_hours <= 0:
        return False
    return lookup_distance(from_port, to_port) / (duration_hours + 1e-5) <= 20

def _remaining_seconds(start_time: float, timelimit: int) -> float:
    if timelimit <= 0:
        return 60.0
    return max(0.0, float(timelimit) - (time.monotonic() - start_time))

def _format_selected_virtual_coverage(patterns: list[Pattern]) -> str:
    virtual_patterns = [pattern for pattern in patterns if pattern.is_virtual]
    if not virtual_patterns:
        return "(none)"
    return "; ".join(
        f"{pattern.pattern_id} -> {_format_coverage_positions(pattern)}"
        for pattern in sorted(virtual_patterns, key=lambda item: item.pattern_id)
    )

def _format_slack_entries(entries: list[tuple[CoverageKey, float]]) -> str:
    if not entries:
        return "(none)"
    return "; ".join(
        f"{key[0]}/{key[1]}/{key[2]}#{key[3]}@{key[5]}={value:.3g}"
        for key, value in entries
    )

def _log_prune_stats(label: str, stats: PoolPruneStats) -> str:
    return (
        f"{label}: input={stats.input_count}, retained={stats.retained_count}, "
        f"schedule_dup={stats.schedule_duplicate_pruned}, coverage_dup={stats.coverage_duplicate_pruned}, "
        f"vessel_cap={stats.vessel_cap_pruned}, total_cap={stats.total_cap_pruned}, orphan={stats.orphan_pruned}"
    )
