from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Mapping

from .common import repr_fields, serialize_temporal


class VesselScheduleEvent:
    """Base class for vessel schedule events."""

    status: str = ""
    start_time_attr: str | None = None
    end_time_attr: str | None = None
    start_port_attr: str | None = None
    end_port_attr: str | None = None

    @classmethod
    def coerce(cls, value: Any) -> "VesselScheduleEvent":
        if isinstance(value, VesselScheduleEvent):
            return value
        if not isinstance(value, Mapping):
            raise TypeError(
                "VesselScheduleEvent.coerce: cannot convert "
                f"{type(value)!r} to VesselScheduleEvent."
            )

        status = value.get("status")
        if not isinstance(status, str):
            raise TypeError(
                "VesselScheduleEvent.coerce: mapping must include string status."
            )

        event_class = EVENT_CLASS_BY_STATUS.get(status)
        if event_class is None:
            raise ValueError(
                f"VesselScheduleEvent.coerce: unsupported event status {status!r}."
            )
        event = event_class.from_dict(value)
        event_costs = value.get("event_costs")
        if isinstance(event_costs, Mapping):
            event.event_costs = {
                str(key): float(cost)
                for key, cost in event_costs.items()
                if isinstance(cost, (int, float)) and not isinstance(cost, bool)
            }
        return event

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status}

    def __repr__(self) -> str:
        return repr_fields(self.__class__.__name__, status=self.status)

    @staticmethod
    def _require_str(field_name: str, value: Any) -> str:
        if not isinstance(value, str):
            raise TypeError(
                "VesselScheduleEvent: "
                f"{field_name} must be str, got {type(value)!r}."
            )
        return value

    @staticmethod
    def _require_int(field_name: str, value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(
                "VesselScheduleEvent: "
                f"{field_name} must be int, got {type(value)!r}."
            )
        return value

    @staticmethod
    def _require_datetime(field_name: str, value: Any) -> datetime:
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            return datetime.fromisoformat(value)
        raise TypeError(
            "VesselScheduleEvent: "
            f"{field_name} must be datetime, got {type(value)!r}."
        )

    @staticmethod
    def _require_timedelta(field_name: str, value: Any) -> timedelta:
        if isinstance(value, timedelta):
            return value
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return timedelta(seconds=float(value))
        raise TypeError(
            "VesselScheduleEvent: "
            f"{field_name} must be timedelta, got {type(value)!r}."
        )

    @classmethod
    def _require_optional_str(cls, field_name: str, value: Any) -> str | None:
        if value is None:
            return None
        return cls._require_str(field_name, value)

    @classmethod
    def _require_optional_int(cls, field_name: str, value: Any) -> int | None:
        if value is None:
            return None
        return cls._require_int(field_name, value)

    @staticmethod
    def _require_optional_numeric(field_name: str, value: Any) -> float | None:
        if value is None:
            return None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        raise TypeError(
            "VesselScheduleEvent: "
            f"{field_name} must be numeric, got {type(value)!r}."
        )


class InLaneEvent(VesselScheduleEvent):
    lane_sort_priority = 99

    def _sort_key(self) -> tuple[datetime, int]:
        if self.start_time_attr is None:
            raise AttributeError(
                f"{type(self).__name__} must define start_time_attr to support sorting."
            )
        return (getattr(self, self.start_time_attr), self.lane_sort_priority)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, InLaneEvent):
            return NotImplemented
        return self._sort_key() < other._sort_key()


class InLaneSail(InLaneEvent):
    status = "INLANE_SAIL"
    start_time_attr = "sea_sail_start"
    end_time_attr = "sea_sail_end"
    start_port_attr = "from_port_code"
    end_port_attr = "to_port_code"

    def __init__(
        self,
        lane_code: str,
        proforma_name: str,
        position_no: int,
        from_port_code: str,
        from_port_seq: int,
        sea_sail_start: datetime,
        to_port_code: str,
        to_port_seq: int,
        sea_sail_end: datetime,
        distance: float | None = None,
        avg_speed: float | None = None,
    ) -> None:
        self.lane_code = self._require_str("lane_code", lane_code)
        self.proforma_name = self._require_str("proforma_name", proforma_name)
        self.position_no = self._require_int("position_no", position_no)
        self.from_port_code = self._require_str("from_port_code", from_port_code)
        self.from_port_seq = self._require_int("from_port_seq", from_port_seq)
        self.sea_sail_start = self._require_datetime("sea_sail_start", sea_sail_start)
        self.to_port_code = self._require_str("to_port_code", to_port_code)
        self.to_port_seq = self._require_int("to_port_seq", to_port_seq)
        self.sea_sail_end = self._require_datetime("sea_sail_end", sea_sail_end)
        self.distance = self._require_optional_numeric("distance", distance)
        self.avg_speed = self._require_optional_numeric("avg_speed", avg_speed)

    @classmethod
    def from_dict(cls, values: Mapping[str, Any]) -> "InLaneSail":
        return cls(
            lane_code=values["lane_code"],
            proforma_name=values["proforma_name"],
            position_no=values["position_no"],
            from_port_code=values["from_port_code"],
            from_port_seq=values["from_port_seq"],
            sea_sail_start=cls._require_datetime(
                "sea_sail_start", values["sea_sail_start"]
            ),
            to_port_code=values["to_port_code"],
            to_port_seq=values["to_port_seq"],
            sea_sail_end=cls._require_datetime("sea_sail_end", values["sea_sail_end"]),
            distance=values.get("distance"),
            avg_speed=values.get("avg_speed"),
        )

    def to_dict(self) -> dict[str, Any]:
        values = super().to_dict()
        values.update(
            {
                "lane_code": self.lane_code,
                "proforma_name": self.proforma_name,
                "position_no": self.position_no,
                "from_port_code": self.from_port_code,
                "from_port_seq": self.from_port_seq,
                "sea_sail_start": serialize_temporal(self.sea_sail_start),
                "to_port_code": self.to_port_code,
                "to_port_seq": self.to_port_seq,
                "sea_sail_end": serialize_temporal(self.sea_sail_end),
            }
        )
        if self.distance is not None:
            values["distance"] = self.distance
        if self.avg_speed is not None:
            values["avg_speed"] = self.avg_speed
        return values

    def __repr__(self) -> str:
        return repr_fields(
            "InLaneSail",
            assignment=(f"{self.lane_code}/{self.proforma_name}/{self.position_no}"),
            from_port=f"{self.from_port_code}#{self.from_port_seq}",
            to_port=f"{self.to_port_code}#{self.to_port_seq}",
            sea_sail_start=self.sea_sail_start,
            sea_sail_end=self.sea_sail_end,
            distance=self.distance,
            avg_speed=self.avg_speed,
        )


class OutLaneSail(VesselScheduleEvent):
    status = "OUTLANE_SAIL"
    start_time_attr = "sea_sail_start"
    end_time_attr = "sea_sail_end"
    start_port_attr = "from_port_code"
    end_port_attr = "to_port_code"

    def __init__(
        self,
        from_port_code: str,
        sea_sail_start: datetime,
        to_port_code: str,
        sea_sail_end: datetime,
        distance: float | None = None,
        avg_speed: float | None = None,
    ) -> None:
        self.from_port_code = self._require_str("from_port_code", from_port_code)
        self.sea_sail_start = self._require_datetime("sea_sail_start", sea_sail_start)
        self.to_port_code = self._require_str("to_port_code", to_port_code)
        self.sea_sail_end = self._require_datetime("sea_sail_end", sea_sail_end)
        self.distance = self._require_optional_numeric("distance", distance)
        self.avg_speed = self._require_optional_numeric("avg_speed", avg_speed)

    @classmethod
    def from_dict(cls, values: Mapping[str, Any]) -> "OutLaneSail":
        return cls(
            from_port_code=values["from_port_code"],
            sea_sail_start=values["sea_sail_start"],
            to_port_code=values["to_port_code"],
            sea_sail_end=values["sea_sail_end"],
            distance=values.get("distance"),
            avg_speed=values.get("avg_speed"),
        )

    def to_dict(self) -> dict[str, Any]:
        values = super().to_dict()
        values.update(
            {
                "from_port_code": self.from_port_code,
                "sea_sail_start": serialize_temporal(self.sea_sail_start),
                "to_port_code": self.to_port_code,
                "sea_sail_end": serialize_temporal(self.sea_sail_end),
            }
        )
        if self.distance is not None:
            values["distance"] = self.distance
        if self.avg_speed is not None:
            values["avg_speed"] = self.avg_speed
        return values

    def __repr__(self) -> str:
        return repr_fields(
            "OutLaneSail",
            from_port=f"{self.from_port_code}",
            to_port=f"{self.to_port_code}",
            sea_sail_start=self.sea_sail_start,
            sea_sail_end=self.sea_sail_end,
            distance=self.distance,
            avg_speed=self.avg_speed,
        )


class CanalPassage(VesselScheduleEvent):
    status = "CANAL_PASSAGE"
    start_time_attr = "passage_start"
    end_time_attr = "passage_end"
    start_port_attr = "canal_port_code"
    end_port_attr = "canal_port_code"

    def __init__(
        self,
        canal_port_code: str,
        direction: str,
        passage_start: datetime,
        passage_end: datetime,
        from_port_code: str,
        to_port_code: str,
    ) -> None:
        self.canal_port_code = self._require_str("canal_port_code", canal_port_code)
        self.direction = self._require_str("direction", direction)
        self.passage_start = self._require_datetime("passage_start", passage_start)
        self.passage_end = self._require_datetime("passage_end", passage_end)
        self.from_port_code = self._require_str("from_port_code", from_port_code)
        self.to_port_code = self._require_str("to_port_code", to_port_code)

    @classmethod
    def from_dict(cls, values: Mapping[str, Any]) -> "CanalPassage":
        return cls(
            canal_port_code=values["canal_port_code"],
            direction=values["direction"],
            passage_start=cls._require_datetime(
                "passage_start", values["passage_start"]
            ),
            passage_end=cls._require_datetime("passage_end", values["passage_end"]),
            from_port_code=values["from_port_code"],
            to_port_code=values["to_port_code"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "canal_port_code": self.canal_port_code,
            "direction": self.direction,
            "passage_start": serialize_temporal(self.passage_start),
            "passage_end": serialize_temporal(self.passage_end),
            "from_port_code": self.from_port_code,
            "to_port_code": self.to_port_code,
        }

    def __repr__(self) -> str:
        return repr_fields(
            "CanalPassage",
            canal_port_code=self.canal_port_code,
            direction=self.direction,
            passage_start=self.passage_start,
            passage_end=self.passage_end,
            route=f"{self.from_port_code}->{self.to_port_code}",
        )


class PhaseIn(InLaneEvent):
    status = "PHASE_IN"
    start_time_attr = "phase_in_time"
    end_time_attr = "phase_in_time"
    start_port_attr = "phase_in_port_code"
    end_port_attr = "phase_in_port_code"
    lane_sort_priority = 2

    def __init__(
        self,
        lane_code: str,
        proforma_name: str,
        position_no: int,
        phase_in_port_code: str,
        phase_in_port_seq: int,
        phase_in_time: datetime,
    ) -> None:
        self.lane_code = self._require_str("lane_code", lane_code)
        self.proforma_name = self._require_str("proforma_name", proforma_name)
        self.position_no = self._require_int("position_no", position_no)
        self.phase_in_port_code = self._require_str(
            "phase_in_port_code", phase_in_port_code
        )
        self.phase_in_port_seq = self._require_int(
            "phase_in_port_seq", phase_in_port_seq
        )
        self.phase_in_time = self._require_datetime("phase_in_time", phase_in_time)

    @classmethod
    def from_dict(cls, values: Mapping[str, Any]) -> "PhaseIn":
        return cls(
            lane_code=values["lane_code"],
            proforma_name=values["proforma_name"],
            position_no=values["position_no"],
            phase_in_port_code=cls._require_str(
                "phase_in_port_code", values["phase_in_port_code"]
            ),
            phase_in_port_seq=cls._require_int(
                "phase_in_port_seq", values["phase_in_port_seq"]
            ),
            phase_in_time=cls._require_datetime(
                "phase_in_time", values["phase_in_time"]
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "lane_code": self.lane_code,
            "proforma_name": self.proforma_name,
            "position_no": self.position_no,
            "phase_in_port_code": self.phase_in_port_code,
            "phase_in_port_seq": self.phase_in_port_seq,
            "phase_in_time": serialize_temporal(self.phase_in_time),
        }

    def __repr__(self) -> str:
        return repr_fields(
            "PhaseIn",
            assignment=(f"{self.lane_code}/{self.proforma_name}/{self.position_no}"),
            phase_in_port=f"{self.phase_in_port_code}#{self.phase_in_port_seq}",
            phase_in_time=self.phase_in_time,
        )


class PhaseOut(InLaneEvent):
    status = "PHASE_OUT"
    start_time_attr = "phase_out_time"
    end_time_attr = "phase_out_time"
    start_port_attr = "phase_out_port_code"
    end_port_attr = "phase_out_port_code"
    lane_sort_priority = 1

    def __init__(
        self,
        lane_code: str,
        proforma_name: str,
        position_no: int,
        phase_out_port_code: str,
        phase_out_port_seq: int,
        phase_out_time: datetime,
    ) -> None:
        self.lane_code = self._require_str("lane_code", lane_code)
        self.proforma_name = self._require_str("proforma_name", proforma_name)
        self.position_no = self._require_int("position_no", position_no)
        self.phase_out_port_code = self._require_str(
            "phase_out_port_code", phase_out_port_code
        )
        self.phase_out_port_seq = self._require_int(
            "phase_out_port_seq", phase_out_port_seq
        )
        self.phase_out_time = self._require_datetime("phase_out_time", phase_out_time)

    @classmethod
    def from_dict(cls, values: Mapping[str, Any]) -> "PhaseOut":
        return cls(
            lane_code=values["lane_code"],
            proforma_name=values["proforma_name"],
            position_no=values["position_no"],
            phase_out_port_code=cls._require_str(
                "phase_out_port_code", values["phase_out_port_code"]
            ),
            phase_out_port_seq=cls._require_int(
                "phase_out_port_seq", values["phase_out_port_seq"]
            ),
            phase_out_time=cls._require_datetime(
                "phase_out_time", values["phase_out_time"]
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "lane_code": self.lane_code,
            "proforma_name": self.proforma_name,
            "position_no": self.position_no,
            "phase_out_port_code": self.phase_out_port_code,
            "phase_out_port_seq": self.phase_out_port_seq,
            "phase_out_time": serialize_temporal(self.phase_out_time),
        }

    def __repr__(self) -> str:
        return repr_fields(
            "PhaseOut",
            assignment=(f"{self.lane_code}/{self.proforma_name}/{self.position_no}"),
            phase_out_port=f"{self.phase_out_port_code}#{self.phase_out_port_seq}",
            phase_out_time=self.phase_out_time,
        )


class PortStay(InLaneEvent):
    status = "PORT_STAY"
    start_time_attr = "pilot_in_start"
    end_time_attr = "pilot_out_end"
    start_port_attr = "port_code"
    end_port_attr = "port_code"

    def __init__(
        self,
        lane_code: str,
        proforma_name: str,
        position_no: int,
        port_code: str,
        port_seq: int,
        pilot_in_start: datetime,
        berthing_start: datetime,
        berthing_end: datetime,
        pilot_out_end: datetime,
    ) -> None:
        self.lane_code = self._require_str("lane_code", lane_code)
        self.proforma_name = self._require_str("proforma_name", proforma_name)
        self.position_no = self._require_int("position_no", position_no)
        self.port_code = self._require_str("port_code", port_code)
        self.port_seq = self._require_int("port_seq", port_seq)
        self.pilot_in_start = self._require_datetime("pilot_in_start", pilot_in_start)
        self.berthing_start = self._require_datetime("berthing_start", berthing_start)
        self.berthing_end = self._require_datetime("berthing_end", berthing_end)
        self.pilot_out_end = self._require_datetime("pilot_out_end", pilot_out_end)

    @classmethod
    def from_dict(cls, values: Mapping[str, Any]) -> "PortStay":
        return cls(
            lane_code=values["lane_code"],
            proforma_name=values["proforma_name"],
            position_no=values["position_no"],
            port_code=values["port_code"],
            port_seq=values["port_seq"],
            pilot_in_start=cls._require_datetime(
                "pilot_in_start", values["pilot_in_start"]
            ),
            berthing_start=cls._require_datetime(
                "berthing_start", values["berthing_start"]
            ),
            berthing_end=cls._require_datetime("berthing_end", values["berthing_end"]),
            pilot_out_end=cls._require_datetime(
                "pilot_out_end", values["pilot_out_end"]
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "lane_code": self.lane_code,
            "proforma_name": self.proforma_name,
            "position_no": self.position_no,
            "port_code": self.port_code,
            "port_seq": self.port_seq,
            "pilot_in_start": serialize_temporal(self.pilot_in_start),
            "berthing_start": serialize_temporal(self.berthing_start),
            "berthing_end": serialize_temporal(self.berthing_end),
            "pilot_out_end": serialize_temporal(self.pilot_out_end),
        }

    def __repr__(self) -> str:
        return repr_fields(
            "PortStay",
            assignment=(f"{self.lane_code}/{self.proforma_name}/{self.position_no}"),
            port=f"{self.port_code}#{self.port_seq}",
            pilot_in_start=self.pilot_in_start,
            berthing_start=self.berthing_start,
            berthing_end=self.berthing_end,
            pilot_out_end=self.pilot_out_end,
        )


class TransshipmentUnload(InLaneEvent):
    status = "TRANSSHIPMENT_UNLOAD"
    start_time_attr = "unload_start"
    end_time_attr = "unload_end"
    start_port_attr = "ts_port_code"
    end_port_attr = "ts_port_code"
    lane_sort_priority = 0

    def __init__(
        self,
        lane_code: str,
        proforma_name: str,
        position_no: int,
        ts_port_code: str,
        ts_port_seq: int,
        unload_start: datetime,
        unload_end: datetime,
    ) -> None:
        self.lane_code = self._require_str("lane_code", lane_code)
        self.proforma_name = self._require_str("proforma_name", proforma_name)
        self.position_no = self._require_int("position_no", position_no)
        self.ts_port_code = self._require_str("ts_port_code", ts_port_code)
        self.ts_port_seq = self._require_int("ts_port_seq", ts_port_seq)
        self.unload_start = self._require_datetime("unload_start", unload_start)
        self.unload_end = self._require_datetime("unload_end", unload_end)

    @classmethod
    def from_dict(cls, values: Mapping[str, Any]) -> "TransshipmentUnload":
        return cls(
            lane_code=values["lane_code"],
            proforma_name=values["proforma_name"],
            position_no=values["position_no"],
            ts_port_code=values["ts_port_code"],
            ts_port_seq=values["ts_port_seq"],
            unload_start=cls._require_datetime("unload_start", values["unload_start"]),
            unload_end=cls._require_datetime("unload_end", values["unload_end"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "lane_code": self.lane_code,
            "proforma_name": self.proforma_name,
            "position_no": self.position_no,
            "ts_port_code": self.ts_port_code,
            "ts_port_seq": self.ts_port_seq,
            "unload_start": serialize_temporal(self.unload_start),
            "unload_end": serialize_temporal(self.unload_end),
        }

    def __repr__(self) -> str:
        return repr_fields(
            "TransshipmentUnload",
            assignment=(f"{self.lane_code}/{self.proforma_name}/{self.position_no}"),
            ts_port=f"{self.ts_port_code}#{self.ts_port_seq}",
            unload_start=self.unload_start,
            unload_end=self.unload_end,
        )


class TransshipmentLoad(InLaneEvent):
    status = "TRANSSHIPMENT_LOAD"
    start_time_attr = "load_start"
    end_time_attr = "load_end"
    start_port_attr = "ts_port_code"
    end_port_attr = "ts_port_code"
    lane_sort_priority = 3

    def __init__(
        self,
        lane_code: str,
        proforma_name: str,
        position_no: int,
        ts_port_code: str,
        ts_port_seq: int,
        load_start: datetime,
        load_end: datetime,
    ) -> None:
        self.lane_code = self._require_str("lane_code", lane_code)
        self.proforma_name = self._require_str("proforma_name", proforma_name)
        self.position_no = self._require_int("position_no", position_no)
        self.ts_port_code = self._require_str("ts_port_code", ts_port_code)
        self.ts_port_seq = self._require_int("ts_port_seq", ts_port_seq)
        self.load_start = self._require_datetime("load_start", load_start)
        self.load_end = self._require_datetime("load_end", load_end)

    @classmethod
    def from_dict(cls, values: Mapping[str, Any]) -> "TransshipmentLoad":
        return cls(
            lane_code=values["lane_code"],
            proforma_name=values["proforma_name"],
            position_no=values["position_no"],
            ts_port_code=values["ts_port_code"],
            ts_port_seq=values["ts_port_seq"],
            load_start=cls._require_datetime("load_start", values["load_start"]),
            load_end=cls._require_datetime("load_end", values["load_end"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "lane_code": self.lane_code,
            "proforma_name": self.proforma_name,
            "position_no": self.position_no,
            "ts_port_code": self.ts_port_code,
            "ts_port_seq": self.ts_port_seq,
            "load_start": serialize_temporal(self.load_start),
            "load_end": serialize_temporal(self.load_end),
        }

    def __repr__(self) -> str:
        return repr_fields(
            "TransshipmentLoad",
            assignment=(f"{self.lane_code}/{self.proforma_name}/{self.position_no}"),
            ts_port=f"{self.ts_port_code}#{self.ts_port_seq}",
            load_start=self.load_start,
            load_end=self.load_end,
        )


class DryDock(VesselScheduleEvent):
    status = "DRY_DOCK"
    start_time_attr = "dock_in"
    end_time_attr = "dock_out"
    start_port_attr = "dock_port_code"
    end_port_attr = "dock_port_code"

    def __init__(
        self,
        dock_port_code: str,
        dock_in: datetime,
        dock_out: datetime,
    ) -> None:
        self.dock_port_code = self._require_str("dock_port_code", dock_port_code)
        self.dock_in = self._require_datetime("dock_in", dock_in)
        self.dock_out = self._require_datetime("dock_out", dock_out)

    @classmethod
    def from_dict(cls, values: Mapping[str, Any]) -> "DryDock":
        dock_in = values.get("dock_in")
        dock_out = values.get("dock_out")
        return cls(
            dock_port_code=values["dock_port_code"],
            dock_in=cls._require_datetime("dock_in", dock_in),
            dock_out=cls._require_datetime("dock_out", dock_out),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "dock_port_code": self.dock_port_code,
            "dock_in": serialize_temporal(self.dock_in),
            "dock_out": serialize_temporal(self.dock_out),
        }

    def __repr__(self) -> str:
        return repr_fields(
            "DryDock",
            dock_port_code=self.dock_port_code,
            dock_in=self.dock_in,
            dock_out=self.dock_out,
        )


class Idle(VesselScheduleEvent):
    status = "IDLE"
    start_time_attr = "idle_start"
    end_time_attr = "idle_end"
    start_port_attr = "port_code"
    end_port_attr = "port_code"

    def __init__(
        self, port_code: str, idle_start: datetime, idle_end: datetime
    ) -> None:
        self.port_code = self._require_str("port_code", port_code)
        self.idle_start = self._require_datetime("idle_start", idle_start)
        self.idle_end = self._require_datetime("idle_end", idle_end)

    @classmethod
    def from_dict(cls, values: Mapping[str, Any]) -> "Idle":
        return cls(
            port_code=values["port_code"],
            idle_start=cls._require_datetime("idle_start", values["idle_start"]),
            idle_end=cls._require_datetime("idle_end", values["idle_end"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "port_code": self.port_code,
            "idle_start": serialize_temporal(self.idle_start),
            "idle_end": serialize_temporal(self.idle_end),
        }

    def __repr__(self) -> str:
        return repr_fields(
            "Idle",
            port_code=self.port_code,
            idle_start=self.idle_start,
            idle_end=self.idle_end,
        )


class Delivery(VesselScheduleEvent):
    status = "DELIVERY"
    start_time_attr = "delivery_time"
    end_time_attr = "delivery_time"
    start_port_attr = "delivery_port_code"
    end_port_attr = "delivery_port_code"

    def __init__(
        self,
        delivery_time: datetime,
        delivery_port_code: str,
    ) -> None:
        self.delivery_port_code = self._require_str(
            "delivery_port_code", delivery_port_code
        )
        self.delivery_time = self._require_datetime("delivery_time", delivery_time)

    @classmethod
    def from_dict(cls, values: Mapping[str, Any]) -> "Delivery":
        return cls(
            delivery_port_code=values["delivery_port_code"],
            delivery_time=cls._require_datetime(
                "delivery_time", values["delivery_time"]
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "delivery_port_code": self.delivery_port_code,
            "delivery_time": serialize_temporal(self.delivery_time),
        }

    def __repr__(self) -> str:
        return repr_fields(
            "Delivery",
            delivery_port_code=self.delivery_port_code,
            delivery_time=self.delivery_time,
        )


class Redelivery(VesselScheduleEvent):
    status = "REDELIVERY"
    start_time_attr = "redelivery_time"
    end_time_attr = "redelivery_time"
    start_port_attr = "redelivery_port_code"
    end_port_attr = "redelivery_port_code"

    def __init__(
        self,
        redelivery_port_code: str,
        redelivery_time: datetime,
    ) -> None:
        self.redelivery_port_code = self._require_str(
            "redelivery_port_code", redelivery_port_code
        )
        self.redelivery_time = self._require_datetime(
            "redelivery_time", redelivery_time
        )

    @classmethod
    def from_dict(cls, values: Mapping[str, Any]) -> "Redelivery":
        return cls(
            redelivery_port_code=values["redelivery_port_code"],
            redelivery_time=cls._require_datetime(
                "redelivery_time", values["redelivery_time"]
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "redelivery_port_code": self.redelivery_port_code,
            "redelivery_time": serialize_temporal(self.redelivery_time),
        }

    def __repr__(self) -> str:
        return repr_fields(
            "Redelivery",
            redelivery_port_code=self.redelivery_port_code,
            redelivery_time=self.redelivery_time,
        )


EVENT_CLASS_BY_STATUS = {
    InLaneSail.status: InLaneSail,
    OutLaneSail.status: OutLaneSail,
    CanalPassage.status: CanalPassage,
    PhaseIn.status: PhaseIn,
    PhaseOut.status: PhaseOut,
    PortStay.status: PortStay,
    TransshipmentUnload.status: TransshipmentUnload,
    TransshipmentLoad.status: TransshipmentLoad,
    DryDock.status: DryDock,
    Idle.status: Idle,
    Delivery.status: Delivery,
    Redelivery.status: Redelivery,
}
