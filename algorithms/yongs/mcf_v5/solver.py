from __future__ import annotations

from copy import deepcopy
from time import perf_counter

from ocam.models import CascadingSolution, DeclaredPosition, InstanceData
from ocam.validation import evaluate_solution
from .flow_to_solution import flow_to_solution
from .cpp_backend import solve_flow_cpp

DESCRIPTION = "v5: 고정 TS timing의 restricted MCF를 full C++ backend로 최적화하고 실행 가능한 canal-aware solution을 반환한다."
USE_PICKLED_FLOW_VALUES = False

LAST_MULTICOMMODITY_RESULT: dict | None = None


def _replace_assignment_vessel_codes(service_lanes: list[dict], vessel_code_map: dict[str, tuple[str, str]]) -> None:
    for lane in service_lanes:
        for version in lane["versions"]:
            for assignment in version["vessel_assignments"]:
                replacement = vessel_code_map.get(assignment["vessel_code"])
                if replacement is not None:
                    assignment["vessel_code"] = replacement[0]


def _duplicate_canal_fees(canal_fee: list[dict], split_codes_by_original: dict[str, tuple[str, str]]) -> list[dict]:
    new_rows = []
    for row in canal_fee:
        vessel_code = row["vessel_code"]
        replacement_codes = split_codes_by_original.get(vessel_code)
        if replacement_codes is None:
            new_rows.append(deepcopy(row))
            continue
        for replacement_code in replacement_codes:
            new_row = deepcopy(row)
            new_row["vessel_code"] = replacement_code
            new_rows.append(new_row)
    return new_rows


def _split_dry_dock_vessels(
    instance_data: InstanceData,
) -> tuple[dict[str, tuple[str, str]], dict[str, str], list[dict[str, object]]]:
    planning_start = instance_data.planning_horizon["start"]
    planning_end = instance_data.planning_horizon["end"]
    split_codes_by_original: dict[str, tuple[str, str]] = {}
    boundary_dry_dock_restore_by_vessel: dict[str, str] = {}
    dd_couplings: list[dict[str, object]] = []
    new_vessels = []

    def _clear_dry_dock(vessel: dict) -> None:
        vessel["next_dock_in"] = None
        vessel["next_dock_out"] = None
        vessel["next_dock_port_code"] = None

    for vessel in instance_data.vessels:
        dock_in = vessel["next_dock_in"]
        dock_out = vessel["next_dock_out"]
        dock_port = vessel["next_dock_port_code"]
        if dock_in is None or dock_out is None or dock_port is None:
            new_vessels.append(vessel)
            continue

        if planning_start < dock_in and dock_out < planning_end:
            first_code = f"{vessel['vessel_code']}_DD1"
            second_code = f"{vessel['vessel_code']}_DD2"
            split_codes_by_original[vessel["vessel_code"]] = (first_code, second_code)
            dd_couplings.append(
                {
                    "original_vessel_code": vessel["vessel_code"],
                    "before_vessel_code": first_code,
                    "after_vessel_code": second_code,
                    "dock_port_code": dock_port,
                    "dock_in": dock_in,
                    "dock_out": dock_out,
                }
            )

            first_vessel = deepcopy(vessel)
            first_vessel["vessel_code"] = first_code
            first_vessel["available_to"] = dock_in
            first_vessel["available_to_port_code"] = dock_port
            _clear_dry_dock(first_vessel)

            second_vessel = deepcopy(vessel)
            second_vessel["vessel_code"] = second_code
            second_vessel["available_from"] = dock_out
            second_vessel["available_from_port_code"] = dock_port
            second_vessel["current_assignment"] = None
            _clear_dry_dock(second_vessel)

            new_vessels.extend([first_vessel, second_vessel])
            continue
        elif dock_in <= planning_start <= dock_out:
            # Planning horizon 내에 유효한 Delivery 정보 없어야 함
            if vessel["current_assignment"] is not None or (
                vessel["available_from"] is not None and vessel["available_from"] >= planning_start
            ):
                raise ValueError(
                    f"multicommodity: vessel {vessel['vessel_code']} has invalid current assignment or available_from "
                    f"while being in dry-dock at the start of planning horizon"
                )
            vessel["available_from"] = dock_out
            vessel["available_from_port_code"] = dock_port
            boundary_dry_dock_restore_by_vessel[vessel["vessel_code"]] = "start"
            _clear_dry_dock(vessel)
            new_vessels.append(vessel)
            continue
        elif dock_in <= planning_end <= dock_out:
            # Planning horizon 내에 유효한 Redelivery 정보 없어야 함
            if vessel["available_to"] is not None and vessel["available_to"] <= planning_end:
                raise ValueError(
                    f"multicommodity: vessel {vessel['vessel_code']} has invalid available_to "
                    f"while being in dry-dock at the end of planning horizon"
                )
            vessel["available_to"] = dock_in
            vessel["available_to_port_code"] = dock_port
            boundary_dry_dock_restore_by_vessel[vessel["vessel_code"]] = "end"
            _clear_dry_dock(vessel)
            new_vessels.append(vessel)
            continue
        else:
            new_vessels.append(vessel)

    instance_data.vessels = new_vessels
    _replace_assignment_vessel_codes(instance_data.service_lanes, split_codes_by_original)
    instance_data.canal_fee = _duplicate_canal_fees(instance_data.canal_fee, split_codes_by_original)
    if split_codes_by_original:
        print(
            "[mcf_v5] split dry-dock vessels "
            f"count={len(split_codes_by_original)} "
            f"created={sum(len(codes) for codes in split_codes_by_original.values())}",
            flush=True,
        )
    return split_codes_by_original, boundary_dry_dock_restore_by_vessel, dd_couplings


def _declare_positions(instance_data: InstanceData) -> list[DeclaredPosition]:
    return []


def _make_network_positions(instance_data: InstanceData) -> list[DeclaredPosition]:
    network_positions = []
    seen = set()
    for lane in instance_data.service_lanes:
        lane_code = lane["lane_code"]
        for version in lane["versions"]:
            position_numbers = set(version.get("declared_positions") or version.get("available_positions") or [])
            for assignment in version["vessel_assignments"]:
                position_numbers.add(assignment["position_no"])
            for position_no in sorted(position_numbers):
                key = (lane_code, version["proforma_name"], position_no)
                if key in seen:
                    continue
                seen.add(key)
                network_positions.append(
                    DeclaredPosition(
                        lane_code=lane_code,
                        proforma_name=version["proforma_name"],
                        declared_position_no=position_no,
                    )
                )
    return network_positions


def algorithm(instance_data: InstanceData, timelimit: int) -> CascadingSolution:
    global LAST_MULTICOMMODITY_RESULT

    working_instance_data = deepcopy(instance_data)
    start_time = perf_counter()
    checkpoint = start_time

    def log_stage(message: str) -> None:
        nonlocal checkpoint
        now = perf_counter()
        print(f"[multicommodity] {message} | step={now - checkpoint:.2f}s total={now - start_time:.2f}s", flush=True)
        checkpoint = now

    log_stage(f"start timelimit={timelimit}")
    split_codes_by_original, boundary_dry_dock_restore_by_vessel, dd_couplings = _split_dry_dock_vessels(
        working_instance_data
    )
    log_stage(f"prepared dry-dock-free instance vessels={len(working_instance_data.vessels)}")

    declared_positions = _declare_positions(working_instance_data)
    network_positions = _make_network_positions(working_instance_data)
    log_stage(
        "prepared position candidates "
        f"output_declared={len(declared_positions)} network_positions={len(network_positions)}"
    )

    vessel_codes = sorted(vessel["vessel_code"] for vessel in working_instance_data.vessels)

    LAST_MULTICOMMODITY_RESULT = None
    solution_pickle_path = None
    if USE_PICKLED_FLOW_VALUES:
        raise ValueError("mcf_v5: pickled flow loading is disabled.")
    else:
        log_stage("pickled flow values disabled")
    if LAST_MULTICOMMODITY_RESULT is not None:
        log_stage(f"load multicommodity flow values finished path={solution_pickle_path}")
    else:
        log_stage(f"cpp network construction and flow solve begin vessels={len(vessel_codes)}")
        LAST_MULTICOMMODITY_RESULT = solve_flow_cpp(
            working_instance_data,
            network_positions,
            output_path="/private/tmp/ocam_yongs/mcf_v5_mip_paths.html",
            model_name="mcf_v5",
            dd_couplings=dd_couplings,
        )
        network = LAST_MULTICOMMODITY_RESULT["network"]
        log_stage(f"cpp flow solve finished nodes={network.number_of_nodes()} edges={network.number_of_edges()}")

    declared_positions = LAST_MULTICOMMODITY_RESULT.get("declared_positions", declared_positions)
    solution = flow_to_solution(
        LAST_MULTICOMMODITY_RESULT,
        declared_positions,
        working_instance_data,
        original_instance_data=instance_data,
        split_codes_by_original=split_codes_by_original,
        boundary_dry_dock_restore_by_vessel=boundary_dry_dock_restore_by_vessel,
    )
    evaluation = evaluate_solution(solution, instance_data)
    if evaluation is not None:
        LAST_MULTICOMMODITY_RESULT["raw_model_objective_value"] = LAST_MULTICOMMODITY_RESULT["objective_value"]
        LAST_MULTICOMMODITY_RESULT["objective_value"] = evaluation["total_cost"]
        LAST_MULTICOMMODITY_RESULT["evaluation"] = evaluation
    log_stage(
        "converted flow to solution "
        f"actual_schedules={len(solution.vessel_schedules)} "
        f"virtual_schedules={len(solution.virtual_vessel_schedules)}"
    )

    print(
        "multicommodity flow solved\n"
        f"- actual vessels: {len(vessel_codes)}\n"
        f"- related lane positions: {len(network.graph['related_lane_keys'])}\n"
        f"- objective value: {LAST_MULTICOMMODITY_RESULT['objective_value']}\n"
        f"- visualization: //multicommodity_mip_paths.html"
    )
    log_stage("finished")
    return solution
