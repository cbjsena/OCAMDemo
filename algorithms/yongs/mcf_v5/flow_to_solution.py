from __future__ import annotations

from datetime import timedelta
from time import perf_counter
from typing import Any

from ocam.models import *
from ocam.utils import *

from .network.nodes import HorizonSailNode


def _copy_event(event: VesselScheduleEvent) -> VesselScheduleEvent:
    return VesselScheduleEvent.coerce(event.to_dict())


def _append_event(events: list[VesselScheduleEvent], event: VesselScheduleEvent | None) -> None:
    if event is not None:
        events.append(_copy_event(event))


def _phase_out_from_port_stay(event: PortStay) -> PhaseOut:
    return PhaseOut(
        lane_code=event.lane_code,
        proforma_name=event.proforma_name,
        position_no=event.position_no,
        phase_out_port_code=event.port_code,
        phase_out_port_seq=event.port_seq,
        phase_out_time=event.pilot_out_end,
    )


def _event_port_seq(event: InLaneEvent, attr_name: str) -> int:
    return getattr(event, attr_name.replace("code", "seq"))


def _same_lane(left: VesselScheduleEvent, right: VesselScheduleEvent) -> bool:
    return (
        isinstance(left, InLaneEvent)
        and isinstance(right, InLaneEvent)
        and left.lane_code == right.lane_code
        and left.proforma_name == right.proforma_name
        and left.position_no == right.position_no
    )


def _node_events(node) -> list[VesselScheduleEvent]:
    event = node.event
    if isinstance(node, HorizonSailNode):
        return [node.to_inlane_sail()]
    if node.label == "delivery" or node.label == "redelivery":
        return [_copy_event(event)]
    if node.label == "ss":
        return [
            PhaseIn(
                lane_code=event.lane_code,
                proforma_name=event.proforma_name,
                position_no=event.position_no,
                phase_in_port_code=event.port_code,
                phase_in_port_seq=event.port_seq,
                phase_in_time=node.node_out_time,
            )
        ]
    if node.label == "se":
        return [
            PhaseOut(
                lane_code=event.lane_code,
                proforma_name=event.proforma_name,
                position_no=event.position_no,
                phase_out_port_code=event.port_code,
                phase_out_port_seq=event.port_seq,
                phase_out_time=node.node_in_time,
            )
        ]
    if node.label in {"ts_in_after", "ts_in_before"}:
        return [
            PhaseIn(
                lane_code=event.lane_code,
                proforma_name=event.proforma_name,
                position_no=event.position_no,
                phase_in_port_code=event.port_code,
                phase_in_port_seq=event.port_seq,
                phase_in_time=node.node_in_time,
            ),
            TransshipmentLoad(
                lane_code=event.lane_code,
                proforma_name=event.proforma_name,
                position_no=event.position_no,
                ts_port_code=event.port_code,
                ts_port_seq=event.port_seq,
                load_start=node.node_in_time,
                load_end=node.node_out_time,
            ),
        ]
    if node.label in {"ts_out_after", "ts_out_before"}:
        return [
            TransshipmentUnload(
                lane_code=event.lane_code,
                proforma_name=event.proforma_name,
                position_no=event.position_no,
                ts_port_code=event.port_code,
                ts_port_seq=event.port_seq,
                unload_start=node.node_in_time,
                unload_end=node.node_out_time,
            ),
            PhaseOut(
                lane_code=event.lane_code,
                proforma_name=event.proforma_name,
                position_no=event.position_no,
                phase_out_port_code=event.port_code,
                phase_out_port_seq=event.port_seq,
                phase_out_time=node.node_out_time,
            ),
        ]
    return []


def _sail_event(from_node, to_node, edge_data: dict[str, Any]) -> VesselScheduleEvent:
    from_event = from_node.event
    to_event = to_node.event
    distance = edge_data.get("distance")
    sail_hours = (to_node.node_in_time - from_node.node_out_time).total_seconds() / 3600
    avg_speed = distance / sail_hours if distance is not None and sail_hours else None
    if _same_lane(from_event, to_event):
        return InLaneSail(
            lane_code=from_event.lane_code,
            proforma_name=from_event.proforma_name,
            position_no=from_event.position_no,
            from_port_code=event_end_port_code(from_event),
            from_port_seq=_event_port_seq(from_event, from_event.end_port_attr),
            sea_sail_start=from_node.node_out_time,
            to_port_code=event_start_port_code(to_event),
            to_port_seq=_event_port_seq(to_event, to_event.start_port_attr),
            sea_sail_end=to_node.node_in_time,
            distance=distance,
            avg_speed=avg_speed,
        )
    return OutLaneSail(
        from_port_code=event_end_port_code(from_event),
        sea_sail_start=from_node.node_out_time,
        to_port_code=event_start_port_code(to_event),
        sea_sail_end=to_node.node_in_time,
        distance=distance,
        avg_speed=avg_speed,
    )


def _edge_data(network, edge: dict[str, Any]) -> dict[str, Any]:
    arc_id = edge.get("arc_id")
    if arc_id is not None:
        data = network.get_edge_data(edge["from_node_id"], edge["to_node_id"], arc_id)
        if data is not None:
            return data
    data = network.get_edge_data(edge["from_node_id"], edge["to_node_id"]) or {}
    if data and all(isinstance(value, dict) for value in data.values()):
        for value in data.values():
            if arc_id is None or value.get("arc_id") == arc_id:
                return value
        return {}
    return data


def _canal_sail_events(from_node, to_node, edge: dict[str, Any]) -> list[VesselScheduleEvent]:
    from_port_code = event_end_port_code(from_node.event)
    canal_port_code = edge["canal_port_code"]
    to_port_code = event_start_port_code(to_node.event)
    leg1_start = from_node.node_out_time
    leg1_end = leg1_start + timedelta(hours=edge["canal_leg1_hours"])
    passage_end = leg1_end + timedelta(hours=edge["canal_passage_hours"])
    leg2_end = to_node.node_in_time
    leg2_hours = (leg2_end - passage_end).total_seconds() / 3600
    leg2_avg_speed = edge["canal_leg2_distance"] / leg2_hours if leg2_hours else None

    return [
        OutLaneSail(
            from_port_code=from_port_code,
            sea_sail_start=leg1_start,
            to_port_code=canal_port_code,
            sea_sail_end=leg1_end,
            distance=edge["canal_leg1_distance"],
            avg_speed=edge["canal_leg1_speed"],
        ),
        CanalPassage(
            canal_port_code=canal_port_code,
            direction=edge["canal_direction"],
            passage_start=leg1_end,
            passage_end=passage_end,
            from_port_code=from_port_code,
            to_port_code=to_port_code,
        ),
        OutLaneSail(
            from_port_code=canal_port_code,
            sea_sail_start=passage_end,
            to_port_code=to_port_code,
            sea_sail_end=leg2_end,
            distance=edge["canal_leg2_distance"],
            avg_speed=leg2_avg_speed,
        ),
    ]


def _arc_event(
    network, from_node, to_node, edge: dict[str, Any], instance_data: InstanceData
) -> VesselScheduleEvent | list[VesselScheduleEvent] | None:
    if to_node.label == "idle":
        return Idle(
            port_code=event_end_port_code(from_node.event),
            idle_start=from_node.node_out_time,
            idle_end=instance_data.planning_horizon["end"],
        )
    if (
        to_node.label == "target"
        and from_node.label != "idle"
        and from_node.node_out_time < instance_data.planning_horizon["end"]
    ):
        return Idle(
            port_code=event_end_port_code(from_node.event),
            idle_start=from_node.node_out_time,
            idle_end=instance_data.planning_horizon["end"],
        )
    if from_node.label == "delivery" and (to_node.label == "pilot_in" or isinstance(to_node, HorizonSailNode)):
        event = to_node.event
        phase_in_time = to_node.node_in_time
        phase_in_port_code = event_start_port_code(event)
        phase_in_port_seq = _event_port_seq(event, event.start_port_attr)
        if isinstance(to_node, HorizonSailNode):
            phase_in_time = to_node.sea_sail_start
            phase_in_port_code = to_node.from_port_code
            phase_in_port_seq = to_node.from_port_seq
        phase_in = PhaseIn(
            lane_code=event.lane_code,
            proforma_name=event.proforma_name,
            position_no=event.position_no,
            phase_in_port_code=phase_in_port_code,
            phase_in_port_seq=phase_in_port_seq,
            phase_in_time=phase_in_time,
        )
        if from_node.node_out_time == phase_in_time:
            return phase_in
        if edge["arc_type"] == "CanalSailArc":
            return [*_canal_sail_events(from_node, to_node, edge), phase_in]
        if edge["arc_type"] == "SailArc":
            edge_data = _edge_data(network, edge)
            return [_sail_event(from_node, to_node, edge_data), phase_in]
        return [
            Idle(
                port_code=event_end_port_code(from_node.event),
                idle_start=from_node.node_out_time,
                idle_end=phase_in_time,
            ),
            phase_in,
        ]
    if from_node.label == "pilot_in" and to_node.label == "pilot_out":
        return from_node.event
    if (
        edge["arc_type"] not in {"SailArc", "CanalSailArc"}
        and event_end_port_code(from_node.event) == event_start_port_code(to_node.event)
        and from_node.node_out_time < to_node.node_in_time
    ):
        return Idle(
            port_code=event_end_port_code(from_node.event),
            idle_start=from_node.node_out_time,
            idle_end=to_node.node_in_time,
        )
    if edge["arc_type"] == "CanalSailArc":
        return _canal_sail_events(from_node, to_node, edge)
    if edge["arc_type"] == "SailArc":
        edge_data = _edge_data(network, edge)
        return _sail_event(from_node, to_node, edge_data)
    return None


def _restore_dry_dock(
    solution: CascadingSolution,
    original_instance_data: InstanceData | None,
    split_codes_by_original: dict[str, tuple[str, str]] | None,
    boundary_dry_dock_restore_by_vessel: dict[str, str] | None,
) -> CascadingSolution:
    if original_instance_data is None:
        return solution
    split_codes_by_original = split_codes_by_original or {}
    boundary_dry_dock_restore_by_vessel = boundary_dry_dock_restore_by_vessel or {}
    if not split_codes_by_original and not boundary_dry_dock_restore_by_vessel:
        return solution

    start_time = perf_counter()
    print(
        "[flow_to_solution] restore dry-dock begin "
        f"split={len(split_codes_by_original)} boundary={len(boundary_dry_dock_restore_by_vessel)}",
        flush=True,
    )
    vessel_by_code = {vessel["vessel_code"]: vessel for vessel in original_instance_data.vessels}
    child_codes = {child_code for codes in split_codes_by_original.values() for child_code in codes}
    vessel_schedules = {
        vessel_code: schedule
        for vessel_code, schedule in solution.vessel_schedules.items()
        if vessel_code not in child_codes
    }

    for original_code, (first_code, second_code) in split_codes_by_original.items():
        vessel = vessel_by_code[original_code]
        dock_port = vessel["next_dock_port_code"]
        dock_in = vessel["next_dock_in"]
        dock_out = vessel["next_dock_out"]
        first_schedule = list(solution.vessel_schedules.get(first_code, []))
        second_schedule = list(solution.vessel_schedules.get(second_code, []))
        if first_schedule and isinstance(first_schedule[-1], Redelivery):
            first_schedule = first_schedule[:-1]
        if second_schedule and isinstance(second_schedule[0], Delivery):
            second_schedule = second_schedule[1:]
        vessel_schedules[original_code] = [
            *first_schedule,
            DryDock(dock_port_code=dock_port, dock_in=dock_in, dock_out=dock_out),
            *second_schedule,
        ]

    for vessel_code, restore_position in boundary_dry_dock_restore_by_vessel.items():
        vessel = vessel_by_code[vessel_code]
        dock_event = DryDock(
            dock_port_code=vessel["next_dock_port_code"],
            dock_in=vessel["next_dock_in"],
            dock_out=vessel["next_dock_out"],
        )
        schedule = list(vessel_schedules.get(vessel_code, solution.vessel_schedules.get(vessel_code, [])))
        if restore_position == "start":
            if schedule and isinstance(schedule[0], Delivery):
                schedule = schedule[1:]
            vessel_schedules[vessel_code] = [dock_event, *schedule]
        else:
            if schedule and isinstance(schedule[-1], Redelivery):
                schedule = schedule[:-1]
            vessel_schedules[vessel_code] = [*schedule, dock_event]

    restored_solution = CascadingSolution(
        declared_positions=solution.declared_positions,
        vessel_schedules=vessel_schedules,
        virtual_vessel_schedules=solution.virtual_vessel_schedules,
        num_virtual_vessels_used=solution.num_virtual_vessels_used,
    )
    print(
        "[flow_to_solution] restore dry-dock finished "
        f"actual_schedules={len(restored_solution.vessel_schedules)} elapsed={perf_counter() - start_time:.2f}s",
        flush=True,
    )
    return restored_solution


def flow_to_solution(
    flow_result: dict[str, Any],
    declared_positions: list[DeclaredPosition],
    instance_data: InstanceData,
    original_instance_data: InstanceData | None = None,
    split_codes_by_original: dict[str, tuple[str, str]] | None = None,
    boundary_dry_dock_restore_by_vessel: dict[str, str] | None = None,
) -> CascadingSolution:
    start_time = perf_counter()
    checkpoint = start_time

    def log_stage(message: str) -> None:
        nonlocal checkpoint
        now = perf_counter()
        print(f"[flow_to_solution] {message} | step={now - checkpoint:.2f}s total={now - start_time:.2f}s", flush=True)
        checkpoint = now

    network = flow_result["network"]
    vessel_schedules: dict[str, list[VesselScheduleEvent]] = {}
    virtual_vessel_schedules: dict[str, list[VesselScheduleEvent]] = {}
    paths = flow_result["paths"]
    log_stage(f"start paths={len(paths)} nodes={network.number_of_nodes()} edges={network.number_of_edges()}")

    for path_index, path in enumerate(paths, start=1):
        path_start_time = perf_counter()
        vessel_code = path["vessel_code"]
        events: list[VesselScheduleEvent] = []
        node_path = path["node_path"]
        edge_path = path["edge_path"]

        for index, node_id in enumerate(node_path):
            node = network.nodes[node_id]["node"]
            for event in _node_events(node):
                _append_event(events, event)
            if index >= len(edge_path):
                continue

            edge = edge_path[index]
            arc = (edge["from_node_id"], edge["to_node_id"])
            from_node = network.nodes[arc[0]]["node"]
            to_node = network.nodes[arc[1]]["node"]
            arc_event = _arc_event(network, from_node, to_node, edge, instance_data)
            arc_events = arc_event if isinstance(arc_event, list) else [arc_event]
            for next_arc_event in arc_events:
                if isinstance(next_arc_event, Idle) and events and isinstance(events[-1], PortStay):
                    _append_event(events, _phase_out_from_port_stay(events[-1]))
                _append_event(events, next_arc_event)

        if path.get("is_virtual"):
            virtual_vessel_schedules[vessel_code] = events
        else:
            vessel_schedules[vessel_code] = events

    solution = CascadingSolution(
        declared_positions=[position.to_dict() for position in declared_positions],
        vessel_schedules=vessel_schedules,
        virtual_vessel_schedules=virtual_vessel_schedules,
        num_virtual_vessels_used=len(virtual_vessel_schedules),
    )
    log_stage(
        f"built raw solution actual_schedules={len(vessel_schedules)} virtual_schedules={len(virtual_vessel_schedules)}"
    )
    solution = _restore_dry_dock(
        solution,
        original_instance_data,
        split_codes_by_original,
        boundary_dry_dock_restore_by_vessel,
    )
    log_stage(
        "finished "
        f"actual_schedules={len(solution.vessel_schedules)} virtual_schedules={len(solution.virtual_vessel_schedules)}"
    )
    return solution
