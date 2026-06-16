from __future__ import annotations

from typing import Any, Literal

from ocam.models import *
from ocam.utils import *

from .arcs import Arc

_NEXT_NODE_ID = 0
_NEXT_NODE_GROUP_ID = 0


def _new_node_id() -> str:
    global _NEXT_NODE_ID

    node_id = f"N{_NEXT_NODE_ID}"
    _NEXT_NODE_ID += 1
    return node_id


def _new_node_group_id() -> str:
    global _NEXT_NODE_GROUP_ID

    node_group_id = f"G{_NEXT_NODE_GROUP_ID}"
    _NEXT_NODE_GROUP_ID += 1
    return node_group_id


class Node:
    def __init__(
        self,
        event: VesselScheduleEvent,
        label: str = "",
        node_in_time: datetime | None = None,
        node_out_time: datetime | None = None,
    ):
        self._id = _new_node_id()
        self.event = event
        self.label = label
        self.node_in_time = node_in_time if node_in_time is not None else event_start_time(event)
        self.node_out_time = node_out_time if node_out_time is not None else event_end_time(event)

    def get_id(self) -> str:
        return self._id


class HorizonSailNode(Node):
    def __init__(self, event: InLaneSail, horizon_side: Literal["start", "end"]):
        if not isinstance(event, InLaneSail):
            raise TypeError(f"HorizonSailNode requires InLaneSail event, got {type(event).__name__}.")
        if horizon_side not in {"start", "end"}:
            raise ValueError(f"HorizonSailNode has invalid horizon_side: {horizon_side!r}.")
        self.lane_code = event.lane_code
        self.proforma_name = event.proforma_name
        self.position_no = event.position_no
        self.from_port_code = event.from_port_code
        self.from_port_seq = event.from_port_seq
        self.sea_sail_start = event.sea_sail_start
        self.to_port_code = event.to_port_code
        self.to_port_seq = event.to_port_seq
        self.sea_sail_end = event.sea_sail_end
        self.distance = event.distance
        self.avg_speed = event.avg_speed
        missing_fields = [
            field_name
            for field_name in (
                "lane_code",
                "proforma_name",
                "position_no",
                "from_port_code",
                "from_port_seq",
                "sea_sail_start",
                "to_port_code",
                "to_port_seq",
                "sea_sail_end",
                "distance",
            )
            if getattr(self, field_name) is None
        ]
        if missing_fields:
            raise ValueError(f"HorizonSailNode has incomplete InLaneSail data: {missing_fields!r}.")
        super().__init__(event=event, label="horizon_sail")
        self.horizon_side = horizon_side
        self.is_first = horizon_side == "start"

    def to_inlane_sail(self) -> InLaneSail:
        return InLaneSail(
            lane_code=self.lane_code,
            proforma_name=self.proforma_name,
            position_no=self.position_no,
            from_port_code=self.from_port_code,
            from_port_seq=self.from_port_seq,
            sea_sail_start=self.sea_sail_start,
            to_port_code=self.to_port_code,
            to_port_seq=self.to_port_seq,
            sea_sail_end=self.sea_sail_end,
            distance=self.distance,
            avg_speed=self.avg_speed,
        )


class NodeGroup:
    def __init__(self, event: PortStay):
        self._id = _new_node_group_id()
        self.event = event
        self.is_canal = is_canal_port(event.port_code)
        self.direction = getattr(event, "direction", None)
        self._ts_cost = None

    def get_id(self) -> str:
        return self._id

    def get_nodes(self) -> list[Node]:
        return [value for value in self.__dict__.values() if isinstance(value, Node)]

    def get_ts_cost(self):
        if self._ts_cost is None:
            self._ts_cost = lookup_ts_cost(
                to_year_month(self.event.pilot_out_end),
                self.event.lane_code,
                self.event.port_code,
            )
        return self._ts_cost

    def build_internal_arcs(self) -> list["Arc"]:
        raise NotImplementedError()

    def build_service_arc(self) -> Arc:
        return Arc(from_node=self.pilot_in, to_node=self.pilot_out, cost=0)

    def inbound_nodes(self) -> list[Node]:
        raise NotImplementedError()

    def outbound_nodes(self) -> list[Node]:
        raise NotImplementedError()


class PS(NodeGroup):
    def __init__(self, event: PortStay, is_last: bool = False, is_first: bool = False):
        super().__init__(event=event)
        self.is_last = is_last
        self.is_first = is_first
        self.pilot_in = Node(event=event, label="pilot_in", node_out_time=event_start_time(event))
        self.pilot_out = Node(event=event, label="pilot_out", node_in_time=event_end_time(event))
        self.pilot_in.is_first = is_first
        if not self.is_canal and not self.is_first:
            self.ts_in_before = Node(
                event=event,
                label="ts_in_before",
                node_in_time=event_start_time(event) - timedelta(hours=6),
                node_out_time=event_start_time(event),
            )
            self.ts_out_before = Node(
                event=event,
                label="ts_out_before",
                node_in_time=event_start_time(event) - timedelta(hours=36),
                node_out_time=event_start_time(event) - timedelta(hours=30),
            )
        if not self.is_canal and not self.is_last:
            self.ts_in_after = Node(
                event=event,
                label="ts_in_after",
                node_in_time=event_end_time(event) + timedelta(hours=30),
                node_out_time=event_end_time(event) + timedelta(hours=36),
            )
            self.ts_out_after = Node(
                event=event,
                label="ts_out_after",
                node_in_time=event_end_time(event),
                node_out_time=event_end_time(event) + timedelta(hours=6),
            )

    def build_internal_arcs(self) -> list[Arc]:
        arcs = [self.build_service_arc()]
        if hasattr(self, "ts_in_before"):
            arcs.append(Arc(from_node=self.ts_in_before, to_node=self.pilot_in))
        if hasattr(self, "ts_out_after"):
            arcs.append(Arc(from_node=self.pilot_out, to_node=self.ts_out_after))
        return arcs

    def inbound_nodes(self) -> list[Node]:
        if self.is_canal:
            return []
        nodes = []
        if hasattr(self, "ts_in_before"):
            nodes.append(self.ts_in_before)
        if hasattr(self, "ts_in_after"):
            nodes.append(self.ts_in_after)
        return nodes

    def outbound_nodes(self) -> list[Node]:
        nodes = []
        if hasattr(self, "ts_out_before"):
            nodes.append(self.ts_out_before)
        if self.is_last:
            nodes.append(self.pilot_out)
        elif hasattr(self, "ts_out_after"):
            nodes.append(self.ts_out_after)
        return nodes


class SS(NodeGroup):
    def __init__(self, service_start: datetime, event: PortStay):
        super().__init__(event=event)
        self.service_start = service_start
        self.ss = Node(event=event, label="ss", node_out_time=event_start_time(event))
        self.pilot_in = Node(event=event, label="pilot_in", node_out_time=event_start_time(event))
        self.pilot_out = Node(event=event, label="pilot_out", node_in_time=event_end_time(event))
        if not self.is_canal:
            self.ts_in_after = Node(
                event=event,
                label="ts_in_after",
                node_in_time=event_end_time(event) + timedelta(hours=30),
                node_out_time=event_end_time(event) + timedelta(hours=36),
            )
            self.ts_out_after = Node(
                event=event,
                label="ts_out_after",
                node_in_time=event_end_time(event),
                node_out_time=event_end_time(event) + timedelta(hours=6),
            )

    def build_internal_arcs(self) -> list[Arc]:
        arcs = [
            Arc(from_node=self.ss, to_node=self.pilot_in, cost=0),
            self.build_service_arc(),
        ]
        if not self.is_canal:
            arcs.extend([Arc(from_node=self.pilot_out, to_node=self.ts_out_after)])
        return arcs

    def inbound_nodes(self) -> list[Node]:
        return [self.ts_in_after, self.ss] if not self.is_canal else [self.ss]

    def outbound_nodes(self) -> list[Node]:
        return [self.ts_out_after] if not self.is_canal else []


class SE(NodeGroup):
    def __init__(self, service_end: datetime, event: PortStay, is_first: bool = False):
        super().__init__(event=event)
        self.service_end = service_end
        self.is_first = is_first
        self.se = Node(event=event, label="se", node_in_time=event_end_time(event))
        self.pilot_in = Node(event=event, label="pilot_in", node_out_time=event_start_time(event))
        self.pilot_out = Node(event=event, label="pilot_out", node_in_time=event_end_time(event))
        self.pilot_in.is_first = is_first
        if not self.is_canal and not self.is_first:
            self.ts_in_before = Node(
                event=event,
                label="ts_in_before",
                node_in_time=event_start_time(event) - timedelta(hours=6),
                node_out_time=event_start_time(event),
            )
            self.ts_out_before = Node(
                event=event,
                label="ts_out_before",
                node_in_time=event_start_time(event) - timedelta(hours=36),
                node_out_time=event_start_time(event) - timedelta(hours=30),
            )

    def build_internal_arcs(self) -> list[Arc]:
        arcs = [
            self.build_service_arc(),
            Arc(from_node=self.pilot_out, to_node=self.se, cost=0),
        ]
        if hasattr(self, "ts_in_before"):
            arcs.append(Arc(from_node=self.ts_in_before, to_node=self.pilot_in))
        return arcs

    def inbound_nodes(self) -> list[Node]:
        return [self.ts_in_before] if hasattr(self, "ts_in_before") else []

    def outbound_nodes(self) -> list[Node]:
        nodes = []
        if hasattr(self, "ts_out_before"):
            nodes.append(self.ts_out_before)
        nodes.append(self.se)
        return nodes


class D(Node):
    def __init__(self, vessel_code: str, event: Delivery):
        super().__init__(event=event, label="delivery")
        self.vessel_code = vessel_code


class R(Node):
    def __init__(self, vessel_code: str, event: Redelivery):
        super().__init__(event=event, label="redelivery")
        self.vessel_code = vessel_code


class I(Node):
    def __init__(self, event: Idle):
        super().__init__(event=event, label="idle")


class T(Node):
    def __init__(self, event: Idle):
        super().__init__(event=event, label="target")
