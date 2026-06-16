from __future__ import annotations

import copy
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from types import ModuleType
from typing import Iterable

from algorithms.yongs.only_virtual import solver as only_virtual1_solver
from algorithms.yongs.only_virtual2 import solver as only_virtual2_solver
from ocam.models import (
    CascadingSolution,
    DeclaredPosition,
    Delivery,
    DryDock,
    Idle,
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
from ocam.utils import event_end_port_code, event_end_time, event_start_port_code, event_start_time
from ocam.validation import validate_solution

DESCRIPTION = (
    "Yongs only_virtual1/only_virtual2 seed-variant wrapper. "
    "Runs declared-position variants and returns the best low-virtual heuristic solution."
)
ALLOW_REVERSE_TIME_FALLBACK = os.environ.get("OCAM_HEURISTIC_YONGS_ALLOW_REVERSE_FALLBACK", "").strip() in {
    "1",
    "true",
    "TRUE",
    "yes",
    "YES",
}


@dataclass(frozen=True)
class HeuristicVariant:
    name: str
    base: str
    position_strategy: str
    seed: int
    description: str
    reverse_time: bool = False


@dataclass(frozen=True)
class VariantMetrics:
    virtual_vessel_count: int
    virtual_portstay_count: int
    actual_vessel_count: int
    declared_position_signature: tuple[tuple[str, str, int], ...]

    def score(self) -> tuple[int, int, int]:
        return (
            self.virtual_portstay_count,
            self.virtual_vessel_count,
            -self.actual_vessel_count,
        )


@dataclass(frozen=True)
class VariantResult:
    variant: HeuristicVariant
    solution: CascadingSolution
    metrics: VariantMetrics


@dataclass(frozen=True)
class PreServiceDrydock:
    vessel_code: str
    start_port_code: str
    dock_in: datetime
    dock_out: datetime
    dock_port_code: str
    first_service_time: datetime
    first_service_port_code: str


@dataclass(frozen=True)
class ReversePrefixRepair:
    vessel_code: str
    lane_code: str
    proforma_name: str
    position_no: int
    actual_schedule: list[VesselScheduleEvent]
    virtual_schedule: list[VesselScheduleEvent]


@dataclass(frozen=True)
class UnassignedRedeliveryRepair:
    vessel_code: str
    available_from: datetime
    available_from_port_code: str
    available_to: datetime
    available_to_port_code: str


VARIANTS: tuple[HeuristicVariant, ...] = (
    HeuristicVariant(
        name="only_virtual1_lowest",
        base="only_virtual1",
        position_strategy="lowest",
        seed=0,
        description="Original yongs/only_virtual behavior: choose the lowest available positions.",
    ),
    HeuristicVariant(
        name="only_virtual1_highest",
        base="only_virtual1",
        position_strategy="highest",
        seed=0,
        description="Choose the highest available positions before running only_virtual1.",
    ),
    HeuristicVariant(
        name="only_virtual1_spread",
        base="only_virtual1",
        position_strategy="spread",
        seed=0,
        description="Spread declared positions across the available position cycle.",
    ),
    HeuristicVariant(
        name="only_virtual1_offset_1",
        base="only_virtual1",
        position_strategy="offset",
        seed=1,
        description="Use a cyclic declared-position window shifted by one slot.",
    ),
    HeuristicVariant(
        name="only_virtual1_offset_2",
        base="only_virtual1",
        position_strategy="offset",
        seed=2,
        description="Use a cyclic declared-position window shifted by two slots.",
    ),
    HeuristicVariant(
        name="only_virtual2_lowest",
        base="only_virtual2",
        position_strategy="lowest",
        seed=0,
        description="Original yongs/only_virtual2 behavior: lowest positions plus surplus-vessel replacement.",
    ),
    HeuristicVariant(
        name="only_virtual2_highest",
        base="only_virtual2",
        position_strategy="highest",
        seed=0,
        description="Choose highest available positions, then run only_virtual2 surplus replacement.",
    ),
    HeuristicVariant(
        name="only_virtual2_spread",
        base="only_virtual2",
        position_strategy="spread",
        seed=0,
        description="Spread declared positions, then run only_virtual2 surplus replacement.",
    ),
    HeuristicVariant(
        name="only_virtual2_offset_1",
        base="only_virtual2",
        position_strategy="offset",
        seed=1,
        description="Use a shifted declared-position window before only_virtual2 surplus replacement.",
    ),
    HeuristicVariant(
        name="only_virtual2_offset_2",
        base="only_virtual2",
        position_strategy="offset",
        seed=2,
        description="Use a second shifted declared-position window before only_virtual2 surplus replacement.",
    ),
)

REVERSE_TIME_VARIANTS: tuple[HeuristicVariant, ...] = (
    HeuristicVariant(
        name="only_virtual1_reverse_time_lowest",
        base="only_virtual1",
        position_strategy="lowest",
        seed=0,
        description="Mirror the horizon, run only_virtual1 on lowest positions, then mirror the solution back.",
        reverse_time=True,
    ),
    HeuristicVariant(
        name="only_virtual1_reverse_time_highest",
        base="only_virtual1",
        position_strategy="highest",
        seed=0,
        description="Mirror the horizon, run only_virtual1 on highest positions, then mirror the solution back.",
        reverse_time=True,
    ),
    HeuristicVariant(
        name="only_virtual1_reverse_time_spread",
        base="only_virtual1",
        position_strategy="spread",
        seed=0,
        description="Mirror the horizon, run only_virtual1 on spread positions, then mirror the solution back.",
        reverse_time=True,
    ),
    HeuristicVariant(
        name="only_virtual1_reverse_time_offset_1",
        base="only_virtual1",
        position_strategy="offset",
        seed=1,
        description="Mirror the horizon, run only_virtual1 on offset_1 positions, then mirror the solution back.",
        reverse_time=True,
    ),
    HeuristicVariant(
        name="only_virtual1_reverse_time_offset_2",
        base="only_virtual1",
        position_strategy="offset",
        seed=2,
        description="Mirror the horizon, run only_virtual1 on offset_2 positions, then mirror the solution back.",
        reverse_time=True,
    ),
    HeuristicVariant(
        name="only_virtual2_reverse_time_lowest",
        base="only_virtual2",
        position_strategy="lowest",
        seed=0,
        description="Mirror the horizon, run only_virtual2 on lowest positions, then mirror the solution back.",
        reverse_time=True,
    ),
    HeuristicVariant(
        name="only_virtual2_reverse_time_highest",
        base="only_virtual2",
        position_strategy="highest",
        seed=0,
        description="Mirror the horizon, run only_virtual2 on highest positions, then mirror the solution back.",
        reverse_time=True,
    ),
    HeuristicVariant(
        name="only_virtual2_reverse_time_spread",
        base="only_virtual2",
        position_strategy="spread",
        seed=0,
        description="Mirror the horizon, run only_virtual2 on spread positions, then mirror the solution back.",
        reverse_time=True,
    ),
    HeuristicVariant(
        name="only_virtual2_reverse_time_offset_1",
        base="only_virtual2",
        position_strategy="offset",
        seed=1,
        description="Mirror the horizon, run only_virtual2 on offset_1 positions, then mirror the solution back.",
        reverse_time=True,
    ),
    HeuristicVariant(
        name="only_virtual2_reverse_time_offset_2",
        base="only_virtual2",
        position_strategy="offset",
        seed=2,
        description="Mirror the horizon, run only_virtual2 on offset_2 positions, then mirror the solution back.",
        reverse_time=True,
    ),
)

ALL_VARIANTS: tuple[HeuristicVariant, ...] = (*VARIANTS, *REVERSE_TIME_VARIANTS)


SOLVER_BY_BASE: dict[str, ModuleType] = {
    "only_virtual1": only_virtual1_solver,
    "only_virtual2": only_virtual2_solver,
}


def algorithm(instance_data: InstanceData, timelimit: int) -> CascadingSolution:
    results = generate_variant_results(instance_data, timelimit)
    if not results:
        raise RuntimeError("heuristic_yongs: no seed variant produced a solution.")

    best_index, best = min(enumerate(results), key=lambda item: (item[1].metrics.score(), item[0]))
    print(
        "heuristic_yongs selected "
        f"{best.variant.name}: virtual_portstays={best.metrics.virtual_portstay_count}, "
        f"virtual_vessels={best.metrics.virtual_vessel_count}, "
        f"variant_index={best_index}"
    )
    return best.solution


def generate_seed_solutions(
    instance_data: InstanceData,
    timelimit: int,
    variant_names: Iterable[str] | None = None,
) -> dict[str, CascadingSolution]:
    return {
        result.variant.name: result.solution
        for result in generate_variant_results(instance_data, timelimit, variant_names=variant_names)
    }


def generate_variant_results(
    instance_data: InstanceData,
    timelimit: int,
    variant_names: Iterable[str] | None = None,
    *,
    fail_fast: bool = False,
) -> list[VariantResult]:
    selected_variants = _select_variants(variant_names)
    results: list[VariantResult] = []
    for variant in selected_variants:
        try:
            solution = _run_variant(instance_data, timelimit, variant)
        except Exception as exc:
            if fail_fast:
                raise
            print(f"heuristic_yongs skipped {variant.name}: {type(exc).__name__}: {exc}")
            continue

        metrics = _measure_solution(solution)
        results.append(VariantResult(variant=variant, solution=solution, metrics=metrics))
        print(
            "heuristic_yongs variant "
            f"{variant.name}: virtual_portstays={metrics.virtual_portstay_count}, "
            f"virtual_vessels={metrics.virtual_vessel_count}, "
            f"declared={len(metrics.declared_position_signature)}"
        )
    return results


def _select_variants(variant_names: Iterable[str] | None) -> tuple[HeuristicVariant, ...]:
    if variant_names is None:
        env_value = os.environ.get("OCAM_HEURISTIC_YONGS_VARIANTS", "").strip()
        variant_names = [name.strip() for name in env_value.split(",") if name.strip()] if env_value else None

    if variant_names is None:
        return VARIANTS

    by_name = {variant.name: variant for variant in ALL_VARIANTS}
    selected = []
    unknown = []
    for name in variant_names:
        variant = by_name.get(name)
        if variant is None:
            unknown.append(name)
        else:
            selected.append(variant)

    if unknown:
        raise ValueError(
            "heuristic_yongs: unknown variant name(s) "
            f"{unknown!r}. Available variants: {sorted(by_name)}"
        )
    return tuple(selected)


def _run_variant(instance_data: InstanceData, timelimit: int, variant: HeuristicVariant) -> CascadingSolution:
    solver = SOLVER_BY_BASE[variant.base]
    if variant.reverse_time:
        try:
            mirror_instance = _mirror_instance(instance_data)
            _apply_position_strategy(mirror_instance, variant.position_strategy, variant.seed)
            (
                solver_instance,
                pre_service_drydocks,
                reverse_prefix_repairs,
                unassigned_redelivery_repairs,
            ) = _prepare_reverse_solver_instance(mirror_instance)
            _reset_yongs_solver(solver)
            mirror_solution = solver.algorithm(solver_instance, timelimit)
            if unassigned_redelivery_repairs:
                _apply_unassigned_redelivery_repairs(mirror_solution, unassigned_redelivery_repairs)
            if reverse_prefix_repairs:
                _apply_reverse_prefix_repairs(mirror_solution, reverse_prefix_repairs)
            if pre_service_drydocks:
                _insert_pre_service_drydocks(mirror_solution, pre_service_drydocks, mirror_instance)
            solution = _mirror_solution_back(mirror_solution, instance_data)
            _repair_solution_boundary_gaps(solution, instance_data)
            _repair_current_assignment_starts(solution, instance_data)
            _repair_missing_declared_virtual_services(solution, instance_data)
            _trim_pre_service_virtual_prefixes(solution, instance_data)
            _extend_virtual_service_suffixes(solution, instance_data)
            _trim_post_service_suffixes(solution, instance_data)
            _repair_solution_boundary_gaps(solution, instance_data)
            validate_solution(solution, instance_data)
            return solution
        except Exception as exc:
            if not ALLOW_REVERSE_TIME_FALLBACK:
                raise RuntimeError(
                    "heuristic_yongs pure reverse-time mirror failed "
                    f"{variant.name}: {type(exc).__name__}: {exc}"
                ) from exc
            print(
                "heuristic_yongs reverse-time mirror fallback "
                f"{variant.name}: {type(exc).__name__}: {exc}"
            )
            fallback_instance = copy.deepcopy(instance_data)
            _apply_reverse_position_strategy(fallback_instance, variant.position_strategy, variant.seed)
            _reset_yongs_solver(solver)
            return solver.algorithm(fallback_instance, timelimit)

    variant_instance = copy.deepcopy(instance_data)
    _apply_position_strategy(variant_instance, variant.position_strategy, variant.seed)
    _reset_yongs_solver(solver)
    return solver.algorithm(variant_instance, timelimit)


def _reset_yongs_solver(solver: ModuleType) -> None:
    # yongs solvers keep virtual-vessel counters in module globals.
    # Multiple seed runs in one Python process must start from a clean state.
    solver.NUM_VIRTUAL_VESSELS_USED = 0
    solver.USED_VIRTUAL_VESSEL_CODES = set()


def _mirror_instance(instance_data: InstanceData) -> InstanceData:
    mirrored = copy.deepcopy(instance_data)
    start = instance_data.planning_horizon["start"]
    end = instance_data.planning_horizon["end"]
    mirrored.planning_horizon = {"start": start, "end": end}
    mirrored.service_lanes = [_mirror_lane(lane, start, end) for lane in instance_data.service_lanes]
    mirrored.vessels = [_mirror_vessel(vessel, instance_data, start, end) for vessel in instance_data.vessels]
    mirrored.distances = [_mirror_distance(row) for row in instance_data.distances]
    return mirrored


def _prepare_reverse_solver_instance(
    mirror_instance: InstanceData,
) -> tuple[
    InstanceData,
    dict[str, PreServiceDrydock],
    dict[str, ReversePrefixRepair],
    dict[str, UnassignedRedeliveryRepair],
]:
    solver_instance = copy.deepcopy(mirror_instance)
    pre_service_drydocks: dict[str, PreServiceDrydock] = {}
    reverse_prefix_repairs: dict[str, ReversePrefixRepair] = {}
    unassigned_redelivery_repairs: dict[str, UnassignedRedeliveryRepair] = {}

    # Pure horizon mirroring can move an originally future dry-dock before the
    # mirrored current-assignment service. The original Yongs solver cannot
    # represent "dry-dock first, then continue current assignment", so we remove
    # that dry-dock for the internal run and splice it back into the vessel
    # prefix afterwards.
    only_virtual1_solver.init_solver_globals(mirror_instance)
    for vessel in mirror_instance.vessels:
        current_assignment = vessel.get("current_assignment")
        dock_in = vessel.get("next_dock_in")
        dock_out = vessel.get("next_dock_out")
        dock_port_code = vessel.get("next_dock_port_code")
        available_from = vessel.get("available_from")
        available_from_port_code = vessel.get("available_from_port_code")
        if current_assignment is None:
            continue

        schedule = only_virtual1_solver.make_inlane_schedule_for_vessel(vessel["vessel_code"])
        first_service_event = only_virtual1_solver.first_inlane_event(schedule)
        first_service_time = event_start_time(first_service_event)

        if (
            available_from is not None
            and available_from_port_code is not None
            and mirror_instance.planning_horizon["start"] <= available_from <= mirror_instance.planning_horizon["end"]
            and available_from > first_service_time
        ):
            repair = _build_reverse_prefix_repair(mirror_instance, vessel, schedule, repair_kind="delivery")
            if repair is not None:
                reverse_prefix_repairs[vessel["vessel_code"]] = repair
                continue

        if dock_in is None or dock_out is None or dock_port_code is None:
            continue

        if dock_in > mirror_instance.planning_horizon["end"]:
            continue

        target_time = dock_in
        target_port_code = dock_port_code
        try:
            only_virtual1_solver.split_inlane_schedule(copy.deepcopy(schedule), target_time, target_port_code)
        except ValueError:
            repair = _build_reverse_prefix_repair(mirror_instance, vessel, schedule, repair_kind="drydock")
            if repair is not None:
                reverse_prefix_repairs[vessel["vessel_code"]] = repair
                continue

        first_service_port_code = event_start_port_code(first_service_event)
        start_port_code = vessel.get("available_from_port_code") or first_service_port_code

        if dock_in >= first_service_time or dock_out > first_service_time:
            continue
        if not _can_sail_between(mirror_instance, start_port_code, dock_port_code, mirror_instance.planning_horizon["start"], dock_in):
            continue
        if not _can_sail_between(mirror_instance, dock_port_code, first_service_port_code, dock_out, first_service_time):
            continue

        vessel_code = vessel["vessel_code"]
        pre_service_drydocks[vessel_code] = PreServiceDrydock(
            vessel_code=vessel_code,
            start_port_code=start_port_code,
            dock_in=dock_in,
            dock_out=dock_out,
            dock_port_code=dock_port_code,
            first_service_time=first_service_time,
            first_service_port_code=first_service_port_code,
        )

    for vessel in solver_instance.vessels:
        if vessel["vessel_code"] not in pre_service_drydocks and vessel["vessel_code"] not in reverse_prefix_repairs:
            continue
        vessel["next_dock_in"] = None
        vessel["next_dock_out"] = None
        vessel["next_dock_port_code"] = None

    for vessel in solver_instance.vessels:
        if vessel.get("current_assignment") is not None or vessel.get("available_to") is None:
            continue
        if vessel.get("available_from") is None or vessel.get("available_from_port_code") is None:
            continue
        unassigned_redelivery_repairs[vessel["vessel_code"]] = UnassignedRedeliveryRepair(
            vessel_code=vessel["vessel_code"],
            available_from=vessel["available_from"],
            available_from_port_code=vessel["available_from_port_code"],
            available_to=vessel["available_to"],
            available_to_port_code=vessel["available_to_port_code"],
        )
        vessel["available_to"] = None
        vessel["available_to_port_code"] = None

    for repair in reverse_prefix_repairs.values():
        _remove_vessel_assignment(
            solver_instance,
            repair.lane_code,
            repair.proforma_name,
            repair.position_no,
            repair.vessel_code,
        )

    return solver_instance, pre_service_drydocks, reverse_prefix_repairs, unassigned_redelivery_repairs


def _apply_unassigned_redelivery_repairs(
    solution: CascadingSolution,
    repairs: dict[str, UnassignedRedeliveryRepair],
) -> None:
    for repair in repairs.values():
        solution.vessel_schedules[repair.vessel_code] = [
            Idle(
                port_code=repair.available_to_port_code,
                idle_start=repair.available_from,
                idle_end=repair.available_to,
            ),
            Redelivery(
                redelivery_time=repair.available_to,
                redelivery_port_code=repair.available_to_port_code,
            ),
        ]


def _build_reverse_prefix_repair(
    mirror_instance: InstanceData,
    vessel: dict,
    schedule: list[VesselScheduleEvent],
    *,
    repair_kind: str,
) -> ReversePrefixRepair | None:
    current_assignment = vessel.get("current_assignment")
    dock_in = vessel.get("next_dock_in")
    dock_out = vessel.get("next_dock_out")
    dock_port_code = vessel.get("next_dock_port_code")
    available_from = vessel.get("available_from")
    available_from_port_code = vessel.get("available_from_port_code")
    if current_assignment is None:
        return None

    planning_start = mirror_instance.planning_horizon["start"]
    planning_end = mirror_instance.planning_horizon["end"]
    first_service_port_code = event_start_port_code(only_virtual1_solver.first_inlane_event(schedule))
    if repair_kind == "drydock":
        if dock_in is None or dock_out is None or dock_port_code is None:
            return None
        ready_time = dock_out
        ready_port_code = dock_port_code
        start_port_code = available_from_port_code or first_service_port_code
        if not _can_sail_between(mirror_instance, start_port_code, dock_port_code, planning_start, dock_in):
            return None
    elif repair_kind == "delivery":
        if available_from is None or available_from_port_code is None:
            return None
        ready_time = available_from
        ready_port_code = available_from_port_code
    else:
        raise ValueError(f"heuristic_yongs: unknown reverse prefix repair kind {repair_kind!r}.")

    lane_code = current_assignment["lane_code"]
    proforma_name = current_assignment["proforma_name"]
    position_no = current_assignment["position_no"]

    first_lane_index = next(
        (
            index
            for index, event in enumerate(schedule)
            if isinstance(event, (PhaseIn, PortStay, InLaneSail, PhaseOut))
        ),
        None,
    )
    if first_lane_index is None:
        return None

    ts_work = timedelta(hours=only_virtual1_solver.TS_WORK_HOUR)
    ts_slack = timedelta(hours=only_virtual1_solver.TS_SLACK_HOUR)
    handover_index = None
    handover_mode = None
    handover_times = None
    for port_stay_index, port_stay in enumerate(schedule):
        if not isinstance(port_stay, PortStay):
            continue
        if port_stay.port_code in ("EGSUZ", "EGSCA", "PAPCA"):
            continue

        if port_stay_index + 1 < len(schedule):
            next_event = schedule[port_stay_index + 1]
            if isinstance(next_event, InLaneSail):
                unload_start = port_stay.pilot_out_end
                unload_end = unload_start + ts_work
                phase_in_time = unload_end + ts_slack
                load_end = phase_in_time + ts_work
                if phase_in_time > ready_time and _can_sail_between(
                    mirror_instance,
                    ready_port_code,
                    port_stay.port_code,
                    ready_time,
                    phase_in_time,
                ):
                    actual_sail_hours = (next_event.sea_sail_end - load_end).total_seconds() / 3600
                    if actual_sail_hours > 0 and next_event.distance / (actual_sail_hours + 1e-5) <= 20:
                        handover_index = port_stay_index
                        handover_mode = "after_portstay"
                        handover_times = (unload_start, unload_end, phase_in_time, load_end)
                        break

        if port_stay_index == 0:
            continue
        prev_event = schedule[port_stay_index - 1]
        if not isinstance(prev_event, InLaneSail):
            continue

        unload_start = port_stay.pilot_in_start - (ts_work + ts_slack + ts_work)
        unload_end = unload_start + ts_work
        phase_in_time = unload_end + ts_slack
        load_end = phase_in_time + ts_work
        if load_end != port_stay.pilot_in_start:
            continue
        if phase_in_time <= ready_time:
            continue
        if not _can_sail_between(mirror_instance, ready_port_code, port_stay.port_code, ready_time, phase_in_time):
            continue

        virtual_sail_hours = (unload_start - prev_event.sea_sail_start).total_seconds() / 3600
        if virtual_sail_hours <= 0:
            continue
        if prev_event.distance / (virtual_sail_hours + 1e-5) > 20:
            continue

        handover_index = port_stay_index
        handover_mode = "before_portstay"
        handover_times = (unload_start, unload_end, phase_in_time, load_end)
        break

    if handover_index is None or handover_mode is None or handover_times is None:
        return None

    unload_start, unload_end, phase_in_time, load_end = handover_times
    port_stay = schedule[handover_index]
    if handover_mode == "after_portstay":
        virtual_schedule = copy.deepcopy(schedule[first_lane_index:handover_index + 1])
        tail_schedule = copy.deepcopy(schedule[handover_index + 1:])
        if not tail_schedule or not isinstance(tail_schedule[0], InLaneSail):
            return None
        tail_schedule[0].sea_sail_start = load_end
        duration_hours = (tail_schedule[0].sea_sail_end - tail_schedule[0].sea_sail_start).total_seconds() / 3600
        if duration_hours <= 0:
            return None
        tail_schedule[0].avg_speed = tail_schedule[0].distance / duration_hours
    else:
        virtual_schedule = copy.deepcopy(schedule[first_lane_index:handover_index])
        if not virtual_schedule or not isinstance(virtual_schedule[-1], InLaneSail):
            return None
        virtual_schedule[-1].sea_sail_end = unload_start
        duration_hours = (virtual_schedule[-1].sea_sail_end - virtual_schedule[-1].sea_sail_start).total_seconds() / 3600
        if duration_hours <= 0:
            return None
        virtual_schedule[-1].avg_speed = virtual_schedule[-1].distance / duration_hours
        tail_schedule = copy.deepcopy(schedule[handover_index:])
    virtual_schedule.extend(
        [
            TransshipmentUnload(
                lane_code=lane_code,
                proforma_name=proforma_name,
                position_no=position_no,
                ts_port_code=port_stay.port_code,
                ts_port_seq=port_stay.port_seq,
                unload_start=unload_start,
                unload_end=unload_end,
            ),
            PhaseOut(
                lane_code=lane_code,
                proforma_name=proforma_name,
                position_no=position_no,
                phase_out_port_code=port_stay.port_code,
                phase_out_port_seq=port_stay.port_seq,
                phase_out_time=unload_end,
            ),
        ]
    )

    actual_schedule: list[VesselScheduleEvent] = []
    if repair_kind == "drydock":
        actual_schedule.extend(_make_connection_events(start_port_code, dock_port_code, planning_start, dock_in))
        actual_schedule.append(
            DryDock(
                dock_in=dock_in,
                dock_port_code=dock_port_code,
                dock_out=dock_out,
            )
        )
    else:
        actual_schedule.append(
            Delivery(
                delivery_time=available_from,
                delivery_port_code=available_from_port_code,
            )
        )
    actual_schedule.extend(_make_connection_events(ready_port_code, port_stay.port_code, ready_time, phase_in_time))
    actual_schedule.extend(
        [
            PhaseIn(
                lane_code=lane_code,
                proforma_name=proforma_name,
                position_no=position_no,
                phase_in_port_code=port_stay.port_code,
                phase_in_port_seq=port_stay.port_seq,
                phase_in_time=phase_in_time,
            ),
            TransshipmentLoad(
                lane_code=lane_code,
                proforma_name=proforma_name,
                position_no=position_no,
                ts_port_code=port_stay.port_code,
                ts_port_seq=port_stay.port_seq,
                load_start=phase_in_time,
                load_end=load_end,
            ),
        ]
    )
    actual_schedule.extend(tail_schedule)
    last_end = event_end_time(actual_schedule[-1])
    if last_end < planning_end:
        actual_schedule.append(
            Idle(
                port_code=event_end_port_code(actual_schedule[-1]),
                idle_start=last_end,
                idle_end=planning_end,
            )
        )

    return ReversePrefixRepair(
        vessel_code=vessel["vessel_code"],
        lane_code=lane_code,
        proforma_name=proforma_name,
        position_no=position_no,
        actual_schedule=actual_schedule,
        virtual_schedule=virtual_schedule,
    )


def _remove_vessel_assignment(
    instance_data: InstanceData,
    lane_code: str,
    proforma_name: str,
    position_no: int,
    vessel_code: str,
) -> None:
    for lane in instance_data.service_lanes:
        if lane["lane_code"] != lane_code:
            continue
        for version in lane["versions"]:
            if version["proforma_name"] != proforma_name:
                continue
            version["vessel_assignments"] = [
                assignment
                for assignment in version.get("vessel_assignments") or []
                if not (
                    assignment.get("position_no") == position_no
                    and assignment.get("vessel_code") == vessel_code
                )
            ]
            return


def _apply_reverse_prefix_repairs(
    solution: CascadingSolution,
    repairs: dict[str, ReversePrefixRepair],
) -> None:
    used_codes = set(solution.virtual_vessel_schedules.keys())
    next_index = max(
        [solution.num_virtual_vessels_used]
        + [
            int(code.removeprefix("VIRTUAL"))
            for code in used_codes
            if code.startswith("VIRTUAL") and code.removeprefix("VIRTUAL").isdigit()
        ]
    )
    for repair in repairs.values():
        solution.vessel_schedules[repair.vessel_code] = repair.actual_schedule
        next_index += 1
        virtual_code = f"VIRTUAL{next_index:03d}"
        while virtual_code in used_codes:
            next_index += 1
            virtual_code = f"VIRTUAL{next_index:03d}"
        used_codes.add(virtual_code)
        solution.virtual_vessel_schedules[virtual_code] = repair.virtual_schedule
    solution.num_virtual_vessels_used = max(solution.num_virtual_vessels_used, next_index)


def _insert_pre_service_drydocks(
    solution: CascadingSolution,
    pre_service_drydocks: dict[str, PreServiceDrydock],
    mirror_instance: InstanceData,
) -> None:
    planning_start = mirror_instance.planning_horizon["start"]
    for vessel_code, drydock in pre_service_drydocks.items():
        schedule = solution.vessel_schedules.get(vessel_code)
        if schedule is None:
            continue

        events = list(schedule.events)
        first_lane_index = next(
            (
                index
                for index, event in enumerate(events)
                if isinstance(
                    event,
                    (
                        PhaseIn,
                        PortStay,
                        InLaneSail,
                        PhaseOut,
                        TransshipmentLoad,
                        TransshipmentUnload,
                    ),
                )
            ),
            None,
        )
        if first_lane_index is None:
            continue

        tail = events[first_lane_index:]
        prefix: list[VesselScheduleEvent] = []
        prefix.extend(
            _make_connection_events(
                drydock.start_port_code,
                drydock.dock_port_code,
                planning_start,
                drydock.dock_in,
            )
        )
        prefix.append(
            DryDock(
                dock_in=drydock.dock_in,
                dock_port_code=drydock.dock_port_code,
                dock_out=drydock.dock_out,
            )
        )
        prefix.extend(
            _make_connection_events(
                drydock.dock_port_code,
                drydock.first_service_port_code,
                drydock.dock_out,
                drydock.first_service_time,
            )
        )
        solution.vessel_schedules[vessel_code] = prefix + tail


def _make_connection_events(
    from_port_code: str,
    to_port_code: str,
    start_time: datetime,
    end_time: datetime,
) -> list[VesselScheduleEvent]:
    if end_time <= start_time:
        return []
    if from_port_code == to_port_code:
        return [
            Idle(
                port_code=from_port_code,
                idle_start=start_time,
                idle_end=end_time,
            )
        ]
    return [
        OutLaneSail(
            from_port_code=from_port_code,
            sea_sail_start=start_time,
            sea_sail_end=end_time,
            to_port_code=to_port_code,
        )
    ]


def _can_sail_between(
    instance_data: InstanceData,
    from_port_code: str,
    to_port_code: str,
    start_time: datetime,
    end_time: datetime,
) -> bool:
    if end_time <= start_time:
        return False
    if from_port_code == to_port_code:
        return True
    distance = _lookup_distance(instance_data, from_port_code, to_port_code)
    if distance is None:
        return False
    duration_hours = (end_time - start_time).total_seconds() / 3600
    return distance / duration_hours <= 20


def _lookup_distance(instance_data: InstanceData, from_port_code: str, to_port_code: str) -> float | None:
    for row in instance_data.distances:
        if row["from_port_code"] == from_port_code and row["to_port_code"] == to_port_code:
            return float(row["distance"])
    return None


def _mirror_lane(lane: dict, start: datetime, end: datetime) -> dict:
    mirrored_lane = copy.deepcopy(lane)
    mirrored_lane["versions"] = [_mirror_version(version, start, end) for version in lane["versions"]]
    return mirrored_lane


def _mirror_version(version: dict, start: datetime, end: datetime) -> dict:
    mirrored = copy.deepcopy(version)
    cycle_len = _position_cycle_length(version)
    duration = timedelta(days=int(version["service_duration"]))
    duration_minutes = int(duration.total_seconds() // 60)
    anchor_shift_rounds = _mirror_anchor_shift_rounds(version, cycle_len)

    mirrored["effective_from"] = _mirror_optional_datetime(version.get("effective_to"), start, end) or start
    mirrored["effective_to"] = _mirror_optional_datetime(version.get("effective_from"), start, end) or end
    mirrored["anchor_date"] = (
        _mirror_datetime(version["anchor_date"] + duration, start, end)
        - timedelta(days=7 * (cycle_len - 1))
        - anchor_shift_rounds * duration
    )
    mirrored["declared_positions"] = sorted(
        _mirror_position_no(position_no, cycle_len) for position_no in version.get("declared_positions") or []
    )
    mirrored["available_positions"] = sorted(
        _mirror_position_no(position_no, cycle_len) for position_no in version.get("available_positions") or []
    )
    mirrored["vessel_assignments"] = [
        {
            **assignment,
            "position_no": _mirror_position_no(assignment["position_no"], cycle_len),
        }
        for assignment in version.get("vessel_assignments") or []
    ]
    mirrored["port_rotation"] = [
        _mirror_rotation(rotation, duration_minutes) for rotation in reversed(version["port_rotation"])
    ]
    return mirrored


def _mirror_anchor_shift_rounds(version: dict, cycle_len: int) -> int:
    effective_to = version.get("effective_to")
    if effective_to is None:
        return 0

    positions = {
        int(position)
        for position in (
            list(version.get("declared_positions") or [])
            + [assignment["position_no"] for assignment in version.get("vessel_assignments") or []]
        )
    }
    if not positions:
        return 0

    duration = timedelta(days=int(version["service_duration"]))
    max_round_index = 0
    for position_no in positions:
        position_start = version["anchor_date"] + timedelta(days=7 * (int(position_no) - 1))
        if position_start >= effective_to:
            continue
        trip_count = 0
        while position_start + trip_count * duration < effective_to:
            trip_count += 1
        max_round_index = max(max_round_index, trip_count - 1)
    return max_round_index


def _mirror_rotation(rotation: dict, duration_minutes: int) -> dict:
    eta = int(rotation["eta_offset_minutes"])
    etb = int(rotation["etb_offset_minutes"])
    etd = int(rotation["etd_offset_minutes"])
    pilot_out = int(rotation["pilot_out_minutes"])
    pilot_in = int(rotation["pilot_in_minutes"])
    return {
        **copy.deepcopy(rotation),
        "eta_offset_minutes": duration_minutes - (etd + pilot_out),
        "etb_offset_minutes": duration_minutes - etd,
        "etd_offset_minutes": duration_minutes - etb,
        "pilot_in_minutes": pilot_out,
        "pilot_out_minutes": pilot_in,
        "berthing_minutes": int(rotation["berthing_minutes"]),
        "in_port_minutes": int(rotation["in_port_minutes"]),
    }


def _mirror_vessel(vessel: dict, instance_data: InstanceData, start: datetime, end: datetime) -> dict:
    mirrored = copy.deepcopy(vessel)
    current_assignment = mirrored.get("current_assignment")
    if current_assignment is not None:
        cycle_len = _version_cycle_length_by_key(
            instance_data,
            current_assignment["lane_code"],
            current_assignment["proforma_name"],
        )
        mirrored["current_assignment"] = {
            **current_assignment,
            "position_no": _mirror_position_no(current_assignment["position_no"], cycle_len),
        }

    available_from = vessel.get("available_from")
    available_to = vessel.get("available_to")
    mirrored["available_from"] = (
        _mirror_datetime(available_to, start, end)
        if available_to is not None and available_to <= end
        else None
    )
    mirrored["available_from_port_code"] = vessel.get("available_to_port_code") if mirrored["available_from"] else None
    mirrored["available_to"] = (
        _mirror_datetime(available_from, start, end)
        if available_from is not None and available_from >= start
        else None
    )
    mirrored["available_to_port_code"] = vessel.get("available_from_port_code") if mirrored["available_to"] else None
    if mirrored["available_to"] is not None and mirrored["available_to"] <= start:
        mirrored["available_to"] = None
        mirrored["available_to_port_code"] = None
    if mirrored["available_from_port_code"] is None and current_assignment is not None:
        mirrored["available_from_port_code"] = _mirror_assignment_start_port(instance_data, current_assignment)
    if mirrored["available_from"] is None and current_assignment is None:
        fallback_port_code = vessel.get("available_from_port_code") or vessel.get("available_to_port_code")
        if fallback_port_code is not None:
            mirrored["available_from"] = start
            mirrored["available_from_port_code"] = fallback_port_code

    next_dock_in = vessel.get("next_dock_in")
    next_dock_out = vessel.get("next_dock_out")
    mirrored["next_dock_in"] = _mirror_optional_datetime(next_dock_out, start, end)
    mirrored["next_dock_out"] = _mirror_optional_datetime(next_dock_in, start, end)
    if (
        mirrored["next_dock_in"] is not None
        and mirrored["next_dock_out"] is not None
        and (mirrored["next_dock_out"] <= start or mirrored["next_dock_in"] >= end)
    ):
        mirrored["next_dock_in"] = None
        mirrored["next_dock_out"] = None
        mirrored["next_dock_port_code"] = None
    return mirrored


def _mirror_assignment_start_port(instance_data: InstanceData, current_assignment: dict) -> str:
    for lane in instance_data.service_lanes:
        if lane["lane_code"] != current_assignment["lane_code"]:
            continue
        for version in lane["versions"]:
            if version["proforma_name"] == current_assignment["proforma_name"]:
                return version["port_rotation"][-1]["port_code"]
    raise ValueError(f"heuristic_yongs: missing current assignment version {current_assignment!r}.")


def _mirror_distance(row: dict) -> dict:
    mirrored = copy.deepcopy(row)
    mirrored["from_port_code"] = row["to_port_code"]
    mirrored["to_port_code"] = row["from_port_code"]
    return mirrored


def _mirror_solution_back(solution: CascadingSolution, original_instance: InstanceData) -> CascadingSolution:
    start = original_instance.planning_horizon["start"]
    end = original_instance.planning_horizon["end"]
    declared_positions = [
        DeclaredPosition(
            lane_code=position.lane_code,
            proforma_name=position.proforma_name,
            declared_position_no=_mirror_position_for_instance(
                original_instance,
                position.lane_code,
                position.proforma_name,
                position.declared_position_no,
            ),
        )
        for position in solution.declared_positions
    ]
    return CascadingSolution(
        declared_positions=declared_positions,
        vessel_schedules={
            vessel_code: _mirror_schedule_back(schedule, original_instance, start, end)
            for vessel_code, schedule in solution.vessel_schedules.items()
        },
        virtual_vessel_schedules={
            vessel_code: _mirror_schedule_back(schedule, original_instance, start, end)
            for vessel_code, schedule in solution.virtual_vessel_schedules.items()
        },
        num_virtual_vessels_used=solution.num_virtual_vessels_used,
    )


def _repair_solution_boundary_gaps(solution: CascadingSolution, instance_data: InstanceData) -> None:
    planning_start = instance_data.planning_horizon["start"]
    planning_end = instance_data.planning_horizon["end"]
    vessels_by_code = {vessel["vessel_code"]: vessel for vessel in instance_data.vessels}
    for vessel_code, vessel in vessels_by_code.items():
        schedule = solution.vessel_schedules.get(vessel_code)
        if schedule is None or not schedule.events:
            continue

        required_start = max(planning_start, vessel["available_from"]) if vessel.get("available_from") else planning_start
        first_event = schedule.events[0]
        first_start = event_start_time(first_event)
        if first_start > required_start:
            start_port_code = vessel.get("available_from_port_code") or event_start_port_code(first_event)
            prefix = _make_connection_events(
                start_port_code,
                event_start_port_code(first_event),
                required_start,
                first_start,
            )
            if prefix:
                solution.vessel_schedules[vessel_code] = prefix + list(schedule.events)
                schedule = solution.vessel_schedules[vessel_code]

        available_to = vessel.get("available_to")
        last_event = schedule.events[-1]
        if isinstance(last_event, Redelivery) and (available_to is None or available_to > planning_end):
            replacement = list(schedule.events[:-1])
            if replacement:
                previous = replacement[-1]
                previous_end = event_end_time(previous)
                if last_event.redelivery_time > previous_end:
                    replacement.append(
                        Idle(
                            port_code=event_end_port_code(previous),
                            idle_start=previous_end,
                            idle_end=last_event.redelivery_time,
                        )
                    )
            solution.vessel_schedules[vessel_code] = replacement
            schedule = solution.vessel_schedules[vessel_code]

        if available_to is None or available_to > planning_end:
            continue
        last_event = schedule.events[-1]
        last_end = event_end_time(last_event)
        if last_end >= available_to or isinstance(last_event, Redelivery):
            continue
        suffix = _make_connection_events(
            event_end_port_code(last_event),
            vessel["available_to_port_code"],
            last_end,
            available_to,
        )
        suffix.append(
            Redelivery(
                redelivery_time=available_to,
                redelivery_port_code=vessel["available_to_port_code"],
            )
        )
        solution.vessel_schedules[vessel_code] = list(schedule.events) + suffix


def _repair_current_assignment_starts(solution: CascadingSolution, instance_data: InstanceData) -> None:
    lane_view = solution.to_lane_view()
    repairs: list[tuple[dict, str, str, int]] = []
    for lane in instance_data.service_lanes:
        lane_code = lane["lane_code"]
        for version in lane["versions"]:
            proforma_name = version["proforma_name"]
            for assignment in version.get("vessel_assignments") or []:
                position_no = int(assignment["position_no"])
                assigned_vessel_code = assignment["vessel_code"]
                lane_events = list(lane_view.get((lane_code, proforma_name, position_no), []))
                if not lane_events:
                    continue
                lane_events.sort(key=lambda event: event_start_time(event.event))
                if lane_events[0].vessel_code == assigned_vessel_code:
                    continue
                vessel = next(
                    (candidate for candidate in instance_data.vessels if candidate["vessel_code"] == assigned_vessel_code),
                    None,
                )
                if vessel is None:
                    continue
                repairs.append((vessel, lane_code, proforma_name, position_no))

    if not repairs:
        return

    only_virtual1_solver.init_solver_globals(instance_data)
    for vessel, lane_code, proforma_name, position_no in repairs:
        vessel_code = vessel["vessel_code"]
        repaired_actual, repaired_virtual = _build_forward_current_assignment_repair(vessel_code, instance_data)
        if not repaired_actual:
            continue

        _remove_assignment_events_from_other_schedules(
            solution,
            lane_code,
            proforma_name,
            position_no,
            keep_vessel_code=vessel_code,
        )
        solution.vessel_schedules[vessel_code] = repaired_actual
        if repaired_virtual:
            virtual_code = _next_virtual_code(solution)
            solution.virtual_vessel_schedules[virtual_code] = repaired_virtual
            solution.num_virtual_vessels_used = max(
                solution.num_virtual_vessels_used,
                int(virtual_code.removeprefix("VIRTUAL")),
            )


def _repair_missing_declared_virtual_services(solution: CascadingSolution, instance_data: InstanceData) -> None:
    lane_view = solution.to_lane_view()
    only_virtual1_solver.init_solver_globals(instance_data)
    for declared_position in solution.declared_positions:
        key = (
            declared_position.lane_code,
            declared_position.proforma_name,
            int(declared_position.declared_position_no),
        )
        if lane_view.get(key):
            continue
        schedule = only_virtual1_solver.make_inlane_schedule(
            declared_position.lane_code,
            declared_position.proforma_name,
            int(declared_position.declared_position_no),
            instance_data.planning_horizon["start"],
            instance_data.planning_horizon["end"],
        )
        virtual_code = _next_virtual_code(solution)
        solution.virtual_vessel_schedules[virtual_code] = schedule
        solution.num_virtual_vessels_used = max(
            solution.num_virtual_vessels_used,
            int(virtual_code.removeprefix("VIRTUAL")),
        )
        lane_view[key] = []


def _build_forward_current_assignment_repair(
    vessel_code: str,
    instance_data: InstanceData,
) -> tuple[list[VesselScheduleEvent], list[VesselScheduleEvent]]:
    vessel = next(candidate for candidate in instance_data.vessels if candidate["vessel_code"] == vessel_code)
    planning_end = instance_data.planning_horizon["end"]
    schedule = only_virtual1_solver.make_inlane_schedule_for_vessel(vessel_code)
    virtual_schedule: list[VesselScheduleEvent] = []

    next_dock_in = (
        vessel["next_dock_in"]
        if vessel.get("next_dock_in") is not None and vessel["next_dock_in"] <= planning_end
        else None
    )
    next_dock_out = vessel.get("next_dock_out")
    next_dock_port_code = vessel.get("next_dock_port_code")
    available_to = (
        vessel["available_to"]
        if vessel.get("available_to") is not None and vessel["available_to"] <= planning_end
        else None
    )
    redelivery_port_code = vessel.get("available_to_port_code")

    target_time = next_dock_in or available_to
    target_port_code = next_dock_port_code if next_dock_in is not None else redelivery_port_code
    if target_time is not None and target_port_code is not None:
        schedule, virtual_schedule = only_virtual1_solver.split_inlane_schedule(
            copy.deepcopy(schedule),
            target_time,
            target_port_code,
        )

    last_event = schedule[-1]
    if next_dock_in is not None and next_dock_out is not None and next_dock_port_code is not None:
        schedule.extend(
            _make_connection_events(
                event_end_port_code(last_event),
                next_dock_port_code,
                event_end_time(last_event),
                next_dock_in,
            )
        )
        schedule.append(
            DryDock(
                dock_in=next_dock_in,
                dock_port_code=next_dock_port_code,
                dock_out=next_dock_out,
            )
        )
        if available_to is not None and redelivery_port_code is not None:
            schedule.extend(
                _make_connection_events(
                    next_dock_port_code,
                    redelivery_port_code,
                    next_dock_out,
                    available_to,
                )
            )
            schedule.append(
                Redelivery(
                    redelivery_time=available_to,
                    redelivery_port_code=redelivery_port_code,
                )
            )
        elif next_dock_out < planning_end:
            schedule.append(
                Idle(
                    port_code=next_dock_port_code,
                    idle_start=next_dock_out,
                    idle_end=planning_end,
                )
            )
    elif available_to is not None and redelivery_port_code is not None:
        schedule.extend(
            _make_connection_events(
                event_end_port_code(last_event),
                redelivery_port_code,
                event_end_time(last_event),
                available_to,
            )
        )
        schedule.append(
            Redelivery(
                redelivery_time=available_to,
                redelivery_port_code=redelivery_port_code,
            )
        )
    elif event_end_time(last_event) < planning_end:
        schedule.append(
            Idle(
                port_code=event_end_port_code(last_event),
                idle_start=event_end_time(last_event),
                idle_end=planning_end,
            )
        )

    return schedule, virtual_schedule


def _remove_assignment_events_from_other_schedules(
    solution: CascadingSolution,
    lane_code: str,
    proforma_name: str,
    position_no: int,
    *,
    keep_vessel_code: str,
) -> None:
    for vessel_code, schedule in list(solution.vessel_schedules.items()):
        if vessel_code == keep_vessel_code:
            continue
        filtered = [
            event
            for event in schedule.events
            if not _event_matches_assignment(event, lane_code, proforma_name, position_no)
        ]
        solution.vessel_schedules[vessel_code] = filtered

    for vessel_code, schedule in list(solution.virtual_vessel_schedules.items()):
        filtered = [
            event
            for event in schedule.events
            if not _event_matches_assignment(event, lane_code, proforma_name, position_no)
        ]
        if filtered:
            solution.virtual_vessel_schedules[vessel_code] = filtered
        else:
            solution.virtual_vessel_schedules.schedules.pop(vessel_code, None)


def _trim_pre_service_virtual_prefixes(solution: CascadingSolution, instance_data: InstanceData) -> None:
    lane_view = solution.to_lane_view()
    for key, lane_events in lane_view.items():
        if not lane_events:
            continue
        lane_code, proforma_name, position_no = key
        expected_calls = _expected_original_port_calls(instance_data, lane_code, proforma_name, position_no)
        if not expected_calls:
            continue
        test_start = expected_calls[0]["pilot_in_start"]
        lane_events.sort(key=lambda lane_event: lane_event.event)
        first_interval = next(
            (
                lane_event.event
                for lane_event in lane_events
                if isinstance(lane_event.event, (PortStay, InLaneSail, TransshipmentLoad, TransshipmentUnload))
            ),
            None,
        )
        if first_interval is None or event_start_time(first_interval) <= test_start <= event_end_time(first_interval):
            continue

        for vessel_code, schedule in list(solution.vessel_schedules.items()):
            filtered = [
                event
                for event in schedule.events
                if not (
                    _event_matches_assignment(event, lane_code, proforma_name, position_no)
                    and event_end_time(event) <= test_start
                )
            ]
            if len(filtered) != len(schedule.events):
                solution.vessel_schedules[vessel_code] = _close_internal_gaps(
                    _add_phaseins_for_open_portstays(filtered)
                )

        for vessel_code, schedule in list(solution.virtual_vessel_schedules.items()):
            filtered = [
                event
                for event in schedule.events
                if not (
                    _event_matches_assignment(event, lane_code, proforma_name, position_no)
                    and event_end_time(event) <= test_start
                )
            ]
            if filtered:
                solution.virtual_vessel_schedules[vessel_code] = _add_phaseins_for_open_portstays(filtered)
            else:
                solution.virtual_vessel_schedules.schedules.pop(vessel_code, None)


def _extend_virtual_service_suffixes(solution: CascadingSolution, instance_data: InstanceData) -> None:
    lane_view = solution.to_lane_view()
    for key, lane_events in lane_view.items():
        if not lane_events:
            continue
        lane_code, proforma_name, position_no = key
        expected_calls = _expected_original_port_calls(instance_data, lane_code, proforma_name, position_no)
        if len(expected_calls) < 2:
            continue
        test_end = expected_calls[-1]["pilot_out_end"]
        lane_events.sort(key=lambda lane_event: lane_event.event)
        last_interval = next(
            (
                lane_event.event
                for lane_event in reversed(lane_events)
                if isinstance(lane_event.event, (PortStay, InLaneSail, TransshipmentLoad, TransshipmentUnload))
            ),
            None,
        )
        if last_interval is not None and event_start_time(last_interval) <= test_end <= event_end_time(last_interval):
            continue

        repaired = False
        for vessel_code, schedule in list(solution.vessel_schedules.items()):
            extended = _extend_schedule_service_suffix(
                list(schedule.events),
                lane_code,
                proforma_name,
                position_no,
                expected_calls,
                test_end,
                instance_data,
            )
            if extended is not None:
                closed = _close_internal_gaps(extended)
                planning_end = instance_data.planning_horizon["end"]
                if closed and event_end_time(closed[-1]) < planning_end:
                    if _event_assignment_key(closed[-1]) is not None and not isinstance(closed[-1], PhaseOut):
                        key = _event_assignment_key(closed[-1])
                        closed.append(
                            PhaseOut(
                                lane_code=key[0],
                                proforma_name=key[1],
                                position_no=key[2],
                                phase_out_port_code=event_end_port_code(closed[-1]),
                                phase_out_port_seq=_event_end_port_seq(closed[-1]),
                                phase_out_time=event_end_time(closed[-1]),
                            )
                        )
                    closed.append(
                        Idle(
                            port_code=event_end_port_code(closed[-1]),
                            idle_start=event_end_time(closed[-1]),
                            idle_end=planning_end,
                        )
                    )
                    closed = _close_internal_gaps(closed)
                solution.vessel_schedules[vessel_code] = closed
                repaired = True
                break
        if repaired:
            continue

        for vessel_code, schedule in list(solution.virtual_vessel_schedules.items()):
            extended = _extend_schedule_service_suffix(
                list(schedule.events),
                lane_code,
                proforma_name,
                position_no,
                expected_calls,
                test_end,
                instance_data,
            )
            if extended is not None:
                solution.virtual_vessel_schedules[vessel_code] = _close_internal_gaps(extended)
                break


def _extend_schedule_service_suffix(
    events: list[VesselScheduleEvent],
    lane_code: str,
    proforma_name: str,
    position_no: int,
    expected_calls: list[dict],
    test_end: datetime,
    instance_data: InstanceData,
) -> list[VesselScheduleEvent] | None:
    matching_indices = [
        index
        for index, event in enumerate(events)
        if _event_matches_assignment(event, lane_code, proforma_name, position_no)
    ]
    if not matching_indices:
        return None
    insert_at = matching_indices[-1] + 1
    if isinstance(events[matching_indices[-1]], PhaseOut):
        insert_at = matching_indices[-1]
        events.pop(matching_indices[-1])
        matching_indices.pop()
    if not matching_indices:
        return None

    last_event = events[matching_indices[-1]]
    if not isinstance(last_event, PortStay):
        return None
    call_index = next(
        (
            index
            for index, call in enumerate(expected_calls)
            if call["port_code"] == last_event.port_code
            and call["port_seq"] == last_event.port_seq
            and call["pilot_in_start"] == last_event.pilot_in_start
        ),
        None,
    )
    if call_index is None:
        return None

    additions: list[VesselScheduleEvent] = []
    previous_stay = last_event
    for next_call in expected_calls[call_index + 1 :]:
        distance = _lookup_distance(instance_data, previous_stay.port_code, next_call["port_code"])
        if distance is None:
            break
        sail_hours = (next_call["pilot_in_start"] - previous_stay.pilot_out_end).total_seconds() / 3600
        if sail_hours <= 0:
            break
        additions.append(
            InLaneSail(
                lane_code=lane_code,
                proforma_name=proforma_name,
                position_no=position_no,
                from_port_code=previous_stay.port_code,
                from_port_seq=previous_stay.port_seq,
                sea_sail_start=previous_stay.pilot_out_end,
                to_port_code=next_call["port_code"],
                to_port_seq=next_call["port_seq"],
                sea_sail_end=next_call["pilot_in_start"],
                distance=distance,
                avg_speed=distance / sail_hours,
            )
        )
        next_stay = PortStay(
            lane_code=lane_code,
            proforma_name=proforma_name,
            position_no=position_no,
            port_code=next_call["port_code"],
            port_seq=next_call["port_seq"],
            pilot_in_start=next_call["pilot_in_start"],
            berthing_start=next_call["berthing_start"],
            berthing_end=next_call["berthing_end"],
            pilot_out_end=next_call["pilot_out_end"],
        )
        additions.append(next_stay)
        previous_stay = next_stay
        if event_start_time(next_stay) <= test_end <= event_end_time(next_stay):
            break
    if not additions:
        return None
    extension_end = event_end_time(additions[-1])
    tail = [event for event in events[insert_at:] if event_start_time(event) >= extension_end]
    return events[:insert_at] + additions + tail


def _trim_post_service_suffixes(solution: CascadingSolution, instance_data: InstanceData) -> None:
    trim_by_key: dict[tuple[str, str, int], datetime] = {}
    for key, lane_events in solution.to_lane_view().items():
        expected_calls = _expected_original_port_calls(instance_data, *key)
        if not expected_calls:
            continue
        test_end = expected_calls[-1]["pilot_out_end"]
        lane_events.sort(key=lambda lane_event: lane_event.event)
        last_interval = next(
            (
                lane_event.event
                for lane_event in reversed(lane_events)
                if isinstance(lane_event.event, (PortStay, InLaneSail, TransshipmentLoad, TransshipmentUnload))
            ),
            None,
        )
        if last_interval is not None and event_start_time(last_interval) <= test_end <= event_end_time(last_interval):
            continue
        trim_by_key[key] = test_end

    if not trim_by_key:
        return

    for vessel_code, schedule in list(solution.vessel_schedules.items()):
        solution.vessel_schedules[vessel_code] = _trim_schedule_post_service_suffixes(
            list(schedule.events),
            trim_by_key,
            instance_data.planning_horizon["end"],
            fill_to_planning_end=True,
        )
    for vessel_code, schedule in list(solution.virtual_vessel_schedules.items()):
        trimmed = _trim_schedule_post_service_suffixes(
            list(schedule.events),
            trim_by_key,
            instance_data.planning_horizon["end"],
            fill_to_planning_end=False,
        )
        if trimmed:
            solution.virtual_vessel_schedules[vessel_code] = trimmed
        else:
            solution.virtual_vessel_schedules.schedules.pop(vessel_code, None)


def _trim_schedule_post_service_suffixes(
    events: list[VesselScheduleEvent],
    trim_by_key: dict[tuple[str, str, int], datetime],
    planning_end: datetime,
    *,
    fill_to_planning_end: bool,
) -> list[VesselScheduleEvent]:
    trimmed: list[VesselScheduleEvent] = []
    inserted_phaseout_for_key: set[tuple[str, str, int]] = set()
    removed_any = False
    for event in events:
        key = _event_assignment_key(event)
        if key in trim_by_key and event_start_time(event) >= trim_by_key[key]:
            if key not in inserted_phaseout_for_key and trimmed:
                previous = trimmed[-1]
                if _event_assignment_key(previous) == key and not isinstance(previous, PhaseOut):
                    phaseout_time = trim_by_key[key]
                    trimmed.append(
                        PhaseOut(
                            lane_code=key[0],
                            proforma_name=key[1],
                            position_no=key[2],
                            phase_out_port_code=event_end_port_code(previous),
                            phase_out_port_seq=_event_end_port_seq(previous),
                            phase_out_time=phaseout_time,
                        )
                    )
                    inserted_phaseout_for_key.add(key)
            removed_any = True
            continue
        trimmed.append(event)

    if not removed_any:
        return events
    trimmed = _close_internal_gaps(trimmed)
    if fill_to_planning_end and trimmed and event_end_time(trimmed[-1]) < planning_end:
        trimmed.append(
            Idle(
                port_code=event_end_port_code(trimmed[-1]),
                idle_start=event_end_time(trimmed[-1]),
                idle_end=planning_end,
            )
        )
        trimmed = _close_internal_gaps(trimmed)
    return trimmed


def _close_internal_gaps(events: list[VesselScheduleEvent]) -> list[VesselScheduleEvent]:
    if not events:
        return events
    closed = [events[0]]
    for event in events[1:]:
        previous = closed[-1]
        previous_end = event_end_time(previous)
        event_start = event_start_time(event)
        if (
            isinstance(previous, Idle)
            and isinstance(event, Idle)
            and previous.port_code == event.port_code
            and previous_end == event_start
        ):
            closed[-1] = Idle(
                port_code=previous.port_code,
                idle_start=previous.idle_start,
                idle_end=event.idle_end,
            )
            continue
        if previous_end < event_start:
            closed.extend(
                _make_connection_events(
                    event_end_port_code(previous),
                    event_start_port_code(event),
                    previous_end,
                    event_start,
                )
            )
        closed.append(event)
    return _merge_adjacent_idles(closed)


def _merge_adjacent_idles(events: list[VesselScheduleEvent]) -> list[VesselScheduleEvent]:
    merged: list[VesselScheduleEvent] = []
    for event in events:
        if (
            merged
            and isinstance(merged[-1], Idle)
            and isinstance(event, Idle)
            and merged[-1].port_code == event.port_code
            and merged[-1].idle_end == event.idle_start
        ):
            previous = merged[-1]
            merged[-1] = Idle(
                port_code=previous.port_code,
                idle_start=previous.idle_start,
                idle_end=event.idle_end,
            )
        else:
            merged.append(event)
    return merged


def _event_assignment_key(event: VesselScheduleEvent) -> tuple[str, str, int] | None:
    lane_code = getattr(event, "lane_code", None)
    proforma_name = getattr(event, "proforma_name", None)
    position_no = getattr(event, "position_no", None)
    if lane_code is None or proforma_name is None or position_no is None:
        return None
    return (lane_code, proforma_name, int(position_no))


def _event_end_port_seq(event: VesselScheduleEvent) -> int:
    for field_name in ("phase_out_port_seq", "to_port_seq", "port_seq", "ts_port_seq", "phase_in_port_seq"):
        value = getattr(event, field_name, None)
        if value is not None:
            return int(value)
    raise ValueError(f"heuristic_yongs: cannot infer end port seq from {event!r}.")


def _event_matches_assignment(
    event: VesselScheduleEvent,
    lane_code: str,
    proforma_name: str,
    position_no: int,
) -> bool:
    return (
        getattr(event, "lane_code", None) == lane_code
        and getattr(event, "proforma_name", None) == proforma_name
        and getattr(event, "position_no", None) == position_no
    )


def _add_phaseins_for_open_portstays(events: list[VesselScheduleEvent]) -> list[VesselScheduleEvent]:
    repaired: list[VesselScheduleEvent] = []
    for event in events:
        if isinstance(event, PortStay):
            key = _event_assignment_key(event)
            previous = repaired[-1] if repaired else None
            if key is not None and (
                previous is None
                or _event_assignment_key(previous) != key
                or not isinstance(previous, (PhaseIn, InLaneSail, TransshipmentLoad, TransshipmentUnload, PortStay))
            ):
                repaired.append(
                    PhaseIn(
                        lane_code=event.lane_code,
                        proforma_name=event.proforma_name,
                        position_no=event.position_no,
                        phase_in_port_code=event.port_code,
                        phase_in_port_seq=event.port_seq,
                        phase_in_time=event.pilot_in_start,
                    )
                )
        repaired.append(event)
    return repaired


def _next_virtual_code(solution: CascadingSolution) -> str:
    used_codes = set(solution.virtual_vessel_schedules.keys())
    next_index = max(
        [solution.num_virtual_vessels_used]
        + [
            int(code.removeprefix("VIRTUAL"))
            for code in used_codes
            if code.startswith("VIRTUAL") and code.removeprefix("VIRTUAL").isdigit()
        ]
    )
    while True:
        next_index += 1
        code = f"VIRTUAL{next_index:03d}"
        if code not in used_codes:
            return code


def _mirror_schedule_back(
    schedule: Iterable[VesselScheduleEvent],
    original_instance: InstanceData,
    start: datetime,
    end: datetime,
) -> list[VesselScheduleEvent]:
    mirrored = [
        _mirror_event_back(event, original_instance, start, end)
        for event in reversed(list(schedule))
    ]
    return mirrored


def _mirror_event_back(
    event: VesselScheduleEvent,
    original_instance: InstanceData,
    start: datetime,
    end: datetime,
) -> VesselScheduleEvent:
    if isinstance(event, InLaneSail):
        lane_code, proforma_name, position_no = _mirror_event_position(event, original_instance)
        sea_sail_start = _mirror_datetime(event.sea_sail_end, start, end)
        sea_sail_end = _mirror_datetime(event.sea_sail_start, start, end)
        from_port_seq = _lookup_original_port_seq(
            original_instance,
            lane_code,
            proforma_name,
            position_no,
            event.to_port_code,
            sea_sail_start,
            preferred="pilot_out",
            fallback_mirrored_port_seq=event.to_port_seq,
        )
        to_port_seq = _lookup_original_port_seq(
            original_instance,
            lane_code,
            proforma_name,
            position_no,
            event.from_port_code,
            sea_sail_end,
            preferred="pilot_in",
            fallback_mirrored_port_seq=event.from_port_seq,
        )
        return InLaneSail(
            lane_code=lane_code,
            proforma_name=proforma_name,
            position_no=position_no,
            from_port_code=event.to_port_code,
            from_port_seq=from_port_seq,
            sea_sail_start=sea_sail_start,
            to_port_code=event.from_port_code,
            to_port_seq=to_port_seq,
            sea_sail_end=sea_sail_end,
            distance=event.distance,
            avg_speed=event.avg_speed,
        )
    if isinstance(event, OutLaneSail):
        return OutLaneSail(
            from_port_code=event.to_port_code,
            sea_sail_start=_mirror_datetime(event.sea_sail_end, start, end),
            to_port_code=event.from_port_code,
            sea_sail_end=_mirror_datetime(event.sea_sail_start, start, end),
            distance=event.distance,
            avg_speed=event.avg_speed,
        )
    if isinstance(event, PhaseIn):
        lane_code, proforma_name, position_no = _mirror_event_position(event, original_instance)
        phase_out_time = _mirror_datetime(event.phase_in_time, start, end)
        return PhaseOut(
            lane_code=lane_code,
            proforma_name=proforma_name,
            position_no=position_no,
            phase_out_port_code=event.phase_in_port_code,
            phase_out_port_seq=_lookup_original_port_seq(
                original_instance,
                lane_code,
                proforma_name,
                position_no,
                event.phase_in_port_code,
                phase_out_time,
                preferred="pilot_out",
                fallback_mirrored_port_seq=event.phase_in_port_seq,
            ),
            phase_out_time=phase_out_time,
        )
    if isinstance(event, PhaseOut):
        lane_code, proforma_name, position_no = _mirror_event_position(event, original_instance)
        phase_in_time = _mirror_datetime(event.phase_out_time, start, end)
        return PhaseIn(
            lane_code=lane_code,
            proforma_name=proforma_name,
            position_no=position_no,
            phase_in_port_code=event.phase_out_port_code,
            phase_in_port_seq=_lookup_original_port_seq(
                original_instance,
                lane_code,
                proforma_name,
                position_no,
                event.phase_out_port_code,
                phase_in_time,
                preferred="pilot_in",
                fallback_mirrored_port_seq=event.phase_out_port_seq,
            ),
            phase_in_time=phase_in_time,
        )
    if isinstance(event, PortStay):
        lane_code, proforma_name, position_no = _mirror_event_position(event, original_instance)
        pilot_in_start = _mirror_datetime(event.pilot_out_end, start, end)
        berthing_start = _mirror_datetime(event.berthing_end, start, end)
        berthing_end = _mirror_datetime(event.berthing_start, start, end)
        pilot_out_end = _mirror_datetime(event.pilot_in_start, start, end)
        return PortStay(
            lane_code=lane_code,
            proforma_name=proforma_name,
            position_no=position_no,
            port_code=event.port_code,
            port_seq=_lookup_original_port_seq(
                original_instance,
                lane_code,
                proforma_name,
                position_no,
                event.port_code,
                pilot_in_start,
                preferred="pilot_in",
                fallback_mirrored_port_seq=event.port_seq,
            ),
            pilot_in_start=pilot_in_start,
            berthing_start=berthing_start,
            berthing_end=berthing_end,
            pilot_out_end=pilot_out_end,
        )
    if isinstance(event, TransshipmentUnload):
        lane_code, proforma_name, position_no = _mirror_event_position(event, original_instance)
        load_start = _mirror_datetime(event.unload_end, start, end)
        load_end = _mirror_datetime(event.unload_start, start, end)
        return TransshipmentLoad(
            lane_code=lane_code,
            proforma_name=proforma_name,
            position_no=position_no,
            ts_port_code=event.ts_port_code,
            ts_port_seq=_lookup_original_port_seq(
                original_instance,
                lane_code,
                proforma_name,
                position_no,
                event.ts_port_code,
                load_end,
                preferred="pilot_in",
                fallback_mirrored_port_seq=event.ts_port_seq,
            ),
            load_start=load_start,
            load_end=load_end,
        )
    if isinstance(event, TransshipmentLoad):
        lane_code, proforma_name, position_no = _mirror_event_position(event, original_instance)
        unload_start = _mirror_datetime(event.load_end, start, end)
        unload_end = _mirror_datetime(event.load_start, start, end)
        return TransshipmentUnload(
            lane_code=lane_code,
            proforma_name=proforma_name,
            position_no=position_no,
            ts_port_code=event.ts_port_code,
            ts_port_seq=_lookup_original_port_seq(
                original_instance,
                lane_code,
                proforma_name,
                position_no,
                event.ts_port_code,
                unload_start,
                preferred="pilot_out",
                fallback_mirrored_port_seq=event.ts_port_seq,
            ),
            unload_start=unload_start,
            unload_end=unload_end,
        )
    if isinstance(event, DryDock):
        return DryDock(
            dock_port_code=event.dock_port_code,
            dock_in=_mirror_datetime(event.dock_out, start, end),
            dock_out=_mirror_datetime(event.dock_in, start, end),
        )
    if isinstance(event, Idle):
        return Idle(
            port_code=event.port_code,
            idle_start=_mirror_datetime(event.idle_end, start, end),
            idle_end=_mirror_datetime(event.idle_start, start, end),
        )
    if isinstance(event, Delivery):
        return Redelivery(
            redelivery_port_code=event.delivery_port_code,
            redelivery_time=_mirror_datetime(event.delivery_time, start, end),
        )
    if isinstance(event, Redelivery):
        return Delivery(
            delivery_port_code=event.redelivery_port_code,
            delivery_time=_mirror_datetime(event.redelivery_time, start, end),
        )
    raise TypeError(f"heuristic_yongs: unsupported event for mirror transform: {type(event).__name__}.")


def _mirror_event_position(
    event: VesselScheduleEvent,
    instance_data: InstanceData,
) -> tuple[str, str, int]:
    lane_code = getattr(event, "lane_code")
    proforma_name = getattr(event, "proforma_name")
    position_no = getattr(event, "position_no")
    return (
        lane_code,
        proforma_name,
        _mirror_position_for_instance(instance_data, lane_code, proforma_name, position_no),
    )


def _mirror_position_for_instance(
    instance_data: InstanceData,
    lane_code: str,
    proforma_name: str,
    position_no: int,
) -> int:
    cycle_len = _version_cycle_length_by_key(instance_data, lane_code, proforma_name)
    return _mirror_position_no(position_no, cycle_len)


def _mirror_port_seq_for_instance(
    instance_data: InstanceData,
    lane_code: str,
    proforma_name: str,
    port_seq: int,
) -> int:
    for lane in instance_data.service_lanes:
        if lane["lane_code"] != lane_code:
            continue
        for version in lane["versions"]:
            if version["proforma_name"] == proforma_name:
                return len(version["port_rotation"]) + 1 - int(port_seq)
    raise ValueError(f"heuristic_yongs: missing version {lane_code}/{proforma_name}.")


def _lookup_original_port_seq(
    instance_data: InstanceData,
    lane_code: str,
    proforma_name: str,
    position_no: int,
    port_code: str,
    when: datetime,
    *,
    preferred: str,
    fallback_mirrored_port_seq: int | None = None,
) -> int:
    calls = _expected_original_port_calls(instance_data, lane_code, proforma_name, position_no)
    candidates = [call for call in calls if call["port_code"] == port_code]
    if not candidates:
        if fallback_mirrored_port_seq is not None:
            return _mirror_port_seq_for_instance(
                instance_data,
                lane_code,
                proforma_name,
                fallback_mirrored_port_seq,
            )
        raise ValueError(
            "heuristic_yongs: missing original port call "
            f"{lane_code}/{proforma_name}/{position_no} {port_code} near {when}."
        )

    exact_field = "pilot_out_end" if preferred == "pilot_out" else "pilot_in_start"
    for call in candidates:
        if call[exact_field] == when:
            return int(call["port_seq"])

    def distance_seconds(call: dict) -> float:
        if call["pilot_in_start"] <= when <= call["pilot_out_end"]:
            return 0.0
        return min(
            abs((when - call["pilot_in_start"]).total_seconds()),
            abs((when - call["pilot_out_end"]).total_seconds()),
        )

    return int(min(candidates, key=distance_seconds)["port_seq"])


def _expected_original_port_calls(
    instance_data: InstanceData,
    lane_code: str,
    proforma_name: str,
    position_no: int,
) -> list[dict]:
    version = _lookup_version_in_instance(instance_data, lane_code, proforma_name)
    planning_start = instance_data.planning_horizon["start"]
    planning_end = instance_data.planning_horizon["end"]
    service_start = version["anchor_date"] + timedelta(days=7 * (int(position_no) - 1))
    effective_to = version.get("effective_to")
    if effective_to is None:
        service_end = planning_end
    else:
        service_duration = timedelta(days=int(version["service_duration"]))
        offset = timedelta(0)
        while service_start + offset < effective_to:
            offset += service_duration
        service_end = service_start + offset

    calls: list[dict] = []
    round_no = 0
    duration_days = int(version["service_duration"])
    while service_start + round_no * timedelta(days=duration_days) < service_end:
        offset = service_start + timedelta(days=round_no * duration_days)
        next_offset = offset + timedelta(days=duration_days)
        rotations = version["port_rotation"] if next_offset >= service_end else version["port_rotation"][:-1]
        for rotation in rotations:
            calls.append(
                {
                    "port_code": rotation["port_code"],
                    "port_seq": rotation["port_seq"],
                    "pilot_in_start": offset + timedelta(minutes=rotation["eta_offset_minutes"]),
                    "berthing_start": offset + timedelta(minutes=rotation["etb_offset_minutes"]),
                    "berthing_end": offset + timedelta(minutes=rotation["etd_offset_minutes"]),
                    "pilot_out_end": offset
                    + timedelta(minutes=rotation["etd_offset_minutes"] + rotation["pilot_out_minutes"]),
                }
            )
        round_no += 1
    return [
        call
        for call in calls
        if call["pilot_in_start"] < planning_end and call["pilot_out_end"] > planning_start
    ]


def _lookup_version_in_instance(instance_data: InstanceData, lane_code: str, proforma_name: str) -> dict:
    for lane in instance_data.service_lanes:
        if lane["lane_code"] != lane_code:
            continue
        for version in lane["versions"]:
            if version["proforma_name"] == proforma_name:
                return version
    raise ValueError(f"heuristic_yongs: missing version {lane_code}/{proforma_name}.")


def _version_cycle_length_by_key(instance_data: InstanceData, lane_code: str, proforma_name: str) -> int:
    for lane in instance_data.service_lanes:
        if lane["lane_code"] != lane_code:
            continue
        for version in lane["versions"]:
            if version["proforma_name"] == proforma_name:
                return _position_cycle_length(version)
    raise ValueError(f"heuristic_yongs: missing version {lane_code}/{proforma_name}.")


def _position_cycle_length(version: dict) -> int:
    return max(1, round(int(version["service_duration"]) / 7))


def _mirror_position_no(position_no: int, cycle_len: int) -> int:
    return cycle_len + 1 - int(position_no)


def _mirror_optional_datetime(value: datetime | None, start: datetime, end: datetime) -> datetime | None:
    return None if value is None else _mirror_datetime(value, start, end)


def _mirror_datetime(value: datetime, start: datetime, end: datetime) -> datetime:
    return start + (end - value)


def _apply_position_strategy(instance_data: InstanceData, strategy: str, seed: int) -> None:
    for lane in instance_data.service_lanes:
        for version in lane["versions"]:
            available_positions = sorted(int(position) for position in version.get("available_positions") or [])
            if not available_positions:
                continue

            own_vessel_count = int(version["own_vessel_count"])
            selected_positions = _pick_positions(available_positions, own_vessel_count, strategy, seed)
            version["available_positions"] = selected_positions


def _apply_reverse_position_strategy(instance_data: InstanceData, strategy: str, seed: int) -> None:
    for lane in instance_data.service_lanes:
        for version in lane["versions"]:
            available_positions = sorted(int(position) for position in version.get("available_positions") or [])
            if not available_positions:
                continue

            cycle_len = _position_cycle_length(version)
            mirrored_positions = sorted(_mirror_position_no(position, cycle_len) for position in available_positions)
            own_vessel_count = int(version["own_vessel_count"])
            selected_mirrored = _pick_positions(mirrored_positions, own_vessel_count, strategy, seed)
            version["available_positions"] = sorted(
                _mirror_position_no(position, cycle_len) for position in selected_mirrored
            )


def _pick_positions(available_positions: list[int], count: int, strategy: str, seed: int) -> list[int]:
    if count <= 0:
        return []
    if count >= len(available_positions):
        return list(available_positions)

    if strategy == "lowest":
        return available_positions[:count]
    if strategy == "highest":
        return available_positions[-count:]
    if strategy == "spread":
        return _spread_positions(available_positions, count)
    if strategy == "offset":
        return sorted(available_positions[(seed + offset) % len(available_positions)] for offset in range(count))

    raise ValueError(f"heuristic_yongs: unknown position strategy {strategy!r}.")


def _spread_positions(available_positions: list[int], count: int) -> list[int]:
    if count == 1:
        return [available_positions[len(available_positions) // 2]]

    last_index = len(available_positions) - 1
    indices = [round(i * last_index / (count - 1)) for i in range(count)]
    unique_indices: list[int] = []
    for index in indices:
        if index not in unique_indices:
            unique_indices.append(index)

    fill_index = 0
    while len(unique_indices) < count:
        if fill_index not in unique_indices:
            unique_indices.append(fill_index)
        fill_index += 1

    return sorted(available_positions[index] for index in unique_indices[:count])


def _measure_solution(solution: CascadingSolution) -> VariantMetrics:
    virtual_portstay_count = 0
    for schedule in solution.virtual_vessel_schedules.values():
        virtual_portstay_count += sum(1 for event in schedule if isinstance(event, PortStay))

    declared_position_signature = tuple(
        sorted(
            (
                position.lane_code,
                position.proforma_name,
                position.declared_position_no,
            )
            for position in solution.declared_positions
        )
    )

    return VariantMetrics(
        virtual_vessel_count=len(solution.virtual_vessel_schedules),
        virtual_portstay_count=virtual_portstay_count,
        actual_vessel_count=len(solution.vessel_schedules),
        declared_position_signature=declared_position_signature,
    )
