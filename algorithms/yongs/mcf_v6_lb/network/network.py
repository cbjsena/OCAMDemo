from __future__ import annotations

import os

import networkx as nx

try:
    from tqdm import tqdm
except ImportError:

    def tqdm(iterable, **kwargs):
        return iterable


from ocam.models import VesselScheduleEvent
from ocam.utils import event_start_port_code

from .nodes import I, Node, NodeGroup
from .arcs import Arc, CanalSailArc, HorizonSailArc, SailArc

IDLE_EVENT_KEY = (("__idle__", True),)


def _progress(iterable, **kwargs):
    kwargs.setdefault("dynamic_ncols", True)
    kwargs.setdefault("leave", False)
    kwargs.setdefault("mininterval", 1.0)
    kwargs.setdefault("disable", not os.isatty(2))
    return tqdm(iterable, **kwargs)


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
