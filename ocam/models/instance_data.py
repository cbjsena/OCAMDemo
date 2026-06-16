from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .common import repr_fields


@dataclass
class InputBundle:
    """Raw input files collected from an input directory."""

    input_dir: Path
    files: dict[str, Path]
    payload: dict[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:
        return repr_fields(
            "InputBundle",
            input_dir=self.input_dir,
            files=list(sorted(self.files)),
            payload_keys=list(sorted(self.payload)),
        )


@dataclass
class InstanceData:
    """Parsed problem instance produced by the preprocessing layer."""

    raw: InputBundle
    scenario_name: str = ""
    planning_horizon: dict[str, datetime] = field(default_factory=dict)
    service_lanes: list[dict[str, Any]] = field(default_factory=list)
    eca_ports: set[str] = field(default_factory=set)
    vessels: list[dict[str, Any]] = field(default_factory=list)
    distances: list[dict[str, Any]] = field(default_factory=list)
    canal_fee: list[dict[str, Any]] = field(default_factory=list)
    canal_direction: list[dict[str, Any]] = field(default_factory=list)
    canal_passage_time: list[dict[str, Any]] = field(default_factory=list)
    bunker_consumption_port: list[dict[str, Any]] = field(default_factory=list)
    bunker_consumption_sea: list[dict[str, Any]] = field(default_factory=list)
    bunker_price: list[dict[str, Any]] = field(default_factory=list)
    transshipment_cost: list[dict[str, Any]] = field(default_factory=list)
    opportunity_cost: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_name": self.scenario_name,
            "planning_horizon": self.planning_horizon,
            "service_lanes": self.service_lanes,
            "eca_ports": self.eca_ports,
            "vessels": self.vessels,
            "distances": self.distances,
            "canal_fee": self.canal_fee,
            "canal_direction": self.canal_direction,
            "canal_passage_time": self.canal_passage_time,
            "bunker_consumption_port": self.bunker_consumption_port,
            "bunker_consumption_sea": self.bunker_consumption_sea,
            "bunker_price": self.bunker_price,
            "transshipment_cost": self.transshipment_cost,
            "opportunity_cost": self.opportunity_cost,
        }

    def __repr__(self) -> str:
        return repr_fields(
            "InstanceData",
            scenario_name=self.scenario_name,
            planning_horizon=self.planning_horizon,
            service_lanes=len(self.service_lanes),
            eca_ports=len(self.eca_ports),
            vessels=len(self.vessels),
            distances=len(self.distances),
            canal_fee=len(self.canal_fee),
            canal_direction=len(self.canal_direction),
            canal_passage_time=len(self.canal_passage_time),
            bunker_consumption_port=len(self.bunker_consumption_port),
            bunker_consumption_sea=len(self.bunker_consumption_sea),
            bunker_price=len(self.bunker_price),
            transshipment_cost=len(self.transshipment_cost),
            opportunity_cost=len(self.opportunity_cost),
        )
