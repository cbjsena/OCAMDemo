from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Literal
from collections import deque
import os
from time import perf_counter

import networkx as nx

try:
    from tqdm import tqdm
except ImportError:

    def tqdm(iterable, **kwargs):
        return iterable


from ocam.models import *
from ocam.utils import *

from .nodes import *
from .arcs import *
from . import arcs as arcs_module
from . import nodes as nodes_module
from .__viz import viz, viz_graph, viz_zero_arcs

INSTANCE_DATA = None
PLANNING_START = None
PLANNING_END = None
IDLE_EVENT_KEY = (("__idle__", True),)
CAPACITY_COMPATIBILITY_TOLERANCE = 0.05
CAPACITY_INTERVAL_BY_LANE_VERSION: dict[tuple[str, str], tuple[float, float]] = {}


def _progress(iterable, **kwargs):
    kwargs.setdefault("dynamic_ncols", True)
    kwargs.setdefault("leave", False)
    kwargs.setdefault("mininterval", 1.0)
    kwargs.setdefault("disable", not os.isatty(2))
    return tqdm(iterable, **kwargs)


def _progress_write(message: str) -> None:
    if not os.isatty(2):
        print(message, flush=True)
        return
    writer = getattr(tqdm, "write", None)
    if callable(writer):
        writer(message)
        return
    print(message, flush=True)


def initialize_network_construction(instance_data: InstanceData) -> None:
    global INSTANCE_DATA, PLANNING_START, PLANNING_END, _NEXT_NODE_ID, _NEXT_NODE_GROUP_ID, _NEXT_ARC_ID
    global CAPACITY_INTERVAL_BY_LANE_VERSION

    INSTANCE_DATA = instance_data
    PLANNING_START = instance_data.planning_horizon["start"]
    PLANNING_END = instance_data.planning_horizon["end"]
    nodes_module._NEXT_NODE_ID = 0
    nodes_module._NEXT_NODE_GROUP_ID = 0
    arcs_module._NEXT_ARC_ID = 0
    CAPACITY_INTERVAL_BY_LANE_VERSION = {}
    for lane in instance_data.service_lanes:
        lane_code = lane["lane_code"]
        for version in lane["versions"]:
            required_capacity = float(version["required_capacity_teu"])
            tolerance = required_capacity * CAPACITY_COMPATIBILITY_TOLERANCE
            CAPACITY_INTERVAL_BY_LANE_VERSION[(lane_code, version["proforma_name"])] = (
                required_capacity - tolerance,
                required_capacity + tolerance,
            )
    init_utils(instance_data)


def lane_info(event: InLaneEvent) -> tuple[str, str, int]:
    return (event.lane_code, event.proforma_name, event.position_no)


def _event_key(event: VesselScheduleEvent) -> tuple[tuple[str, object], ...]:
    payload = event.to_dict()
    return tuple((key, payload[key]) for key in sorted(payload))


def _to_networkx(network_items: list[Node | NodeGroup], arcs: list[Arc]) -> nx.MultiDiGraph:
    graph = nx.MultiDiGraph()
    graph.graph["network_items"] = network_items
    graph.graph["arcs"] = arcs

    for item in _progress(network_items, total=len(network_items), desc="graph nodes"):
        for node in item.get_nodes() if isinstance(item, NodeGroup) else [item]:
            graph.add_node(
                node.get_id(),
                node=node,
                owner=item,
                owner_id=item.get_id(),
                label=node.label,
                event=node.event,
                status=node.event.status,
                node_in_time=node.node_in_time,
                node_out_time=node.node_out_time,
                port_code=event_start_port_code(node.event),
            )

    item_by_event_key: dict[tuple[tuple[str, object], ...], Node | NodeGroup] = {}
    vessel_item_by_event_key: dict[tuple[str, tuple[tuple[str, object], ...]], Node] = {}
    for item in _progress(network_items, total=len(network_items), desc="event index"):
        event_key = IDLE_EVENT_KEY if isinstance(item, I) else _event_key(item.event)
        if isinstance(item, NodeGroup):
            if event_key in item_by_event_key:
                raise ValueError(f"_to_networkx: duplicate item for event key: event={item.event!r}.")
            item_by_event_key[event_key] = item
            continue

        vessel_code = getattr(item, "vessel_code", None)
        if vessel_code is not None:
            vessel_event_key = (vessel_code, event_key)
            if vessel_event_key in vessel_item_by_event_key:
                raise ValueError(
                    "_to_networkx: duplicate vessel node for event key: "
                    f"vessel_code={vessel_code!r}, event={item.event!r}."
                )
            vessel_item_by_event_key[vessel_event_key] = item
            continue

        if event_key in item_by_event_key:
            raise ValueError(f"_to_networkx: duplicate item for event key: event={item.event!r}.")
        item_by_event_key[event_key] = item

    graph.graph["item_by_event_key"] = item_by_event_key
    graph.graph["vessel_item_by_event_key"] = vessel_item_by_event_key

    for arc in _progress(arcs, total=len(arcs), desc="graph arcs"):
        from_node_id = arc.from_node.get_id()
        to_node_id = arc.to_node.get_id()
        if from_node_id == to_node_id:
            from_node = graph.nodes[from_node_id]["node"]
            raise ValueError(
                "_to_networkx: self-loop arc detected. "
                f"node_id={from_node_id!r}, label={from_node.label!r}, arc_type={type(arc).__name__!r}."
            )
        attrs = {
            "arc": arc,
            "arc_id": arc.get_id(),
            "capacity": arc.capacity,
            "cost": arc.cost,
            "arc_type": type(arc).__name__,
        }
        if isinstance(arc, (SailArc, HorizonSailArc)):
            attrs["distance"] = arc.distance
            attrs["sail_time"] = arc.sail_time
        if isinstance(arc, CanalSailArc):
            attrs["canal_port_code"] = arc.canal_port_code
            attrs["canal_direction"] = arc.canal_direction
            attrs["canal_leg1_distance"] = arc.leg1_distance
            attrs["canal_leg1_eca_distance"] = arc.leg1_eca_distance
            attrs["canal_leg2_distance"] = arc.leg2_distance
            attrs["canal_leg2_eca_distance"] = arc.leg2_eca_distance
            attrs["canal_passage_hours"] = arc.passage_hours
        graph.add_edge(from_node_id, to_node_id, key=arc.get_id(), **attrs)
    return graph


def _is_canal_service_edge(graph: nx.DiGraph, from_node_id: str, to_node_id: str) -> bool:
    from_data = graph.nodes[from_node_id]
    to_data = graph.nodes[to_node_id]
    owner = from_data.get("owner")
    return (
        isinstance(owner, NodeGroup)
        and owner is to_data.get("owner")
        and owner.is_canal
        and from_data.get("label") == "pilot_in"
        and to_data.get("label") == "pilot_out"
    )


def _build_arc_between_nodes(from_node: Node, to_node: Node) -> list[Arc]:
    from_port_code = event_end_port_code(from_node.event)
    to_port_code = event_start_port_code(to_node.event)
    if from_port_code == to_port_code:
        return [Arc(from_node=from_node, to_node=to_node, cost=0)]
    sail_start = from_node.node_out_time
    sail_end = to_node.node_in_time
    sail_time = (sail_end - sail_start).total_seconds() / 3600
    distance = lookup_distance(from_port_code, to_port_code)
    if sail_time < 0 or distance / (sail_time + 1e-5) > 20 or (abs(sail_time) < 1e-5 and distance > 1e-5):
        return []
    return [SailArc(from_node=from_node, to_node=to_node, distance=distance, sail_time=sail_time)]


def _required_capacity_interval(lane_code: str, proforma_name: str) -> tuple[float, float]:
    try:
        return CAPACITY_INTERVAL_BY_LANE_VERSION[(lane_code, proforma_name)]
    except KeyError as exc:
        raise ValueError(
            "_required_capacity_interval: unknown lane/proforma. "
            f"lane_code={lane_code!r}, proforma_name={proforma_name!r}."
        ) from exc


def _has_overlapping_capacity_compatibility(left: NodeGroup, right: NodeGroup) -> bool:
    left_lane_code, left_proforma_name, _ = lane_info(left.event)
    right_lane_code, right_proforma_name, _ = lane_info(right.event)
    if (left_lane_code, left_proforma_name) == (right_lane_code, right_proforma_name):
        return True

    left_min, left_max = _required_capacity_interval(left_lane_code, left_proforma_name)
    right_min, right_max = _required_capacity_interval(right_lane_code, right_proforma_name)
    return max(left_min, right_min) <= min(left_max, right_max)


def build_arcs_between(left: Node | NodeGroup, right: Node | NodeGroup) -> list[Arc]:
    # region validation
    if isinstance(left, R) or isinstance(right, D):
        return []
    if isinstance(left, NodeGroup) and isinstance(right, NodeGroup):
        if lane_info(left.event) == lane_info(right.event):
            if isinstance(left, SE):
                raise ValueError("Invalid node group sequence: SE cannot be followed by another node group.")
            if isinstance(right, SS):
                raise ValueError("Invalid node group sequence: SS cannot follow another node group.")

    # endregion

    arcs: list[Arc] = []
    if isinstance(left, NodeGroup) and isinstance(right, NodeGroup):
        if lane_info(left.event) != lane_info(right.event):
            if not _has_overlapping_capacity_compatibility(left, right):
                return []
            for start_node in left.outbound_nodes():
                for end_node in right.inbound_nodes():
                    arcs.extend(_build_arc_between_nodes(start_node, end_node))
    elif isinstance(left, NodeGroup) and isinstance(right, Node):
        for start_node in left.outbound_nodes():
            arcs.extend(_build_arc_between_nodes(start_node, right))
    elif isinstance(left, Node) and isinstance(right, NodeGroup):
        for end_node in right.inbound_nodes():
            arcs.extend(_build_arc_between_nodes(left, end_node))
    elif isinstance(left, Node) and isinstance(right, Node):
        arcs.extend(_build_arc_between_nodes(left, right))
    return arcs


def cascade_delete_digraph(G: nx.DiGraph, V0):
    """
    DiGraph G에서 V0를 삭제하고,
    그 결과 in-degree 또는 out-degree가 0이 된 노드를 반복 삭제한다.

    삭제 규칙:
    - 노드가 삭제되면 그 노드에 incident한 모든 arc도 삭제된 것으로 본다.
    - 어떤 살아 있는 노드의 in-degree == 0 또는 out-degree == 0 이 되면 삭제 대상이 된다.

    Returns
    -------
    G
        삭제 후 남은 그래프 copy.
    """

    initial_num_edges = G.number_of_edges()
    alive = set(G.nodes)

    in_deg = dict(G.in_degree())
    out_deg = dict(G.out_degree())

    q = deque()
    queued = set()

    deleted_nodes = []

    def enqueue(v):
        if v in alive and v not in queued:
            q.append(v)
            queued.add(v)

    # 최초 삭제 집합
    for v in V0:
        enqueue(v)

    while q:
        u = q.popleft()
        queued.discard(u)

        if u not in alive:
            continue

        # u 삭제
        alive.remove(u)
        deleted_nodes.append(u)

        # u -> v edge 삭제
        for _, v in G.out_edges(u):
            # v가 아직 살아 있으면 이 edge는 지금 삭제되는 것
            # self-loop는 u 삭제 시 같이 삭제되므로 따로 기록

            if v in alive:
                in_deg[v] -= 1
                is_terminal_sink = isinstance(G.nodes[v]["node"], (I, T))

                if in_deg[v] == 0 or (out_deg[v] == 0 and not is_terminal_sink):
                    enqueue(v)

        # w -> u edge 삭제
        for w, _ in G.in_edges(u):
            # 이미 out_edges에서 self-loop는 기록했으므로 w == u는 제외
            # w가 이미 죽은 경우 그 edge는 이전에 이미 삭제된 것

            if w in alive:
                out_deg[w] -= 1
                is_terminal_sink = isinstance(G.nodes[w]["node"], (I, T))

                if in_deg[w] == 0 or (out_deg[w] == 0 and not is_terminal_sink):
                    enqueue(w)

    G.remove_nodes_from(deleted_nodes)
    _progress_write(
        f"cascade_delete_digraph: deleted nodes={len(deleted_nodes)}, "
        f"deleted arcs={initial_num_edges - G.number_of_edges()}"
    )

    return G


# lane nodes를 생성하기 위해 인스턴스 데이터로부터 기항 계획 가져오기
def _generate_port_stays(
    version: dict[str, Any],
    lane_key: tuple[str, str, int],
    service_start: datetime,
    service_end: datetime,
    filter_to_planning: bool = True,
) -> list[PortStay]:
    if PLANNING_START is None or PLANNING_END is None:
        raise ValueError("_generate_port_stays: initialize_network() must be called first.")

    lane_code, proforma_name, position_no = lane_key
    port_stays: list[PortStay] = []
    service_duration = version["service_duration"]
    n_round_trips = 0
    while service_start + timedelta(days=n_round_trips * service_duration) < service_end:
        trip_offset = service_start + timedelta(days=n_round_trips * service_duration)
        next_offset = trip_offset + timedelta(days=service_duration)
        rotations = version["port_rotation"] if next_offset >= service_end else version["port_rotation"][:-1]
        for rotation in rotations:
            port_stay = PortStay(
                lane_code=lane_code,
                proforma_name=proforma_name,
                position_no=position_no,
                port_code=rotation["port_code"],
                port_seq=rotation["port_seq"],
                pilot_in_start=trip_offset + timedelta(minutes=rotation["eta_offset_minutes"]),
                berthing_start=trip_offset + timedelta(minutes=rotation["etb_offset_minutes"]),
                berthing_end=trip_offset + timedelta(minutes=rotation["etd_offset_minutes"]),
                pilot_out_end=trip_offset
                + timedelta(minutes=rotation["etd_offset_minutes"] + rotation["pilot_out_minutes"]),
            )
            port_stay.direction = rotation["direction"]
            if (
                not filter_to_planning
                or port_stay.pilot_in_start <= PLANNING_END
                and port_stay.pilot_out_end >= PLANNING_START
            ):
                port_stays.append(port_stay)
        n_round_trips += 1
    return port_stays


def _current_assignment_by_lane_key(lane_keys: list[tuple[str, str, int]]) -> dict[tuple[str, str, int], str]:
    if INSTANCE_DATA is None:
        raise ValueError("_current_assignment_by_lane_key: initialize_network() must be called first.")

    lane_key_set = set(lane_keys)
    current_assignment_by_lane_key = {}
    for lane in INSTANCE_DATA.service_lanes:
        lane_code = lane["lane_code"]
        for version in lane["versions"]:
            proforma_name = version["proforma_name"]
            for assignment in version["vessel_assignments"]:
                lane_key = (lane_code, proforma_name, assignment["position_no"])
                if lane_key not in lane_key_set:
                    continue
                if lane_key in current_assignment_by_lane_key:
                    raise ValueError(f"_current_assignment_by_lane_key: duplicate current assignment: {lane_key!r}.")
                current_assignment_by_lane_key[lane_key] = assignment["vessel_code"]
    return current_assignment_by_lane_key


def _horizon_start_lane_keys(
    lane_keys: list[tuple[str, str, int]],
    current_assignment_by_lane_key: dict[tuple[str, str, int], str],
) -> set[tuple[str, str, int]]:
    if INSTANCE_DATA is None or PLANNING_START is None or PLANNING_END is None:
        raise ValueError("_horizon_start_lane_keys: initialize_network() must be called first.")

    all_lanes = set([lane[0] for lane in lane_keys])
    all_lane_positions = set([lane[:2] for lane in lane_keys])
    positions_by_version: dict[tuple[str, str], list[int]] = {}
    for lane_code, proforma_name, position_no in lane_keys:
        positions_by_version.setdefault((lane_code, proforma_name), []).append(position_no)

    horizon_start_lane_keys = set()
    for lane in INSTANCE_DATA.service_lanes:
        lane_code = lane["lane_code"]
        if lane_code not in all_lanes:
            continue
        for version in lane["versions"]:
            proforma_name = version["proforma_name"]
            if (lane_code, proforma_name) not in all_lane_positions:
                continue
            for position_no in sorted(positions_by_version[(lane_code, proforma_name)]):
                lane_key = (lane_code, proforma_name, position_no)
                if lane_key not in current_assignment_by_lane_key:
                    continue
                service_start = get_service_start_datetime(*lane_key)
                service_end = get_service_end_datetime(*lane_key)
                all_port_stays = _generate_port_stays(
                    version, lane_key, service_start, service_end, filter_to_planning=False
                )
                for left_stay, right_stay in zip(all_port_stays, all_port_stays[1:]):
                    if not (left_stay.pilot_out_end < PLANNING_START < right_stay.pilot_in_start):
                        continue
                    if not (right_stay.pilot_in_start < PLANNING_END and right_stay.pilot_out_end > PLANNING_START):
                        continue
                    horizon_start_lane_keys.add(lane_key)
                    break
    return horizon_start_lane_keys


def _build_lane_nodes(lane_keys: list[tuple[str, str, int]]) -> dict[tuple[str, str, int], list[Node | NodeGroup]]:
    if INSTANCE_DATA is None or PLANNING_START is None or PLANNING_END is None:
        raise ValueError("_build_lane_nodes: initialize_network() must be called first.")

    current_assignment_by_lane_key = _current_assignment_by_lane_key(lane_keys)
    horizon_start_lane_keys = _horizon_start_lane_keys(lane_keys, current_assignment_by_lane_key)

    all_lanes = set([lane[0] for lane in lane_keys])
    all_lane_positions = set([lane[:2] for lane in lane_keys])
    positions_by_version: dict[tuple[str, str], list[int]] = {}
    for lane_code, proforma_name, position_no in lane_keys:
        positions_by_version.setdefault((lane_code, proforma_name), []).append(position_no)

    lane_nodes: dict[tuple[str, str, int], list[Node | NodeGroup]] = {}

    for lane in _progress(INSTANCE_DATA.service_lanes, total=len(INSTANCE_DATA.service_lanes), desc="lane nodes"):
        lane_code = lane["lane_code"]
        if lane_code not in all_lanes:
            continue

        for version in lane["versions"]:
            proforma_name = version["proforma_name"]
            if (lane_code, proforma_name) not in all_lane_positions:
                continue

            for position_no in sorted(positions_by_version[(lane_code, proforma_name)]):
                lane_key = (lane_code, proforma_name, position_no)
                service_start = get_service_start_datetime(*lane_key)
                service_end = get_service_end_datetime(*lane_key)
                lane_nodes[lane_key] = []
                all_port_stays = _generate_port_stays(
                    version, lane_key, service_start, service_end, filter_to_planning=False
                )
                port_stays = [
                    port_stay
                    for port_stay in all_port_stays
                    if port_stay.pilot_in_start <= PLANNING_END and port_stay.pilot_out_end >= PLANNING_START
                ]
                port_stays = [
                    port_stay
                    for port_stay in port_stays
                    if port_stay.pilot_in_start < PLANNING_END and port_stay.pilot_out_end > PLANNING_START
                ]
                if lane_key in horizon_start_lane_keys:
                    for left_stay, right_stay in zip(all_port_stays, all_port_stays[1:]):
                        if not (left_stay.pilot_out_end < PLANNING_START < right_stay.pilot_in_start):
                            continue
                        if not (right_stay.pilot_in_start < PLANNING_END and right_stay.pilot_out_end > PLANNING_START):
                            continue
                        distance = lookup_distance(left_stay.port_code, right_stay.port_code)
                        sail_hours = (right_stay.pilot_in_start - left_stay.pilot_out_end).total_seconds() / 3600
                        lane_nodes[lane_key].append(
                            HorizonSailNode(
                                event=InLaneSail(
                                    lane_code=left_stay.lane_code,
                                    proforma_name=left_stay.proforma_name,
                                    position_no=left_stay.position_no,
                                    from_port_code=left_stay.port_code,
                                    from_port_seq=left_stay.port_seq,
                                    sea_sail_start=left_stay.pilot_out_end,
                                    to_port_code=right_stay.port_code,
                                    to_port_seq=right_stay.port_seq,
                                    sea_sail_end=right_stay.pilot_in_start,
                                    distance=distance,
                                    avg_speed=distance / sail_hours if sail_hours else None,
                                ),
                                horizon_side="start",
                            )
                        )
                        break
                for port_stay_index, port_stay in enumerate(port_stays):
                    is_last = port_stay_index == len(port_stays) - 1
                    is_first = lane_key in current_assignment_by_lane_key and port_stay_index == 0
                    if port_stay.pilot_in_start == service_start:
                        node = SS(service_start, port_stay)
                    elif port_stay.pilot_in_start == service_end:
                        node = SE(service_end, port_stay, is_first=is_first)
                    else:
                        node = PS(port_stay, is_last=is_last, is_first=is_first)
                    lane_nodes[lane_key].append(node)

                if lane_nodes[lane_key]:
                    last_group = next(
                        (item for item in reversed(lane_nodes[lane_key]) if isinstance(item, NodeGroup)),
                        None,
                    )
                    if last_group is None:
                        continue
                    last_event = last_group.event
                    if last_event.pilot_out_end < PLANNING_END:
                        next_port_stay = next(
                            (
                                port_stay
                                for port_stay in all_port_stays
                                if last_event.pilot_out_end < PLANNING_END < port_stay.pilot_in_start
                            ),
                            None,
                        )
                        if next_port_stay is not None:
                            distance = lookup_distance(last_event.port_code, next_port_stay.port_code)
                            sail_hours = (
                                next_port_stay.pilot_in_start - last_event.pilot_out_end
                            ).total_seconds() / 3600
                            lane_nodes[lane_key].append(
                                HorizonSailNode(
                                    event=InLaneSail(
                                        lane_code=last_event.lane_code,
                                        proforma_name=last_event.proforma_name,
                                        position_no=last_event.position_no,
                                        from_port_code=last_event.port_code,
                                        from_port_seq=last_event.port_seq,
                                        sea_sail_start=last_event.pilot_out_end,
                                        to_port_code=next_port_stay.port_code,
                                        to_port_seq=next_port_stay.port_seq,
                                        sea_sail_end=next_port_stay.pilot_in_start,
                                        distance=distance,
                                        avg_speed=distance / sail_hours if sail_hours else None,
                                    ),
                                    horizon_side="end",
                                )
                            )
    return lane_nodes


def _current_assignment_source_by_vessel(
    lane_nodes: dict[tuple[str, str, int], list[Node | NodeGroup]],
    current_assignment_by_lane_key: dict[tuple[str, str, int], str],
) -> dict[str, Node]:
    current_assignment_source_by_vessel = {}
    for lane_key, vessel_code in current_assignment_by_lane_key.items():
        nodes = lane_nodes.get(lane_key)
        if not nodes:
            continue
        first_item = nodes[0]
        if isinstance(first_item, HorizonSailNode):
            if first_item.horizon_side != "start":
                raise ValueError(f"_current_assignment_source_by_vessel: invalid first horizon node: {lane_key!r}.")
            current_assignment_source_by_vessel[vessel_code] = first_item
            continue
        if isinstance(first_item, NodeGroup):
            first_item.pilot_in.is_first = True
            current_assignment_source_by_vessel[vessel_code] = first_item.pilot_in
            continue
        raise TypeError(f"_current_assignment_source_by_vessel: unexpected lane item type: {type(first_item)!r}.")
    return current_assignment_source_by_vessel


def _lane_keys_from_positions(declared_positions: list[DeclaredPosition]) -> list[tuple[str, str, int]]:
    if INSTANCE_DATA is None:
        raise ValueError("_declared_lane_keys: initialize_network() must be called first.")

    version_by_key = {
        (lane["lane_code"], version["proforma_name"]): version
        for lane in INSTANCE_DATA.service_lanes
        for version in lane["versions"]
    }

    lane_keys: set[tuple[str, str, int]] = set()
    duplicate_check: set[tuple[str, str, int]] = set()
    for declared_position in declared_positions:
        lane_key = (
            declared_position.lane_code,
            declared_position.proforma_name,
            declared_position.declared_position_no,
        )
        if lane_key in duplicate_check:
            raise ValueError(f"_declared_lane_keys: duplicate declared position: {lane_key!r}.")
        duplicate_check.add(lane_key)

        version = version_by_key.get((declared_position.lane_code, declared_position.proforma_name))
        if version is None:
            raise ValueError(f"_declared_lane_keys: declared position references unknown version: {lane_key!r}.")

        allowed_positions = version.get("declared_positions") or version.get("available_positions") or []
        if declared_position.declared_position_no not in allowed_positions:
            raise ValueError(
                "_declared_lane_keys: declared position number is not allowed by instance data. "
                f"lane_key={lane_key!r}, allowed_positions={allowed_positions!r}."
            )

        lane_keys.add(lane_key)

    return sorted(lane_keys)


def _build_vessel_nodes_from_instance() -> list[Node]:
    vessel_nodes: list[Node] = []
    for vessel in sorted(INSTANCE_DATA.vessels, key=lambda item: item["vessel_code"]):
        vessel_code = vessel["vessel_code"]
        if vessel["available_from"] is not None:
            vessel_nodes.append(
                D(
                    vessel_code,
                    Delivery(
                        delivery_port_code=vessel["available_from_port_code"],
                        delivery_time=vessel["available_from"],
                    ),
                )
            )
        if vessel["available_to"] is not None and vessel["available_to"] <= PLANNING_END:
            vessel_nodes.append(
                R(
                    vessel_code,
                    Redelivery(
                        redelivery_port_code=vessel["available_to_port_code"],
                        redelivery_time=vessel["available_to"],
                    ),
                )
            )
    return vessel_nodes


def construct_network(network_positions: list[DeclaredPosition]) -> nx.DiGraph:
    if INSTANCE_DATA is None or PLANNING_START is None or PLANNING_END is None:
        raise ValueError("construct_network: initialize_network() must be called first.")

    start_time = perf_counter()
    checkpoint = start_time

    def log_stage(message: str) -> None:
        nonlocal checkpoint
        now = perf_counter()
        _progress_write(f"[construct_network] {message} | step={now - checkpoint:.2f}s total={now - start_time:.2f}s")
        checkpoint = now

    log_stage(f"start network_positions={len(network_positions)}")

    all_lane_keys = _lane_keys_from_positions(network_positions)
    log_stage(f"collected related lanes count={len(all_lane_keys)}")

    vessel_nodes = _build_vessel_nodes_from_instance()
    log_stage(f"built vessel nodes count={len(vessel_nodes)}")

    lane_nodes = _build_lane_nodes(all_lane_keys)
    node_group_count = sum(1 for nodes in lane_nodes.values() for item in nodes if isinstance(item, NodeGroup))

    log_stage(
        "built lane nodes "
        f"lane_positions={len(lane_nodes)} lane_items={sum(len(nodes) for nodes in lane_nodes.values())} "
        f"node_groups={node_group_count} "
        f"horizon_end_sails={sum(
        1
        for nodes in lane_nodes.values()
        for item in nodes 
        if isinstance(item, HorizonSailNode) and item.horizon_side == "end"
    )} "
        f"horizon_start_sails={sum(
        1
        for nodes in lane_nodes.values()
        for item in nodes
        if isinstance(item, HorizonSailNode) and item.horizon_side == "start"
    )}"
    )
    network_items: list[Node | NodeGroup] = vessel_nodes + [node for nodes in lane_nodes.values() for node in nodes]
    arcs: list[Arc] = []
    log_stage(f"prepared network items count={len(network_items)}")

    current_assignment_by_lane_key = _current_assignment_by_lane_key(all_lane_keys)
    log_stage(f"collected current assignments count={len(current_assignment_by_lane_key)}")

    current_assignment_source_by_vessel = _current_assignment_source_by_vessel(
        lane_nodes,
        current_assignment_by_lane_key,
    )
    log_stage(f"identified current assignment sources count={len(current_assignment_source_by_vessel)}")

    delivery_node_by_vessel: dict[str, D] = {}
    for node in vessel_nodes:
        if not isinstance(node, D):
            continue
        if node.vessel_code in delivery_node_by_vessel:
            raise ValueError(
                "construct_network: multiple delivery nodes for vessel. " f"vessel_code={node.vessel_code!r}."
            )
        delivery_node_by_vessel[node.vessel_code] = node

    # Delivery 직후에 current assignment source로 바로 Phase-in 하도록 입력으로 예약된 케이스.
    # 이 경우 Delivery를 source node로 삼고 D의 outbound arc는 current assignment source로 가는 아크만 둔다.
    committed_delivery_arcs: dict[str, tuple[D, Node]] = {}
    for vessel_code, delivery_node in delivery_node_by_vessel.items():
        current_assignment_source = current_assignment_source_by_vessel.get(vessel_code)
        if current_assignment_source is None:
            continue
        if not (
            PLANNING_START <= delivery_node.node_out_time <= current_assignment_source.node_in_time <= PLANNING_END
        ):
            raise ValueError(
                "construct_network: invalid delivery-to-current-assignment timing. "
                f"vessel_code={vessel_code!r}, "
                f"planning_start={PLANNING_START!r}, delivery_out={delivery_node.node_out_time!r}, "
                f"source_in={current_assignment_source.node_in_time!r}, planning_end={PLANNING_END!r}."
            )
        committed_delivery_arcs[vessel_code] = (delivery_node, current_assignment_source)
    committed_delivery_node_ids = {delivery_node.get_id() for delivery_node, _ in committed_delivery_arcs.values()}
    log_stage(f"identified delivery-to-current-assignment commitments count={len(committed_delivery_arcs)}")

    # 연속된 이벤트'만' pilot_in -> pilot_out 연결을 만들기 위해 따로 계산
    for lane_key, nodes in _progress(lane_nodes.items(), total=len(lane_nodes), desc="sequential lane arcs"):
        for i in range(len(nodes) - 1):
            left = nodes[i]
            right = nodes[i + 1]
            if isinstance(left, HorizonSailNode) and isinstance(right, NodeGroup):
                sail_time = (left.node_out_time - left.node_in_time).total_seconds() / 3600
                arcs.append(
                    HorizonSailArc(
                        from_node=left,
                        to_node=right.pilot_in,
                        distance=left.distance,
                        sail_time=sail_time,
                    )
                )
                continue
            if isinstance(left, NodeGroup) and isinstance(right, HorizonSailNode):
                sail_time = (right.node_out_time - left.pilot_out.node_out_time).total_seconds() / 3600
                arcs.append(
                    HorizonSailArc(
                        from_node=left.pilot_out,
                        to_node=right,
                        distance=right.distance,
                        sail_time=sail_time,
                    )
                )
                continue
            if isinstance(left, NodeGroup) and isinstance(right, NodeGroup):
                arcs.extend(_build_arc_between_nodes(left.pilot_out, right.pilot_in))
                has_ts_in_after = hasattr(left, "ts_in_after")
                has_ts_out_before = hasattr(right, "ts_out_before")
                if has_ts_in_after:
                    arcs.extend(_build_arc_between_nodes(left.ts_in_after, right.pilot_in))
                    if has_ts_out_before:
                        arcs.extend(_build_arc_between_nodes(left.ts_in_after, right.ts_out_before))
                if has_ts_out_before:
                    arcs.extend(_build_arc_between_nodes(left.pilot_out, right.ts_out_before))
                continue
            raise TypeError(
                "construct_network: invalid lane item sequence. "
                f"lane_key={lane_key!r}, left={type(left).__name__}, right={type(right).__name__}."
            )
    log_stage(f"built sequential lane arcs total_arcs={len(arcs)}")

    for item in _progress(network_items, total=len(network_items), desc="internal arcs"):
        if isinstance(item, NodeGroup):
            arcs.extend(item.build_internal_arcs())
    log_stage(f"built internal arcs total_arcs={len(arcs)}")

    horizon_sail_node_ids = {
        item.get_id() for nodes in lane_nodes.values() for item in nodes if isinstance(item, HorizonSailNode)
    }

    for vessel_code, (delivery_node, current_assignment_source) in committed_delivery_arcs.items():
        delivery_to_source_arcs = _build_arc_between_nodes(delivery_node, current_assignment_source)
        if len(delivery_to_source_arcs) == 0:
            raise ValueError(
                "construct_network: unable to build feasible delivery-to-current-assignment arc. "
                f"vessel_code={vessel_code!r}, delivery_node_id={delivery_node.get_id()!r}, "
                f"source_node_id={current_assignment_source.get_id()!r}."
            )
        arcs.extend(delivery_to_source_arcs)
    log_stage(f"built delivery-to-current-assignment commitment arcs total_arcs={len(arcs)}")

    horizon_tail_pilot_out_ids = set()
    for nodes in lane_nodes.values():
        if len(nodes) >= 2 and isinstance(nodes[-1], HorizonSailNode) and isinstance(nodes[-2], NodeGroup):
            horizon_tail_pilot_out_ids.add(nodes[-2].pilot_out.get_id())
    for left in _progress(network_items, total=len(network_items), desc="cross arcs"):
        if isinstance(left, D) and left.get_id() in committed_delivery_node_ids:
            continue
        if isinstance(left, Node) and left.get_id() in horizon_sail_node_ids:
            continue
        for right in network_items:
            if isinstance(right, Node) and right.get_id() in horizon_sail_node_ids:
                continue
            if left is right or event_end_time(left.event) > event_start_time(right.event):
                continue
            next_arcs = build_arcs_between(left, right)
            if isinstance(left, NodeGroup) and left.pilot_out.get_id() in horizon_tail_pilot_out_ids:
                next_arcs = [arc for arc in next_arcs if arc.from_node.get_id() not in horizon_tail_pilot_out_ids]
            arcs.extend(next_arcs)
    log_stage(f"built cross arcs total_arcs={len(arcs)}")

    target_node = T(event=Idle(port_code="TARGET", idle_start=PLANNING_END, idle_end=PLANNING_END))
    network_items.append(target_node)
    target_source_node_ids = set()
    for lane_key, nodes in _progress(lane_nodes.items(), total=len(lane_nodes), desc="target arcs"):
        if len(nodes) == 0:
            continue
        last_item = nodes[-1]
        if isinstance(last_item, HorizonSailNode):
            if last_item.horizon_side != "end":
                raise ValueError(f"construct_network: invalid horizon node at lane tail: lane_key={lane_key!r}.")
            arcs.append(Arc(from_node=last_item, to_node=target_node, cost=0))
            target_source_node_ids.add(last_item.get_id())
            if len(nodes) >= 2 and isinstance(nodes[-2], NodeGroup):
                target_source_node_ids.add(nodes[-2].pilot_out.get_id())
            continue

        last_group = last_item
        if not isinstance(last_group, NodeGroup):
            raise TypeError(
                "construct_network: invalid lane tail item. "
                f"lane_key={lane_key!r}, item_type={type(last_group).__name__}."
            )
        candidate_nodes = last_group.outbound_nodes()
        if len(candidate_nodes) == 0:
            raise ValueError(
                "construct_network: unable to find last node candidate for lane. "
                f"lane_key={lane_key!r}, last_group_id={last_group.get_id()!r}."
            )
        last_node = sorted(candidate_nodes, key=lambda node: node.node_out_time)[-1]
        arcs.append(Arc(from_node=last_node, to_node=target_node, cost=0))
        target_source_node_ids.add(last_node.get_id())
    log_stage(f"built target arcs total_arcs={len(arcs)}")

    idle_node = I(event=Idle(port_code="IDLE", idle_start=PLANNING_END, idle_end=PLANNING_END))
    network_items.append(idle_node)
    for item in _progress(network_items, total=len(network_items), desc="idle arcs"):
        if item is idle_node or item is target_node:
            continue
        if isinstance(item, NodeGroup):
            for node in item.outbound_nodes():
                if node.get_id() in target_source_node_ids:
                    continue
                arcs.append(Arc(from_node=node, to_node=idle_node, cost=0))
        else:
            if item.get_id() in horizon_sail_node_ids:
                continue
            if item.get_id() in target_source_node_ids:
                continue
            if isinstance(item, D) and item.get_id() in committed_delivery_node_ids:
                continue
            arcs.append(Arc(from_node=item, to_node=idle_node, cost=0))
    arcs.append(Arc(from_node=idle_node, to_node=target_node, cost=0))
    log_stage(f"built idle arcs total_arcs={len(arcs)} network_items={len(network_items)}")

    _progress_write("network constructed")

    graph = _to_networkx(network_items, arcs)
    graph.graph["related_lane_keys"] = all_lane_keys
    graph.graph["current_assignment_source_node_id_by_vessel"] = {
        vessel_code: node.get_id() for vessel_code, node in current_assignment_source_by_vessel.items()
    }
    _progress_write("networkx done")
    log_stage(f"converted to networkx nodes={graph.number_of_nodes()} edges={graph.number_of_edges()}")

    # network size brief
    graph_items = {
        data["owner"].get_id(): data["owner"] for _, data in graph.nodes(data=True) if data.get("owner") is not None
    }.values()
    _progress_write(
        f"- nodes: {graph.number_of_nodes()}\n"
        f"- arcs: {graph.number_of_edges()}\n"
        f"  - sail arcs: {sum(1 for _, _, data in graph.edges(data=True) if data['arc_type'] == 'SailArc')}\n"
        f"  - canal service arcs: {sum(1 for u, v in graph.edges if _is_canal_service_edge(graph, u, v))}\n"
        f"- node groups: {len([item for item in graph_items if isinstance(item, NodeGroup)])}\n"
        f"  - PS: {len([item for item in graph_items if isinstance(item, PS)])}\n"
        f"  - SS: {len([item for item in graph_items if isinstance(item, SS)])}\n"
        f"  - SE: {len([item for item in graph_items if isinstance(item, SE)])}\n"
        f"  - D: {len([item for item in graph_items if isinstance(item, D)])}\n"
        f"  - R: {len([item for item in graph_items if isinstance(item, R)])}\n"
        f"  - T: {len([item for item in graph_items if isinstance(item, T)])}\n"
        f"  - I: {len([item for item in graph_items if isinstance(item, I)])}\n"
    )
    # viz_zero_arcs(network_items=network_items, arcs=arcs)
    # viz_graph(graph)
    log_stage("finished")
    return graph
