from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .nodes import Node

_NEXT_ARC_ID = 0


def _new_arc_id() -> str:
    global _NEXT_ARC_ID

    arc_id = f"A{_NEXT_ARC_ID}"
    _NEXT_ARC_ID += 1
    return arc_id


class Arc:
    def __init__(self, from_node: Node, to_node: Node, cost: float | None = None, capacity: int = 1):
        self._id = _new_arc_id()
        self.from_node = from_node
        self.to_node = to_node
        self.cost = cost
        self.capacity = capacity

    def get_id(self) -> str:
        return self._id


class InternalArc(Arc):
    pass


class SailArc(Arc):
    def __init__(self, from_node: Node, to_node: Node, distance: float, sail_time: float):
        super().__init__(from_node, to_node)
        self.distance = distance
        self.sail_time = sail_time


class CanalSailArc(SailArc):
    def __init__(
        self,
        from_node: Node,
        to_node: Node,
        distance: float,
        sail_time: float,
        canal_port_code: str,
        canal_direction: str,
        leg1_distance: float,
        leg1_eca_distance: float,
        leg2_distance: float,
        leg2_eca_distance: float,
        passage_hours: float,
    ):
        super().__init__(from_node, to_node, distance, sail_time)
        self.canal_port_code = canal_port_code
        self.canal_direction = canal_direction
        self.leg1_distance = leg1_distance
        self.leg1_eca_distance = leg1_eca_distance
        self.leg2_distance = leg2_distance
        self.leg2_eca_distance = leg2_eca_distance
        self.passage_hours = passage_hours


class HorizonSailArc(Arc):
    def __init__(self, from_node: Node, to_node: Node, distance: float, sail_time: float):
        super().__init__(from_node, to_node)
        self.distance = distance
        self.sail_time = sail_time
