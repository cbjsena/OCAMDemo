from __future__ import annotations

from typing import Any, Mapping, NamedTuple

from .common import repr_fields
from .schedule_events import InLaneEvent, VesselScheduleEvent

# TODO: validate() for each class


class LaneViewEvent(NamedTuple):
    vessel_code: str
    event: InLaneEvent


class DeclaredPosition:
    """Typed solution record for declared positions."""

    __slots__ = ("lane_code", "proforma_name", "declared_position_no")

    def __init__(
        self, lane_code: str, proforma_name: str, declared_position_no: int
    ) -> None:
        self.lane_code = self._require_str("lane_code", lane_code)
        self.proforma_name = self._require_str("proforma_name", proforma_name)
        self.declared_position_no = self._require_int(
            "declared_position_no", declared_position_no
        )

    @classmethod
    def from_dict(cls, values: Mapping[str, Any]) -> "DeclaredPosition":
        return cls(
            lane_code=values["lane_code"],
            proforma_name=values["proforma_name"],
            declared_position_no=values["declared_position_no"],
        )

    @classmethod
    def coerce(cls, value: Any) -> "DeclaredPosition":
        if isinstance(value, cls):
            return value
        if isinstance(value, Mapping):
            return cls.from_dict(value)
        if isinstance(value, tuple) and len(value) == 3:
            return cls(*value)
        raise TypeError(
            "DeclaredPosition.coerce: cannot convert "
            f"{type(value)!r} to DeclaredPosition."
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "lane_code": self.lane_code,
            "proforma_name": self.proforma_name,
            "declared_position_no": self.declared_position_no,
        }

    def __repr__(self) -> str:
        return repr_fields(
            "DeclaredPosition",
            lane_code=self.lane_code,
            proforma_name=self.proforma_name,
            declared_position_no=self.declared_position_no,
        )

    @staticmethod
    def _require_str(field_name: str, value: Any) -> str:
        if not isinstance(value, str):
            raise TypeError(
                "DeclaredPosition: " f"{field_name} must be str, got {type(value)!r}."
            )
        return value

    @staticmethod
    def _require_int(field_name: str, value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(
                "DeclaredPosition: " f"{field_name} must be int, got {type(value)!r}."
            )
        return value


class VesselSchedule:
    """Chronological event list for a single vessel."""

    def __init__(self, events: list[VesselScheduleEvent | Mapping[str, Any]]) -> None:
        self.events = self._coerce_events(events)

    @classmethod
    def from_dict(cls, values: Mapping[str, Any]) -> "VesselSchedule":
        return cls(events=values["events"])

    @classmethod
    def coerce(cls, value: Any) -> "VesselSchedule":
        if isinstance(value, cls):
            return value
        if isinstance(value, list):
            return cls(events=value)
        if isinstance(value, Mapping):
            return cls.from_dict(value)
        raise TypeError(
            "VesselSchedule.coerce: cannot convert "
            f"{type(value)!r} to VesselSchedule."
        )

    def to_dict(self) -> dict[str, Any]:
        events = []
        for event in self.events:
            values = event.to_dict()
            event_costs = getattr(event, "event_costs", None)
            if event_costs:
                values["event_costs"] = dict(event_costs)
            events.append(values)
        return {"events": events}

    def __getitem__(self, index: int) -> VesselScheduleEvent:
        return self.events[index]

    def __iter__(self):
        return iter(self.events)

    def __len__(self) -> int:
        return len(self.events)

    def append(self, event: VesselScheduleEvent | Mapping[str, Any]) -> None:
        self.events.append(VesselScheduleEvent.coerce(event))

    def __repr__(self) -> str:
        return repr_fields(
            "VesselSchedule", events=len(self.events), preview=self.events[:2]
        )

    @staticmethod
    def _coerce_events(
        values: list[VesselScheduleEvent | Mapping[str, Any]] | Any,
    ) -> list[VesselScheduleEvent]:
        if not isinstance(values, list):
            raise TypeError(
                "VesselSchedule: events must be list, " f"got {type(values)!r}."
            )
        return [VesselScheduleEvent.coerce(value) for value in values]


class VesselSchedules:
    """Schedules keyed by vessel code."""

    def __init__(self, schedules: Mapping[str, Any] | None = None) -> None:
        if schedules is None:
            schedules = {}
        if not isinstance(schedules, Mapping):
            raise TypeError(
                "VesselSchedules: schedules must be mapping, got "
                f"{type(schedules)!r}."
            )
        self.schedules = {
            self._require_str("vessel_code", vessel_code): VesselSchedule.coerce(value)
            for vessel_code, value in schedules.items()
        }

    @classmethod
    def from_dict(cls, values: Mapping[str, Any]) -> "VesselSchedules":
        return cls(schedules=values)

    @classmethod
    def coerce(cls, value: Any) -> "VesselSchedules":
        if isinstance(value, cls):
            return value
        if isinstance(value, list):
            if value:
                raise TypeError(
                    "VesselSchedules.coerce: non-empty list is not supported; use a mapping keyed by vessel_code."
                )
            return cls()
        if isinstance(value, Mapping):
            return cls.from_dict(value)
        raise TypeError(
            "VesselSchedules.coerce: cannot convert "
            f"{type(value)!r} to VesselSchedules."
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            vessel_code: schedule.to_dict()
            for vessel_code, schedule in self.schedules.items()
        }

    def __getitem__(self, vessel_code: str) -> VesselSchedule:
        return self.schedules[vessel_code]

    def __setitem__(
        self, vessel_code: str, schedule: VesselSchedule | Mapping[str, Any] | list[Any]
    ) -> None:
        self.schedules[self._require_str("vessel_code", vessel_code)] = (
            VesselSchedule.coerce(schedule)
        )

    def __contains__(self, vessel_code: object) -> bool:
        return vessel_code in self.schedules

    def __iter__(self):
        return iter(self.schedules)

    def __len__(self) -> int:
        return len(self.schedules)

    def __bool__(self) -> bool:
        return bool(self.schedules)

    def get(self, vessel_code: str, default: Any = None) -> VesselSchedule | Any:
        return self.schedules.get(vessel_code, default)

    def keys(self):
        return self.schedules.keys()

    def values(self):
        return self.schedules.values()

    def items(self):
        return self.schedules.items()

    def __repr__(self) -> str:
        return repr_fields(
            "VesselSchedules",
            vessels=len(self.schedules),
            vessel_codes=list(sorted(self.schedules)),
        )

    @staticmethod
    def _require_str(field_name: str, value: Any) -> str:
        if not isinstance(value, str):
            raise TypeError(
                "VesselSchedules: " f"{field_name} must be str, got {type(value)!r}."
            )
        return value


class CascadingSolution:
    """Solution object returned by every algorithm."""

    def __init__(
        self,
        declared_positions: (
            list[DeclaredPosition | Mapping[str, Any] | tuple] | object
        ) = None,
        vessel_schedules: VesselSchedules | Mapping[str, Any] | object = None,
        virtual_vessel_schedules: VesselSchedules | Mapping[str, Any] | object = None,
        num_virtual_vessels_used: int = 0,
    ) -> None:
        if declared_positions is None:
            raise TypeError("CascadingSolution: declared_positions cannot be None.")
        if vessel_schedules is None:
            raise TypeError("CascadingSolution: vessel_schedules cannot be None.")
        if virtual_vessel_schedules is None:
            virtual_vessel_schedules = {}
        if num_virtual_vessels_used < 0:
            raise ValueError(
                "CascadingSolution: num_virtual_vessels_used cannot be negative."
            )

        self.declared_positions = [
            DeclaredPosition.coerce(value) for value in declared_positions
        ]
        self.vessel_schedules = VesselSchedules.coerce(vessel_schedules)
        self.virtual_vessel_schedules = VesselSchedules.coerce(virtual_vessel_schedules)
        overlapping_codes = set(self.vessel_schedules).intersection(
            self.virtual_vessel_schedules
        )
        if overlapping_codes:
            raise ValueError(
                "CascadingSolution: vessel_schedules and virtual_vessel_schedules must use disjoint vessel codes, "
                f"but overlapped on {sorted(overlapping_codes)!r}."
            )
        self.num_virtual_vessels_used = self._require_int(
            "num_virtual_vessels_used", num_virtual_vessels_used
        )

    @property
    def all_vessel_schedules(self) -> VesselSchedules:
        return VesselSchedules(
            {
                **self.vessel_schedules.to_dict(),
                **self.virtual_vessel_schedules.to_dict(),
            }
        )

    def to_lane_view(self) -> dict[tuple[str, str, int], list[LaneViewEvent]]:
        lane_events: dict[tuple[str, str, int], list[LaneViewEvent]] = {}
        for vessel_code, schedule in self.all_vessel_schedules.items():
            for event in schedule:
                if not isinstance(event, InLaneEvent):
                    continue
                key = (event.lane_code, event.proforma_name, event.position_no)
                lane_events.setdefault(key, []).append(
                    LaneViewEvent(vessel_code=vessel_code, event=event)
                )

        for events in lane_events.values():
            events.sort(key=lambda lane_event: lane_event.event)
        return lane_events

    def to_dict(self) -> dict[str, Any]:
        return {
            "declared_positions": [
                declared_position.to_dict()
                for declared_position in self.declared_positions
            ],
            "vessel_schedules": self.vessel_schedules.to_dict(),
            "virtual_vessel_schedules": self.virtual_vessel_schedules.to_dict(),
            "num_virtual_vessels_used": self.num_virtual_vessels_used,
        }

    def __repr__(self) -> str:
        return repr_fields(
            "CascadingSolution",
            declared_positions=len(self.declared_positions),
            vessel_schedules=len(self.vessel_schedules),
            virtual_vessel_schedules=len(self.virtual_vessel_schedules),
            num_virtual_vessels_used=self.num_virtual_vessels_used,
        )

    @staticmethod
    def _require_int(field_name: str, value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(
                "CascadingSolution: " f"{field_name} must be int, got {type(value)!r}."
            )
        return value
