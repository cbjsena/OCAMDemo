from __future__ import annotations

import time
from collections import Counter
from typing import Any

from algorithms.wsgoh.utils_mip import CoverageKey, Pattern, _safe_name, _status_name
from ocam.models import CascadingSolution, InstanceData

from .config import PHASE_I_FEASIBILITY_TOLERANCE
from .patterns import _warm_start_z_values
from .types import CoverageContext, MasterResult, PhaseIResult
from .utils import _family_counts, _gurobi_name, _policy_status

def _build_master_model(
    gp,
    GRB,
    *,
    model_name: str,
    instance_data: InstanceData,
    context: CoverageContext,
    patterns: list[Pattern],
    mip_timelimit: int,
    warm_start_ids: set[str],
    solve_mode: str,
):
    if not patterns:
        raise ValueError("wsgoh/mip_twostage_v10: pattern pool is empty.")
    if not context.required_coverage:
        raise ValueError("wsgoh/mip_twostage_v10: no required coverage keys were generated.")

    model = gp.Model(model_name)
    # OCAM's ConsoleCapture mirrors stdout/stderr to the terminal and stores it
    # in *_logs.txt, so enabling Gurobi console output gives both live and saved
    # solver logs for each master solve.
    model.Params.OutputFlag = 1
    model.Params.LogToConsole = 1
    if mip_timelimit > 0:
        model.Params.TimeLimit = mip_timelimit
    if solve_mode == "norel":
        model.Params.NoRelHeurTime = max(1, mip_timelimit)
        model.Params.NodeLimit = 0
        model.Params.MIPFocus = 1
        model.Params.Heuristics = 1.0
    elif solve_mode != "full":
        raise ValueError(f"wsgoh/mip_twostage_v10: unknown solve mode {solve_mode!r}.")

    y = {
        pattern.pattern_id: model.addVar(vtype=GRB.BINARY, name=_gurobi_name("y", pattern.pattern_id))
        for pattern in patterns
    }
    z = {
        key: model.addVar(
            vtype=GRB.BINARY,
            lb=0,
            ub=1,
            name=_gurobi_name("z", f"{key[0]}_{key[1]}_{key[2]}"),
        )
        for key in sorted(context.selectable_positions)
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
                f"wsgoh/mip_twostage_v10: pattern {pattern.pattern_id!r} has missing parent patterns "
                f"{sorted(missing_requirements)!r}."
            )

    # eq 1,2,3 / one_pattern?
    for vessel in instance_data.vessels:
        vessel_code = vessel["vessel_code"]
        vessel_pattern_ids = actual_patterns_by_vessel.get(vessel_code, [])
        if not vessel_pattern_ids:
            raise ValueError(f"wsgoh/mip_twostage_v10: missing actual-vessel pattern for {vessel_code!r}.")
        model.addConstr(
            gp.quicksum(y[pattern_id] for pattern_id in vessel_pattern_ids) == 1,
            name=_gurobi_name("one_pattern", vessel_code),
        )

    for pattern in patterns:
        for required_pattern_id in sorted(pattern.requires_pattern_ids):
            model.addConstr(
                y[pattern.pattern_id] <= y[required_pattern_id],
                name=_gurobi_name("requires", f"{pattern.pattern_id}_{required_pattern_id}"),
            )

    # eq 4, declare
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
                name=_gurobi_name("declare", f"{lane_code}_{proforma_name}"),
            )

    for coverage_key in sorted(context.required_coverage):
        position_key = (coverage_key[0], coverage_key[1], coverage_key[2])
        rhs = z[position_key] if coverage_key in context.selectable_coverage else 1.0
        model.addConstr(
            gp.quicksum(y[pattern_id] for pattern_id in patterns_by_coverage.get(coverage_key, [])) == rhs,
            name=_gurobi_name(
                "cover",
                (
                    f"{coverage_key[0]}_{coverage_key[1]}_{coverage_key[2]}_"
                    f"{coverage_key[3]}_{int(coverage_key[5].timestamp())}"
                ),
            ),
        )

    for pattern in patterns:
        y[pattern.pattern_id].Start = 1.0 if pattern.pattern_id in warm_start_ids else 0.0
    warm_z = _warm_start_z_values(context, patterns, warm_start_ids)
    for key, variable in z.items():
        variable.Start = float(warm_z.get(key, context.initial_selectable_values.get(key, 0)))

    return model, y, z

def _build_phase_i_master_model(
    gp,
    GRB,
    *,
    model_name: str,
    instance_data: InstanceData,
    context: CoverageContext,
    patterns: list[Pattern],
    mip_timelimit: int,
    warm_start_ids: set[str],
):
    if not patterns:
        raise ValueError("wsgoh/mip_twostage_v10: Phase-I pattern pool is empty.")
    if any(pattern.is_virtual for pattern in patterns):
        raise ValueError("wsgoh/mip_twostage_v10: Phase-I actual-only pool contains virtual patterns.")
    if not context.required_coverage:
        raise ValueError("wsgoh/mip_twostage_v10: no required coverage keys were generated.")

    model = gp.Model(model_name)
    model.Params.OutputFlag = 1
    model.Params.LogToConsole = 1
    if mip_timelimit > 0:
        model.Params.TimeLimit = mip_timelimit

    y = {
        pattern.pattern_id: model.addVar(vtype=GRB.BINARY, name=_gurobi_name("y", pattern.pattern_id))
        for pattern in patterns
    }
    z = {
        key: model.addVar(
            vtype=GRB.BINARY,
            lb=0,
            ub=1,
            name=_gurobi_name("z", f"{key[0]}_{key[1]}_{key[2]}"),
        )
        for key in sorted(context.selectable_positions)
    }
    miss = {
        coverage_key: model.addVar(vtype=GRB.CONTINUOUS, lb=0.0, name=_gurobi_name("miss", coverage_key))
        for coverage_key in sorted(context.required_coverage)
    }
    extra = {
        coverage_key: model.addVar(vtype=GRB.CONTINUOUS, lb=0.0, name=_gurobi_name("extra", coverage_key))
        for coverage_key in sorted(context.required_coverage)
    }

    actual_patterns_by_vessel: dict[str, list[str]] = {}
    patterns_by_coverage: dict[CoverageKey, list[str]] = {}
    pattern_ids = {pattern.pattern_id for pattern in patterns}
    for pattern in patterns:
        actual_patterns_by_vessel.setdefault(pattern.vessel_code, []).append(pattern.pattern_id)
        for coverage_key in pattern.coverage_keys:
            patterns_by_coverage.setdefault(coverage_key, []).append(pattern.pattern_id)
        missing_requirements = pattern.requires_pattern_ids - pattern_ids
        if missing_requirements:
            raise ValueError(
                f"wsgoh/mip_twostage_v10: pattern {pattern.pattern_id!r} has missing parent patterns "
                f"{sorted(missing_requirements)!r}."
            )

    for vessel in instance_data.vessels:
        vessel_code = vessel["vessel_code"]
        vessel_pattern_ids = actual_patterns_by_vessel.get(vessel_code, [])
        if not vessel_pattern_ids:
            raise ValueError(f"wsgoh/mip_twostage_v10: missing actual-vessel pattern for {vessel_code!r}.")
        model.addConstr(
            gp.quicksum(y[pattern_id] for pattern_id in vessel_pattern_ids) == 1,
            name=_gurobi_name("one_pattern", vessel_code),
        )

    for pattern in patterns:
        for required_pattern_id in sorted(pattern.requires_pattern_ids):
            model.addConstr(
                y[pattern.pattern_id] <= y[required_pattern_id],
                name=_gurobi_name("requires", f"{pattern.pattern_id}_{required_pattern_id}"),
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
                name=_gurobi_name("declare", f"{lane_code}_{proforma_name}"),
            )

    for coverage_key in sorted(context.required_coverage):
        position_key = (coverage_key[0], coverage_key[1], coverage_key[2])
        rhs = z[position_key] if coverage_key in context.selectable_coverage else 1.0
        model.addConstr(
            gp.quicksum(y[pattern_id] for pattern_id in patterns_by_coverage.get(coverage_key, []))
            + miss[coverage_key]
            - extra[coverage_key]
            == rhs,
            name=_gurobi_name(
                "phase1_cover",
                (
                    f"{coverage_key[0]}_{coverage_key[1]}_{coverage_key[2]}_"
                    f"{coverage_key[3]}_{int(coverage_key[5].timestamp())}"
                ),
            ),
        )

    for pattern in patterns:
        y[pattern.pattern_id].Start = 1.0 if pattern.pattern_id in warm_start_ids else 0.0
    warm_z = _warm_start_z_values(context, patterns, warm_start_ids)
    for key, variable in z.items():
        variable.Start = float(warm_z.get(key, context.initial_selectable_values.get(key, 0)))

    return model, y, z, miss, extra

def _selected_solution_from_model(
    patterns: list[Pattern],
    y,
    z,
) -> tuple[CascadingSolution, list[Pattern], set[str]]:
    selected_pattern_ids = {
        pattern.pattern_id for pattern in patterns if y[pattern.pattern_id].X > 0.5
    }
    selected_patterns = [pattern for pattern in patterns if pattern.pattern_id in selected_pattern_ids]
    selected_actual_schedules: dict[str, dict[str, Any]] = {}
    selected_virtual_schedules: dict[str, dict[str, Any]] = {}
    for pattern in selected_patterns:
        if pattern.is_virtual:
            selected_virtual_schedules[pattern.vessel_code] = pattern.schedule_payload
        else:
            selected_actual_schedules[pattern.vessel_code] = pattern.schedule_payload

    selected_declared_positions = [
        {
            "lane_code": lane_code,
            "proforma_name": proforma_name,
            "declared_position_no": position_no,
        }
        for (lane_code, proforma_name, position_no), variable in sorted(z.items())
        if variable.X > 0.5
    ]

    solution = CascadingSolution(
        declared_positions=selected_declared_positions,
        vessel_schedules=selected_actual_schedules,
        virtual_vessel_schedules=selected_virtual_schedules,
        num_virtual_vessels_used=len(selected_virtual_schedules),
    )
    return solution, selected_patterns, selected_pattern_ids

def _solve_actual_master(
    gp,
    GRB,
    instance_data: InstanceData,
    context: CoverageContext,
    patterns: list[Pattern],
    mip_timelimit: int,
    warm_start_ids: set[str],
    iteration_label: str,
    solve_mode: str,
) -> MasterResult:
    start = time.monotonic()
    model, y, z = _build_master_model(
        gp,
        GRB,
        model_name=f"wsgoh_mip_twostage_v10_actual_{_safe_name(iteration_label)}",
        instance_data=instance_data,
        context=context,
        patterns=patterns,
        mip_timelimit=mip_timelimit,
        warm_start_ids=warm_start_ids,
        solve_mode=solve_mode,
    )
    model.setObjective(
        gp.quicksum(pattern.cost * y[pattern.pattern_id] for pattern in patterns),
        GRB.MINIMIZE,
    )
    model.optimize()
    status = _status_name(GRB, model.Status)
    policy_status = _policy_status(GRB, model, status)
    solve_seconds = time.monotonic() - start
    if model.SolCount < 1:
        return MasterResult(
            solution=None,
            objective=None,
            virtual_portstay_objective=None,
            status=status,
            policy_status=policy_status,
            selected_patterns=[],
            selected_pattern_ids=set(),
            selected_family_counts=Counter(),
            solve_seconds=solve_seconds,
        )

    solution, selected_patterns, selected_pattern_ids = _selected_solution_from_model(patterns, y, z)
    selected_cost = sum(pattern.cost for pattern in selected_patterns)
    return MasterResult(
        solution=solution,
        objective=float(selected_cost),
        virtual_portstay_objective=0,
        status=status,
        policy_status=policy_status,
        selected_patterns=selected_patterns,
        selected_pattern_ids=selected_pattern_ids,
        selected_family_counts=_family_counts(selected_patterns),
        solve_seconds=solve_seconds,
    )

def _solve_phase_i_actual_master(
    gp,
    GRB,
    instance_data: InstanceData,
    context: CoverageContext,
    patterns: list[Pattern],
    mip_timelimit: int,
    warm_start_ids: set[str],
) -> PhaseIResult:
    start = time.monotonic()
    model, y, z, miss, extra = _build_phase_i_master_model(
        gp,
        GRB,
        model_name="wsgoh_mip_twostage_v10_phase_i_actual",
        instance_data=instance_data,
        context=context,
        patterns=patterns,
        mip_timelimit=mip_timelimit,
        warm_start_ids=warm_start_ids,
    )
    model.setObjective(
        gp.quicksum(miss[key] + extra[key] for key in context.required_coverage),
        GRB.MINIMIZE,
    )
    model.optimize()
    status = _status_name(GRB, model.Status)
    policy_status = _policy_status(GRB, model, status)
    solve_seconds = time.monotonic() - start
    if model.SolCount < 1:
        return PhaseIResult(
            solution=None,
            objective=None,
            status=status,
            policy_status=policy_status,
            selected_patterns=[],
            selected_pattern_ids=set(),
            selected_family_counts=Counter(),
            solve_seconds=solve_seconds,
            missing_total=0.0,
            extra_total=0.0,
            top_missing=[],
            top_extra=[],
        )

    solution, selected_patterns, selected_pattern_ids = _selected_solution_from_model(patterns, y, z)
    missing_values = {
        key: float(variable.X)
        for key, variable in miss.items()
        if float(variable.X) > PHASE_I_FEASIBILITY_TOLERANCE
    }
    extra_values = {
        key: float(variable.X)
        for key, variable in extra.items()
        if float(variable.X) > PHASE_I_FEASIBILITY_TOLERANCE
    }
    objective = float(model.ObjVal)
    return PhaseIResult(
        solution=solution,
        objective=objective,
        status=status,
        policy_status=policy_status,
        selected_patterns=selected_patterns,
        selected_pattern_ids=selected_pattern_ids,
        selected_family_counts=_family_counts(selected_patterns),
        solve_seconds=solve_seconds,
        missing_total=sum(missing_values.values()),
        extra_total=sum(extra_values.values()),
        top_missing=sorted(missing_values.items(), key=lambda item: (-item[1], item[0]))[:10],
        top_extra=sorted(extra_values.items(), key=lambda item: (-item[1], item[0]))[:10],
    )

def _solve_fallback_master(
    gp,
    GRB,
    instance_data: InstanceData,
    context: CoverageContext,
    patterns: list[Pattern],
    mip_timelimit: int,
    warm_start_ids: set[str],
) -> MasterResult:
    start = time.monotonic()
    model, y, z = _build_master_model(
        gp,
        GRB,
        model_name="wsgoh_mip_twostage_v10_mixed_improve",
        instance_data=instance_data,
        context=context,
        patterns=patterns,
        mip_timelimit=mip_timelimit,
        warm_start_ids=warm_start_ids,
        solve_mode="full",
    )
    virtual_portstay_expr = gp.quicksum(
        len(pattern.coverage_keys) * y[pattern.pattern_id]
        for pattern in patterns
        if pattern.is_virtual
    )
    total_cost_expr = gp.quicksum(pattern.cost * y[pattern.pattern_id] for pattern in patterns)
    model.ModelSense = GRB.MINIMIZE
    model.setObjectiveN(
        virtual_portstay_expr,
        index=0,
        priority=2,
        weight=1.0,
        abstol=0.0,
        reltol=0.0,
        name="MinVirtualPortStay",
    )
    model.setObjectiveN(
        total_cost_expr,
        index=1,
        priority=1,
        weight=1.0,
        abstol=0.0,
        reltol=0.0,
        name="MinTotalCost",
    )
    model.optimize()
    status = _status_name(GRB, model.Status)
    policy_status = _policy_status(GRB, model, status)
    solve_seconds = time.monotonic() - start
    if model.SolCount < 1:
        return MasterResult(
            solution=None,
            objective=None,
            virtual_portstay_objective=None,
            status=status,
            policy_status=policy_status,
            selected_patterns=[],
            selected_pattern_ids=set(),
            selected_family_counts=Counter(),
            solve_seconds=solve_seconds,
        )

    solution, selected_patterns, selected_pattern_ids = _selected_solution_from_model(patterns, y, z)
    selected_cost = sum(pattern.cost for pattern in selected_patterns)
    selected_virtual_portstays = sum(len(pattern.coverage_keys) for pattern in selected_patterns if pattern.is_virtual)
    return MasterResult(
        solution=solution,
        objective=float(selected_cost),
        virtual_portstay_objective=int(selected_virtual_portstays),
        status=status,
        policy_status=policy_status,
        selected_patterns=selected_patterns,
        selected_pattern_ids=selected_pattern_ids,
        selected_family_counts=_family_counts(selected_patterns),
        solve_seconds=solve_seconds,
    )
