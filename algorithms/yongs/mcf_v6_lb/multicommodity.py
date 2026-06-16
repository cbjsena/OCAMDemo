from __future__ import annotations

from bisect import bisect_left
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import math
import os
from pathlib import Path
import pickle
from pyexpat import model
from time import perf_counter
from typing import Any

import networkx as nx

import ocam.utils as ocam_utils

try:
    from tqdm import tqdm
except ImportError:

    def tqdm(iterable, **kwargs):
        return iterable


from ocam.models import *
from ocam.utils import *

from .cpp_backend import construct_network_cpp
from .network.__viz import viz_mip_result, viz_model_network
from .network.network import IDLE_EVENT_KEY
from .network.nodes import *
from .network.arcs import *

INSTANCE_DATA: InstanceData | None = None
LAST_DESTROY_BATCH_FLOWS: list[dict[str, Any]] = []
LAST_MULTICOMMODITY_RESULT: dict[str, Any] | None = None
BUNKER_SEA_BY_CAPACITY: dict[int, dict[float, float]] = {}
BUNKER_PORT_BY_CAPACITY: dict[int, dict[str, float]] = {}
BUNKER_PRICE_BY_KEY: dict[tuple[str, str, str], float] = {}
BUNKER_PRICES_BY_YEAR_MONTH_TYPE: dict[tuple[str, str], list[float]] = {}
CANAL_FEE_BY_KEY: dict[tuple[str, str, str], float] = {}
CANAL_DIRECTION_BY_KEY: dict[tuple[str, str, str], str] = {}
OPPORTUNITY_COST_BY_KEY: dict[tuple[str, str, str], float] = {}
DIRECTION_BY_LANE_VERSION_SEQ: dict[tuple[str, str, int], str] = {}
VIRTUAL_VESSEL_CODE = "v_0"
DEFAULT_OUTPUT_PATH = "multicommodity_mip_paths.html"
AGGREGATE_POSITION_ACTIVATION_CONSTRAINTS = True
INCLUDE_VIRTUAL_VESSEL = False
SAIL_COST_LB_YEAR_MONTH = "__LB_MIN_BUNKER_MONTH__"
UNATTRIBUTED_BUNKER_PRICE_PER_TON = 0.0
TS_NODE_LABELS = {"ts_in_before", "ts_in_after", "ts_out_before", "ts_out_after"}


class NoFeasibleCanalSpeedPair(ValueError):
    pass


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


def initialize_multicommodity(instance_data: InstanceData) -> None:
    global INSTANCE_DATA, SERVICE_DATETIME_CACHE, _NEXT_NODE_ID, _NEXT_NODE_GROUP_ID, _NEXT_ARC_ID
    global LAST_DESTROY_BATCH_FLOWS, LAST_MULTICOMMODITY_RESULT
    global BUNKER_SEA_BY_CAPACITY, BUNKER_PORT_BY_CAPACITY, BUNKER_PRICE_BY_KEY, BUNKER_PRICES_BY_YEAR_MONTH_TYPE
    global CANAL_FEE_BY_KEY, CANAL_DIRECTION_BY_KEY, OPPORTUNITY_COST_BY_KEY, DIRECTION_BY_LANE_VERSION_SEQ

    INSTANCE_DATA = instance_data
    SERVICE_DATETIME_CACHE = {}
    _NEXT_NODE_ID = 0
    _NEXT_NODE_GROUP_ID = 0
    _NEXT_ARC_ID = 0
    LAST_DESTROY_BATCH_FLOWS = []
    LAST_MULTICOMMODITY_RESULT = None
    BUNKER_SEA_BY_CAPACITY = {
        row["capacity_teu"]: {
            consumption["speed"]: consumption["consumption_for_sailing"] for consumption in row["consumption"]
        }
        for row in instance_data.bunker_consumption_sea
    }
    BUNKER_PORT_BY_CAPACITY = {
        row["capacity_teu"]: {key: float(value) for key, value in row["consumption"].items()}
        for row in instance_data.bunker_consumption_port
    }
    BUNKER_PRICE_BY_KEY = {
        (row["year_month"], row["lane_code"], row["bunker_type"]): float(row["price"])
        for row in instance_data.bunker_price
    }
    BUNKER_PRICES_BY_YEAR_MONTH_TYPE = defaultdict(list)
    for row in instance_data.bunker_price:
        BUNKER_PRICES_BY_YEAR_MONTH_TYPE[(row["year_month"], row["bunker_type"])].append(float(row["price"]))
    CANAL_FEE_BY_KEY = {
        (row["vessel_code"], row["direction"], _canonical_canal_port_code(row["port_code"])): float(row["fee"])
        for row in instance_data.canal_fee
    }
    CANAL_DIRECTION_BY_KEY = {
        (
            _canonical_canal_port_code(row["from_port_code"]),
            _canonical_canal_port_code(row["canal_port_code"]),
            _canonical_canal_port_code(row["to_port_code"]),
        ): row["direction"]
        for row in instance_data.canal_direction
    }
    OPPORTUNITY_COST_BY_KEY = {
        (row["lane_code"], row["proforma_name"], row["direction"]): float(row["opportunity_cost"])
        for row in instance_data.opportunity_cost
    }
    DIRECTION_BY_LANE_VERSION_SEQ = {}
    for lane in instance_data.service_lanes:
        lane_code = lane["lane_code"]
        for version in lane["versions"]:
            proforma_name = version["proforma_name"]
            for port in version["port_rotation"]:
                DIRECTION_BY_LANE_VERSION_SEQ[(lane_code, proforma_name, int(port["port_seq"]))] = port["direction"]
    init_utils(instance_data)


def get_last_destroy_batch_flows() -> list[dict[str, Any]]:
    return list(LAST_DESTROY_BATCH_FLOWS)


def get_last_multicommodity_result() -> dict[str, Any] | None:
    return LAST_MULTICOMMODITY_RESULT


def _placeholder_sail_arc_cost(
    vessel_code: str,
    lane_code: str | None,
    proforma_name: str | None,
    year_month: str,
    from_port_code: str,
    to_port_code: str,
    distance: float,
    sail_time: float,
) -> float:
    if INSTANCE_DATA is None:
        raise ValueError("_placeholder_sail_arc_cost: initialize_local_search() must be called first.")
    if sail_time <= 0 or distance <= 0:
        raise ValueError(
            "_placeholder_sail_arc_cost: invalid sail_time or distance. " f"sail_time={sail_time}, distance={distance}."
        )

    capacity_teu = int(lookup_vessel(vessel_code)["capacity_teu"])
    return _placeholder_sail_arc_cost_for_capacity(
        capacity_teu=capacity_teu,
        lane_code=lane_code,
        year_month=year_month,
        from_port_code=from_port_code,
        to_port_code=to_port_code,
        distance=distance,
        sail_time=sail_time,
    )


def _placeholder_sail_arc_cost_for_capacity(
    capacity_teu: int,
    lane_code: str | None,
    year_month: str,
    from_port_code: str,
    to_port_code: str,
    distance: float,
    sail_time: float,
) -> float:
    avg_speed = distance / sail_time
    rounded_speed = min(20.0, max(14.0, math.ceil(avg_speed * 2.0) / 2.0))
    try:
        daily_consumption = float(BUNKER_SEA_BY_CAPACITY[capacity_teu][rounded_speed])
    except KeyError as exc:
        raise ValueError(
            "_placeholder_sail_arc_cost: missing sea bunker consumption for "
            f"capacity={capacity_teu}, speed={rounded_speed}."
        ) from exc

    distance_matrix = ocam_utils.DISTANCE_MATRIX
    distance_row = distance_matrix.get((from_port_code, to_port_code)) if distance_matrix is not None else None
    if distance_row is None:
        raise ValueError(
            "_placeholder_sail_arc_cost: missing distance components for " f"{from_port_code}->{to_port_code}."
        )
    eca_distance = float(distance_row["eca_distance"])
    mgo_rate = min(1.0, max(0.0, eca_distance / distance))

    sail_hours = distance / rounded_speed if rounded_speed > 0 else 0.0
    bunker_consumption_tons = (daily_consumption / 24.0) * sail_hours
    mgo_consumption = bunker_consumption_tons * mgo_rate
    lsfo_consumption = bunker_consumption_tons - mgo_consumption

    prices = _bunker_prices_for_lane_or_unattributed_default(year_month, lane_code)
    mgo_price = prices["MGO"]
    lsfo_price = prices["LSFO"]

    return lsfo_consumption * float(lsfo_price) + mgo_consumption * float(mgo_price)


def _bunker_year_months_with_complete_prices() -> list[str]:
    mgo_months = {year_month for year_month, bunker_type in BUNKER_PRICES_BY_YEAR_MONTH_TYPE if bunker_type == "MGO"}
    lsfo_months = {year_month for year_month, bunker_type in BUNKER_PRICES_BY_YEAR_MONTH_TYPE if bunker_type == "LSFO"}
    months = sorted(mgo_months & lsfo_months)
    if not months:
        raise ValueError("_bunker_year_months_with_complete_prices: no year-month has both MGO and LSFO prices.")
    return months


def _lb_sail_arc_cost_for_capacity(
    capacity_teu: int,
    lane_code: str | None,
    from_port_code: str,
    to_port_code: str,
    distance: float,
    sail_time: float,
) -> float:
    return min(
        _placeholder_sail_arc_cost_for_capacity(
            capacity_teu=capacity_teu,
            lane_code=lane_code,
            year_month=year_month,
            from_port_code=from_port_code,
            to_port_code=to_port_code,
            distance=distance,
            sail_time=sail_time,
        )
        for year_month in _bunker_year_months_with_complete_prices()
    )


def _sail_cost_by_capacity_for_key(cost_key: tuple[Any, ...], unique_capacities: list[int]) -> dict[int, float]:
    lane_code, year_month, from_port_code, to_port_code, distance, sail_time = cost_key
    if year_month == SAIL_COST_LB_YEAR_MONTH:
        return {
            capacity_teu: _lb_sail_arc_cost_for_capacity(
                capacity_teu=capacity_teu,
                lane_code=lane_code,
                from_port_code=from_port_code,
                to_port_code=to_port_code,
                distance=distance,
                sail_time=sail_time,
            )
            for capacity_teu in unique_capacities
        }
    return {
        capacity_teu: _placeholder_sail_arc_cost_for_capacity(
            capacity_teu=capacity_teu,
            lane_code=lane_code,
            year_month=year_month,
            from_port_code=from_port_code,
            to_port_code=to_port_code,
            distance=distance,
            sail_time=sail_time,
        )
        for capacity_teu in unique_capacities
    }


def _canal_leg_bunker_cost_for_capacity(
    capacity_teu: int,
    lane_code: str | None,
    year_month: str,
    distance: float,
    eca_distance: float,
    speed: float,
) -> float:
    if distance <= 1e-9:
        return 0.0
    try:
        daily_consumption = float(BUNKER_SEA_BY_CAPACITY[capacity_teu][speed])
    except KeyError as exc:
        raise ValueError(
            "multicommodity_flow: missing sea bunker consumption for "
            f"capacity={capacity_teu}, speed={speed}."
        ) from exc
    eca_rate = min(1.0, max(0.0, eca_distance / distance))
    sail_hours = distance / speed
    bunker_consumption_tons = (daily_consumption / 24.0) * sail_hours
    prices = _bunker_prices_for_lane_or_unattributed_default(year_month, lane_code)
    return bunker_consumption_tons * ((1.0 - eca_rate) * prices["LSFO"] + eca_rate * prices["MGO"])


def _canal_route_bunker_cost_for_month(
    capacity_teu: int,
    lane_code: str | None,
    year_month: str,
    canal_port_code: str,
    leg1_distance: float,
    leg1_eca_distance: float,
    leg2_distance: float,
    leg2_eca_distance: float,
    passage_hours: float,
    sail_time: float,
) -> float:
    speed_table = BUNKER_SEA_BY_CAPACITY.get(capacity_teu)
    if speed_table is None:
        raise ValueError(f"multicommodity_flow: missing sea bunker consumption for capacity={capacity_teu}.")
    speeds = sorted(speed for speed in speed_table if 0.0 < speed <= 20.0 + 1e-9)
    if not speeds:
        raise ValueError(f"multicommodity_flow: no positive sea speed up to 20 knots for capacity={capacity_teu}.")

    best = math.inf
    for leg1_speed in speeds:
        remaining_hours = sail_time - passage_hours - leg1_distance / leg1_speed
        if remaining_hours < -1e-9:
            continue
        if leg2_distance <= 1e-9:
            leg2_speed = speeds[0]
        else:
            required_leg2_speed = leg2_distance / (remaining_hours + 1e-9)
            speed_index = bisect_left(speeds, required_leg2_speed)
            if speed_index == len(speeds):
                continue
            leg2_speed = speeds[speed_index]
        leg2_hours = 0.0 if leg2_distance <= 1e-9 else leg2_distance / leg2_speed
        if leg1_distance / leg1_speed + leg2_hours + passage_hours > sail_time + 1e-9:
            continue
        cost = _canal_leg_bunker_cost_for_capacity(
            capacity_teu,
            lane_code,
            year_month,
            leg1_distance,
            leg1_eca_distance,
            leg1_speed,
        ) + _canal_leg_bunker_cost_for_capacity(
            capacity_teu,
            lane_code,
            year_month,
            leg2_distance,
            leg2_eca_distance,
            leg2_speed,
        )
        if cost < best:
            best = cost
    if not math.isfinite(best):
        raise NoFeasibleCanalSpeedPair(
            "multicommodity_flow: no feasible canal speed pair for "
            f"capacity={capacity_teu}, sail_time={sail_time}, passage_hours={passage_hours}, "
            f"leg1_distance={leg1_distance}, leg2_distance={leg2_distance}."
        )
    try:
        port_consumption = BUNKER_PORT_BY_CAPACITY[capacity_teu]
    except KeyError as exc:
        raise ValueError(f"multicommodity_flow: missing port bunker consumption for capacity={capacity_teu}.") from exc
    prices = _bunker_prices_for_lane_or_unattributed_default(year_month, lane_code)
    passage_bunker_tons = float(port_consumption["consumption_for_pilot"]) * passage_hours
    if INSTANCE_DATA is not None and _canonical_canal_port_code(canal_port_code) in INSTANCE_DATA.eca_ports:
        best += passage_bunker_tons * prices["MGO"]
    else:
        best += passage_bunker_tons * prices["LSFO"]
    return best


def _canal_route_bunker_cost_for_capacity(
    capacity_teu: int,
    lane_code: str | None,
    year_month: str,
    canal_port_code: str,
    leg1_distance: float,
    leg1_eca_distance: float,
    leg2_distance: float,
    leg2_eca_distance: float,
    passage_hours: float,
    sail_time: float,
) -> float:
    if year_month == SAIL_COST_LB_YEAR_MONTH:
        return min(
            _canal_route_bunker_cost_for_month(
                capacity_teu,
                lane_code,
                candidate_year_month,
                canal_port_code,
                leg1_distance,
                leg1_eca_distance,
                leg2_distance,
                leg2_eca_distance,
                passage_hours,
                sail_time,
            )
            for candidate_year_month in _bunker_year_months_with_complete_prices()
        )
    return _canal_route_bunker_cost_for_month(
        capacity_teu,
        lane_code,
        year_month,
        canal_port_code,
        leg1_distance,
        leg1_eca_distance,
        leg2_distance,
        leg2_eca_distance,
        passage_hours,
        sail_time,
    )


def _canal_route_cost_by_capacity_for_key(
    cost_key: tuple[Any, ...], unique_capacities: list[int]
) -> dict[int, float]:
    (
        lane_code,
        year_month,
        _from_port_code,
        canal_port_code,
        _to_port_code,
        _direction,
        leg1_distance,
        leg1_eca_distance,
        leg2_distance,
        leg2_eca_distance,
        passage_hours,
        sail_time,
    ) = cost_key
    return {
        capacity_teu: _canal_route_bunker_cost_for_capacity(
            capacity_teu,
            lane_code,
            year_month,
            canal_port_code,
            leg1_distance,
            leg1_eca_distance,
            leg2_distance,
            leg2_eca_distance,
            passage_hours,
            sail_time,
        )
        for capacity_teu in unique_capacities
    }


def _bunker_prices_for_lane_or_unattributed_default(year_month: str, lane_code: str | None) -> dict[str, float]:
    if not lane_code:
        return {
            "LSFO": UNATTRIBUTED_BUNKER_PRICE_PER_TON,
            "MGO": UNATTRIBUTED_BUNKER_PRICE_PER_TON,
        }
    mgo_price = BUNKER_PRICE_BY_KEY.get((year_month, lane_code, "MGO"))
    lsfo_price = BUNKER_PRICE_BY_KEY.get((year_month, lane_code, "LSFO"))
    if mgo_price is None:
        raise ValueError(
            f"multicommodity_flow: missing MGO price for year_month={year_month}, lane_code={lane_code}."
        )
    if lsfo_price is None:
        raise ValueError(
            f"multicommodity_flow: missing LSFO price for year_month={year_month}, lane_code={lane_code}."
        )
    return {"LSFO": float(lsfo_price), "MGO": float(mgo_price)}


def _edge_lane_info(network: nx.DiGraph, from_node_id: str, to_node_id: str) -> tuple[str | None, str | None]:
    left_owner = network.nodes[from_node_id].get("owner")
    right_owner = network.nodes[to_node_id].get("owner")
    for owner in (left_owner, right_owner):
        if isinstance(owner, NodeGroup):
            return owner.event.lane_code, owner.event.proforma_name
    return None, None


def _edge_cost_lane_like_evaluation(network: nx.DiGraph, from_node_id: str, to_node_id: str) -> str | None:
    left_owner = network.nodes[from_node_id].get("owner")
    right_owner = network.nodes[to_node_id].get("owner")
    to_event = network.nodes[to_node_id]["node"].event
    if isinstance(left_owner, NodeGroup) and isinstance(to_event, InLaneSail):
        return to_event.lane_code
    if isinstance(left_owner, NodeGroup) and isinstance(right_owner, NodeGroup):
        left_key = (left_owner.event.lane_code, left_owner.event.proforma_name, left_owner.event.position_no)
        right_key = (right_owner.event.lane_code, right_owner.event.proforma_name, right_owner.event.position_no)
        if left_key == right_key:
            return left_owner.event.lane_code
        return right_owner.event.lane_code
    if isinstance(right_owner, NodeGroup):
        return right_owner.event.lane_code
    return None


def _service_owner_for_edge(network: nx.DiGraph, from_node_id: str, to_node_id: str) -> NodeGroup | None:
    from_data = network.nodes[from_node_id]
    to_data = network.nodes[to_node_id]
    owner = from_data.get("owner")
    if (
        isinstance(owner, NodeGroup)
        and owner is to_data.get("owner")
        and from_data.get("label") == "pilot_in"
        and to_data.get("label") == "pilot_out"
    ):
        return owner
    return None


def _service_cost_key(owner: NodeGroup) -> tuple[Any, ...]:
    event = owner.event
    return (
        event.lane_code,
        event.proforma_name,
        event.port_code,
        int(event.port_seq),
        owner.direction,
        event.pilot_in_start,
        event.berthing_start,
        event.berthing_end,
        event.pilot_out_end,
        bool(owner.is_canal),
        bool(getattr(owner, "is_last", False)),
    )


def _portstay_bunker_cost_for_capacity(capacity_teu: int, service_cost_key: tuple[Any, ...]) -> float:
    lane_code, _, port_code, _, _, pilot_in_start, berthing_start, berthing_end, pilot_out_end, _, _ = service_cost_key
    try:
        port_consumption = BUNKER_PORT_BY_CAPACITY[capacity_teu]
    except KeyError as exc:
        raise ValueError(f"multicommodity_flow: missing port bunker consumption for capacity={capacity_teu}.") from exc
    pilot_hours = (berthing_start - pilot_in_start).total_seconds() / 3600
    pilot_hours += (pilot_out_end - berthing_end).total_seconds() / 3600
    bunker_consumption_tons = float(port_consumption["consumption_for_pilot"]) * pilot_hours
    prices = _bunker_prices_for_lane_or_unattributed_default(to_year_month(pilot_out_end), lane_code)
    if INSTANCE_DATA is not None and port_code in INSTANCE_DATA.eca_ports:
        return bunker_consumption_tons * prices["MGO"]
    return bunker_consumption_tons * prices["LSFO"]


def _canonical_canal_port_code(port_code: str) -> str:
    return "EGSCA" if port_code == "EGSUZ" else port_code


def _service_canal_cost_key(service_cost_key: tuple[Any, ...]) -> tuple[str, str] | None:
    lane_code, proforma_name, port_code, port_seq, direction, _, _, _, _, is_canal, is_last = service_cost_key
    if not is_canal:
        return None
    canal_port_code = _canonical_canal_port_code(port_code)
    version = lookup_version(lane_code, proforma_name)
    rotations = sorted(version["port_rotation"], key=lambda row: row["port_seq"])
    index = next((i for i, row in enumerate(rotations) if int(row["port_seq"]) == int(port_seq)), None)
    if index is not None and rotations:
        prev_port_code = rotations[index - 1]["port_code"]
        next_port_code = rotations[(index + 1) % len(rotations)]["port_code"]
        direction = CANAL_DIRECTION_BY_KEY.get((prev_port_code, canal_port_code, next_port_code), direction)
    if direction is None:
        raise ValueError(f"multicommodity_flow: missing canal direction for port_code={port_code!r}.")
    return canal_port_code, direction


def _lookup_lane_direction(lane_code: str, proforma_name: str, port_seq: int) -> str:
    try:
        return DIRECTION_BY_LANE_VERSION_SEQ[(lane_code, proforma_name, port_seq)]
    except KeyError as exc:
        raise ValueError(
            f"multicommodity_flow: missing direction for lane={lane_code}, proforma={proforma_name}, port_seq={port_seq}."
        ) from exc


def _interval_days(start_time, end_time) -> float:
    return (end_time - start_time).total_seconds() / (24 * 3600)


def _opportunity_cost_for_group_interval(owner: NodeGroup, start_time, end_time) -> float:
    direction = _lookup_lane_direction(owner.event.lane_code, owner.event.proforma_name, owner.event.port_seq)
    opp_key = (owner.event.lane_code, owner.event.proforma_name, direction)
    if opp_key not in OPPORTUNITY_COST_BY_KEY:
        raise ValueError(f"multicommodity_flow: missing opportunity cost for {opp_key}.")
    return OPPORTUNITY_COST_BY_KEY[opp_key] * _interval_days(start_time, end_time)


def _service_opportunity_cost(service_cost_key: tuple[Any, ...]) -> float:
    lane_code, proforma_name, _, port_seq, _, pilot_in_start, _, _, pilot_out_end, _, _ = service_cost_key
    direction = _lookup_lane_direction(lane_code, proforma_name, port_seq)
    opp_key = (lane_code, proforma_name, direction)
    if opp_key not in OPPORTUNITY_COST_BY_KEY:
        raise ValueError(f"multicommodity_flow: missing opportunity cost for {opp_key}.")
    return OPPORTUNITY_COST_BY_KEY[opp_key] * _interval_days(pilot_in_start, pilot_out_end)


def _sail_opportunity_cost(network: nx.DiGraph, from_node_id: str, to_node_id: str) -> float:
    if network.nodes[from_node_id].get("label") != "pilot_out" or network.nodes[to_node_id].get("label") != "pilot_in":
        return 0.0
    left_owner = network.nodes[from_node_id].get("owner")
    right_owner = network.nodes[to_node_id].get("owner")
    if not isinstance(left_owner, NodeGroup) or not isinstance(right_owner, NodeGroup):
        return 0.0
    left_event = left_owner.event
    right_event = right_owner.event
    left_key = (left_event.lane_code, left_event.proforma_name, left_event.position_no)
    right_key = (right_event.lane_code, right_event.proforma_name, right_event.position_no)
    if left_key != right_key:
        return 0.0
    direction = _lookup_lane_direction(left_event.lane_code, left_event.proforma_name, left_event.port_seq)
    opp_key = (left_event.lane_code, left_event.proforma_name, direction)
    if opp_key not in OPPORTUNITY_COST_BY_KEY:
        raise ValueError(f"multicommodity_flow: missing opportunity cost for {opp_key}.")
    from_node = network.nodes[from_node_id]["node"]
    to_node = network.nodes[to_node_id]["node"]
    return OPPORTUNITY_COST_BY_KEY[opp_key] * _interval_days(from_node.node_out_time, to_node.node_in_time)


def _same_lane_position(left_owner: NodeGroup, right_owner: NodeGroup) -> bool:
    return (
        left_owner.event.lane_code == right_owner.event.lane_code
        and left_owner.event.proforma_name == right_owner.event.proforma_name
        and left_owner.event.position_no == right_owner.event.position_no
    )


def _is_sequential_edge(network: nx.DiGraph, from_node_id: str, to_node_id: str) -> bool:
    from_data = network.nodes[from_node_id]
    to_data = network.nodes[to_node_id]
    left_owner = from_data.get("owner")
    right_owner = to_data.get("owner")
    if not isinstance(left_owner, NodeGroup) or not isinstance(right_owner, NodeGroup):
        return False
    if left_owner is right_owner or not _same_lane_position(left_owner, right_owner):
        return False
    return (from_data.get("label"), to_data.get("label")) in {
        ("pilot_out", "pilot_in"),
        ("pilot_out", "ts_out_before"),
        ("ts_in_after", "pilot_in"),
        ("ts_in_after", "ts_out_before"),
    }


def _virtual_opportunity_cost(network: nx.DiGraph, from_node_id: str, to_node_id: str) -> float:
    from_data = network.nodes[from_node_id]
    to_data = network.nodes[to_node_id]
    from_owner = from_data.get("owner")
    to_owner = to_data.get("owner")
    from_label = from_data.get("label")
    to_label = to_data.get("label")
    ts_labels = {"ts_in_before", "ts_in_after", "ts_out_before", "ts_out_after"}
    # v6_lb relaxes TS node times to make a lower-bound network. Those relaxed TS
    # intervals can have node_in after node_out, so charging v0 opportunity cost
    # on TS-touching arcs can create artificial negative objective coefficients.
    # v0 is only a coverage device here; TS-specific economic effects are handled
    # by y variables, so TS-touching v0 arcs intentionally carry no arc cost.
    if from_label in ts_labels or to_label in ts_labels:
        return 0.0
    from_node = from_data["node"]
    to_node = to_data["node"]

    service_owner = _service_owner_for_edge(network, from_node_id, to_node_id)
    if service_owner is not None:
        return _opportunity_cost_for_group_interval(service_owner, from_node.node_in_time, to_node.node_out_time)

    if isinstance(from_owner, NodeGroup) and isinstance(to_node.event, InLaneSail):
        return _inlane_event_opportunity_cost(to_node.event)

    if isinstance(from_owner, NodeGroup) and from_owner is to_owner:
        if (from_label, to_label) == ("ts_in_before", "pilot_in"):
            return _opportunity_cost_for_group_interval(from_owner, from_node.node_in_time, to_node.node_in_time)
        if (from_label, to_label) == ("pilot_out", "ts_out_after"):
            return _opportunity_cost_for_group_interval(from_owner, from_node.node_out_time, to_node.node_out_time)
        return 0.0

    if not isinstance(from_owner, NodeGroup) or not isinstance(to_owner, NodeGroup):
        return 0.0
    if not _same_lane_position(from_owner, to_owner):
        return 0.0

    if (from_label, to_label) == ("pilot_out", "pilot_in"):
        return _opportunity_cost_for_group_interval(from_owner, from_node.node_out_time, to_node.node_in_time)
    if (from_label, to_label) == ("pilot_out", "ts_out_before"):
        return _opportunity_cost_for_group_interval(
            from_owner, from_node.node_out_time, to_node.node_in_time
        ) + _opportunity_cost_for_group_interval(to_owner, to_node.node_in_time, to_node.node_out_time)
    if (from_label, to_label) == ("ts_in_after", "pilot_in"):
        return _opportunity_cost_for_group_interval(
            from_owner, from_node.node_in_time, from_node.node_out_time
        ) + _opportunity_cost_for_group_interval(from_owner, from_node.node_out_time, to_node.node_in_time)
    if (from_label, to_label) == ("ts_in_after", "ts_out_before"):
        return (
            _opportunity_cost_for_group_interval(from_owner, from_node.node_in_time, from_node.node_out_time)
            + _opportunity_cost_for_group_interval(from_owner, from_node.node_out_time, to_node.node_in_time)
            + _opportunity_cost_for_group_interval(to_owner, to_node.node_in_time, to_node.node_out_time)
        )
    return 0.0


def _is_v0_allowed_base_edge(network: nx.DiGraph, from_node_id: str, to_node_id: str) -> bool:
    from_owner = network.nodes[from_node_id].get("owner")
    to_owner = network.nodes[to_node_id].get("owner")
    # Cross-lane base arcs are valid for real vessels because they represent
    # possible repositioning/assignment moves in the shared network. v0 is
    # different: it is a virtual coverage path, not a physical vessel. Letting v0
    # jump between two different lane-proforma-position groups lets it stitch
    # unrelated service fragments together and can distort opportunity accounting.
    # This includes same-port cross-lane "Arc" edges, not only SailArc edges.
    if isinstance(from_owner, NodeGroup) and isinstance(to_owner, NodeGroup):
        if from_owner is not to_owner and not _same_lane_position(from_owner, to_owner):
            return False
    if isinstance(from_owner, NodeGroup) and from_owner is to_owner:
        return True
    return _is_sequential_edge(network, from_node_id, to_node_id)


def _inlane_event_opportunity_cost(event: InLaneEvent) -> float:
    if not is_interval_event(event):
        return 0.0
    if isinstance(event, InLaneSail):
        direction = _lookup_lane_direction(event.lane_code, event.proforma_name, event.from_port_seq)
    elif isinstance(event, PortStay):
        direction = _lookup_lane_direction(event.lane_code, event.proforma_name, event.port_seq)
    else:
        return 0.0
    opp_key = (event.lane_code, event.proforma_name, direction)
    if opp_key not in OPPORTUNITY_COST_BY_KEY:
        raise ValueError(f"multicommodity_flow: missing opportunity cost for {opp_key}.")
    return OPPORTUNITY_COST_BY_KEY[opp_key] * _interval_days(event_start_time(event), event_end_time(event))


def _coverage_key(event: InLaneEvent) -> tuple[tuple[str, object], ...]:
    payload = event.to_dict()
    payload.pop("distance", None)
    payload.pop("avg_speed", None)
    if isinstance(event, InLaneSail):
        payload.pop("sea_sail_end", None)
    return tuple((key, value) for key, value in sorted(payload.items()))


def _service_coverage_key(owner: NodeGroup) -> tuple[tuple[str, object], ...]:
    return _coverage_key(owner.event)


def _sail_coverage_key(
    network: nx.DiGraph, from_node_id: str, to_node_id: str
) -> tuple[tuple[str, object], ...] | None:
    if network.nodes[from_node_id].get("label") != "pilot_out" or network.nodes[to_node_id].get("label") != "pilot_in":
        return None
    left_owner = network.nodes[from_node_id].get("owner")
    right_owner = network.nodes[to_node_id].get("owner")
    if not isinstance(left_owner, NodeGroup) or not isinstance(right_owner, NodeGroup):
        return None
    left_event = left_owner.event
    right_event = right_owner.event
    if (left_event.lane_code, left_event.proforma_name, left_event.position_no) != (
        right_event.lane_code,
        right_event.proforma_name,
        right_event.position_no,
    ):
        return None
    from_node = network.nodes[from_node_id]["node"]
    return _coverage_key(
        InLaneSail(
            lane_code=left_event.lane_code,
            proforma_name=left_event.proforma_name,
            position_no=left_event.position_no,
            from_port_code=left_event.port_code,
            from_port_seq=left_event.port_seq,
            sea_sail_start=from_node.node_out_time,
            to_port_code=right_event.port_code,
            to_port_seq=right_event.port_seq,
            sea_sail_end=network.nodes[to_node_id]["node"].node_in_time,
        )
    )


def _is_adjacent_inlane_sail_for_canal(
    network: nx.DiGraph,
    from_node_id: str,
    to_node_id: str,
    service_owner: NodeGroup,
    side: str,
    arc_type: str | None = None,
) -> bool:
    if arc_type not in {"SailArc", "HorizonSailArc"}:
        return False
    from_owner = network.nodes[from_node_id].get("owner")
    to_owner = network.nodes[to_node_id].get("owner")
    lane_key = (service_owner.event.lane_code, service_owner.event.proforma_name, service_owner.event.position_no)
    if side == "previous":
        if isinstance(from_owner, NodeGroup) and to_owner is service_owner:
            from_key = (from_owner.event.lane_code, from_owner.event.proforma_name, from_owner.event.position_no)
            return from_key == lane_key and network.nodes[from_node_id].get("label") == "pilot_out"
        from_node = network.nodes[from_node_id]["node"]
        return (
            isinstance(from_node, HorizonSailNode)
            and from_node.horizon_side == "start"
            and (from_node.lane_code, from_node.proforma_name, from_node.position_no) == lane_key
        )
    if side == "next":
        if from_owner is service_owner and isinstance(to_owner, NodeGroup):
            to_key = (to_owner.event.lane_code, to_owner.event.proforma_name, to_owner.event.position_no)
            return to_key == lane_key and network.nodes[to_node_id].get("label") == "pilot_in"
        to_node = network.nodes[to_node_id]["node"]
        return (
            isinstance(to_node, HorizonSailNode)
            and to_node.horizon_side == "end"
            and (to_node.lane_code, to_node.proforma_name, to_node.position_no) == lane_key
        )
    raise ValueError(f"_is_adjacent_inlane_sail_for_canal: invalid side={side!r}.")


def _node_lane_version(network: nx.DiGraph, node_id: str) -> tuple[str, str] | None:
    owner = network.nodes[node_id].get("owner")
    if isinstance(owner, NodeGroup):
        return owner.event.lane_code, owner.event.proforma_name
    event = network.nodes[node_id]["node"].event
    if isinstance(event, InLaneEvent):
        return event.lane_code, event.proforma_name
    return None


def _canal_arc_cost_for_vessel(vessel_code: str, canal_port_code: str, direction: str) -> float:
    port_code = _canonical_canal_port_code(canal_port_code)
    fee = CANAL_FEE_BY_KEY.get((vessel_code, direction, port_code))
    if fee is not None:
        return fee
    raise KeyError(
        "missing canal fee for "
        f"vessel_code={vessel_code!r}, canal_port_code={canal_port_code!r}, direction={direction!r}."
    )


def enumerate_year_months_in_interval(start: datetime, end: datetime) -> list[str]:
    if end < start:
        raise ValueError(
            "enumerate_year_months_in_interval: empty interval. " f"start={start!r}, end={end!r}."
        )
    current = datetime(start.year, start.month, 1)
    end_month = datetime(end.year, end.month, 1)
    months = []
    while current <= end_month:
        months.append(to_year_month(current))
        if current.month == 12:
            current = datetime(current.year + 1, 1, 1)
        else:
            current = datetime(current.year, current.month + 1, 1)
    return months


def min_ts_price_over_yearmonths(year_months: list[str], lane_code: str, port_code: str) -> float:
    prices = []
    missing = []
    for year_month in year_months:
        try:
            prices.append(float(lookup_ts_cost(year_month, lane_code, port_code)))
        except KeyError:
            missing.append(year_month)
    if missing:
        raise ValueError(
            "min_ts_price_over_yearmonths: missing TS price for possible unloading-start month(s). "
            f"lane_code={lane_code!r}, port_code={port_code!r}, missing_year_months={missing!r}."
        )
    return min(prices)


def get_ts_cost_window(tsi_node: Node, tso_node: Node) -> tuple[datetime, datetime]:
    if INSTANCE_DATA is None:
        raise ValueError("get_ts_cost_window: initialize_multicommodity() must be called first.")
    planning_start = INSTANCE_DATA.planning_horizon["start"]
    planning_end = INSTANCE_DATA.planning_horizon["end"]
    start = max(
        min(
            tsi_node.node_in_time,
            tsi_node.node_out_time,
            tso_node.node_in_time,
            tso_node.node_out_time - timedelta(hours=6),
        ),
        planning_start,
    )
    end = min(
        max(
            tsi_node.node_in_time,
            tsi_node.node_out_time,
            tso_node.node_in_time,
            tso_node.node_out_time,
        ),
        planning_end,
    )
    if start > end:
        raise ValueError(
            "get_ts_cost_window: TS cost window does not intersect the planning horizon. "
            f"tsi_node_id={tsi_node.get_id()!r}, tso_node_id={tso_node.get_id()!r}, "
            f"start={start!r}, end={end!r}, planning_start={planning_start!r}, planning_end={planning_end!r}."
        )
    return start, end


def compute_lb_ts_cost(owner: NodeGroup, tsi_node: Node, tso_node: Node) -> tuple[float, tuple[datetime, datetime], list[str]]:
    interval = get_ts_cost_window(tsi_node, tso_node)
    year_months = enumerate_year_months_in_interval(*interval)
    return min_ts_price_over_yearmonths(year_months, owner.event.lane_code, owner.event.port_code), interval, year_months


def _collect_ts_units(network: nx.DiGraph) -> list[dict[str, Any]]:
    ts_units = []
    seen_owner_ids = set()
    for _, node_data in _progress(
        network.nodes(data=True),
        total=network.number_of_nodes(),
        desc="ts units",
    ):
        owner = node_data.get("owner")
        if not isinstance(owner, NodeGroup) or owner.get_id() in seen_owner_ids:
            continue
        seen_owner_ids.add(owner.get_id())
        for suffix, tsi_label, tso_label in (
            ("before", "ts_in_before", "ts_out_before"),
            ("after", "ts_in_after", "ts_out_after"),
        ):
            tsi_node = getattr(owner, tsi_label, None)
            tso_node = getattr(owner, tso_label, None)
            if not isinstance(tsi_node, Node) or not isinstance(tso_node, Node):
                continue
            cost, unloading_start_interval, ts_cost_year_months = compute_lb_ts_cost(owner, tsi_node, tso_node)
            ts_units.append(
                {
                    "unit_id": f"{owner.get_id()}:{suffix}",
                    "suffix": suffix,
                    "tsi_node_id": tsi_node.get_id(),
                    "tso_node_id": tso_node.get_id(),
                    "cost": cost,
                    "unloading_start_interval_start": unloading_start_interval[0],
                    "unloading_start_interval_end": unloading_start_interval[1],
                    "ts_cost_year_months": ts_cost_year_months,
                    "owner_id": owner.get_id(),
                    "lane_code": owner.event.lane_code,
                    "proforma_name": owner.event.proforma_name,
                    "position_no": owner.event.position_no,
                    "port_code": owner.event.port_code,
                }
            )
    return ts_units


def _find_unique_target_node_id(network: nx.DiGraph) -> str:
    target_node_ids = [node_id for node_id, data in network.nodes(data=True) if isinstance(data.get("node"), T)]
    if len(target_node_ids) != 1:
        raise ValueError(
            "_find_unique_target_node_id: expected exactly one target node. " f"found={target_node_ids!r}."
        )
    return target_node_ids[0]


def _find_source_node_id(network: nx.DiGraph, vessel_code: str) -> str:
    delivery_candidates = [
        node_id
        for node_id, data in network.nodes(data=True)
        if isinstance(data.get("node"), D) and getattr(data["node"], "vessel_code", None) == vessel_code
    ]
    if len(delivery_candidates) > 1:
        raise ValueError(f"_find_source_node_id: multiple delivery source candidates for vessel {vessel_code!r}.")
    if len(delivery_candidates) == 1:
        return delivery_candidates[0]

    current_assignment_sources = network.graph.get("current_assignment_source_node_id_by_vessel", {})
    current_assignment_source = current_assignment_sources.get(vessel_code)
    if current_assignment_source is not None:
        return current_assignment_source

    raise ValueError("_find_source_node_id: unable to determine source node. " f"vessel_code={vessel_code!r}.")


def _find_sink_node_id(network: nx.DiGraph, vessel_code: str, target_node_id: str) -> str:
    redelivery_candidates = [
        node_id
        for node_id, data in network.nodes(data=True)
        if isinstance(data.get("node"), R) and getattr(data["node"], "vessel_code", None) == vessel_code
    ]
    if len(redelivery_candidates) > 1:
        raise ValueError(f"_find_sink_node_id: multiple redelivery sink candidates for vessel {vessel_code!r}.")
    if len(redelivery_candidates) == 1:
        return redelivery_candidates[0]
    return target_node_id


def _edge_cost_from_model_edge(
    edge: dict[str, Any],
    vessel_code: str,
    capacity_by_vessel: dict[str, int],
    sail_cost_cache: dict[tuple[Any, ...], dict[int, float]],
    canal_route_cost_cache: dict[tuple[Any, ...], dict[int, float]],
    service_cost_cache: dict[tuple[Any, ...], dict[int, float]],
) -> float:
    if vessel_code == VIRTUAL_VESSEL_CODE:
        return float(edge.get("virtual_opportunity_cost") or 0.0)
    edge_cost = 0.0
    if edge["arc_type"] in {"SailArc", "HorizonSailArc"}:
        edge_cost += float(sail_cost_cache[edge["sail_cost_key"]][capacity_by_vessel[vessel_code]])
    if edge["arc_type"] == "CanalSailArc":
        edge_cost += float(canal_route_cost_cache[edge["canal_route_cost_key"]][capacity_by_vessel[vessel_code]])
        edge_cost += _canal_arc_cost_for_vessel(vessel_code, *edge["canal_route_fee_key"])
    if edge["service_cost_key"] is not None:
        edge_cost += float(service_cost_cache[edge["service_cost_key"]][capacity_by_vessel[vessel_code]])
    if edge["canal_cost_key"] is not None:
        edge_cost += _canal_arc_cost_for_vessel(vessel_code, *edge["canal_cost_key"])
    return edge_cost


def _node_arrival_lane_code(network: nx.DiGraph, node_id: str) -> str | None:
    owner = network.nodes[node_id].get("owner")
    if isinstance(owner, NodeGroup):
        return owner.event.lane_code
    if isinstance(owner, HorizonSailNode):
        return owner.lane_code
    return None


def _build_selected_path(
    vessel_code: str,
    source_node_id: str,
    sink_node_id: str,
    model_edge_by_id: dict[str, dict[str, Any]],
    selected_edge_ids_by_vessel: dict[str, set[str]],
    capacity_by_vessel: dict[str, int],
    sail_cost_cache: dict[tuple[Any, ...], dict[int, float]],
    canal_route_cost_cache: dict[tuple[Any, ...], dict[int, float]],
    service_cost_cache: dict[tuple[Any, ...], dict[int, float]],
) -> dict[str, Any]:
    selected_edge_ids = selected_edge_ids_by_vessel[vessel_code]
    outgoing_by_node: dict[str, str] = {}
    for edge_id in selected_edge_ids:
        edge = model_edge_by_id[edge_id]
        if edge["from_node_id"] in outgoing_by_node:
            raise ValueError(
                "_build_selected_path: multiple outgoing selected edges from one node. "
                f"vessel_code={vessel_code!r}, node_id={edge['from_node_id']!r}."
            )
        outgoing_by_node[edge["from_node_id"]] = edge_id

    node_path = [source_node_id]
    edge_path = []
    total_profit = 0.0
    visited_nodes = {source_node_id}
    current_node_id = source_node_id
    while current_node_id != sink_node_id:
        edge_id = outgoing_by_node.get(current_node_id)
        if edge_id is None:
            raise ValueError(
                "_build_selected_path: selected edges do not form a source-to-sink path. "
                f"vessel_code={vessel_code!r}, stuck_at={current_node_id!r}."
            )
        edge = model_edge_by_id[edge_id]
        edge_cost = _edge_cost_from_model_edge(
            edge, vessel_code, capacity_by_vessel, sail_cost_cache, canal_route_cost_cache, service_cost_cache
        )
        edge_path.append(
            {
                "edge_id": edge_id,
                "from_node_id": edge["from_node_id"],
                "to_node_id": edge["to_node_id"],
                "arc_id": edge["arc_id"],
                "arc_type": edge["arc_type"],
                "profit": edge_cost,
            }
        )
        total_profit += edge_cost
        current_node_id = edge["to_node_id"]
        if current_node_id in visited_nodes and current_node_id != sink_node_id:
            raise ValueError(
                "_build_selected_path: selected base edges contain a cycle. "
                f"vessel_code={vessel_code!r}, node_id={current_node_id!r}."
            )
        visited_nodes.add(current_node_id)
        node_path.append(current_node_id)

    return {
        "vessel_code": vessel_code,
        "source_node_id": source_node_id,
        "sink_node_id": sink_node_id,
        "node_path": node_path,
        "edge_path": edge_path,
        "total_profit": total_profit,
    }


def _build_virtual_selected_paths(
    virtual_source_node_id: str,
    target_node_id: str,
    network: nx.DiGraph,
    model_edge_by_id: dict[str, dict[str, Any]],
    selected_edge_ids: set[str],
) -> list[dict[str, Any]]:
    outgoing_by_node: dict[str, list[str]] = defaultdict(list)
    for edge_id in selected_edge_ids:
        edge = model_edge_by_id[edge_id]
        outgoing_by_node[edge["from_node_id"]].append(edge_id)

    paths = []
    for path_index, first_edge_id in enumerate(sorted(outgoing_by_node.get(virtual_source_node_id, [])), start=1):
        full_node_path = [virtual_source_node_id]
        full_edge_path = []
        current_node_id = virtual_source_node_id
        visited_nodes = {virtual_source_node_id}
        while current_node_id != target_node_id:
            outgoing_edge_ids = outgoing_by_node.get(current_node_id, [])
            if current_node_id == virtual_source_node_id:
                edge_id = first_edge_id
            elif len(outgoing_edge_ids) == 1:
                edge_id = outgoing_edge_ids[0]
            else:
                raise ValueError(
                    "_build_virtual_selected_paths: selected virtual edges do not form disjoint paths. "
                    f"node_id={current_node_id!r}, outgoing={outgoing_edge_ids!r}."
                )
            edge = model_edge_by_id[edge_id]
            edge_cost = float(edge.get("virtual_opportunity_cost") or 0.0)
            full_edge_path.append(
                {
                    "edge_id": edge_id,
                    "from_node_id": edge["from_node_id"],
                    "to_node_id": edge["to_node_id"],
                    "arc_id": edge["arc_id"],
                    "arc_type": edge["arc_type"],
                    "profit": edge_cost,
                }
            )
            current_node_id = edge["to_node_id"]
            if current_node_id in visited_nodes and current_node_id != target_node_id:
                raise ValueError(
                    "_build_virtual_selected_paths: selected virtual edges contain a cycle. "
                    f"node_id={current_node_id!r}."
                )
            visited_nodes.add(current_node_id)
            full_node_path.append(current_node_id)

        node_path = [node_id for node_id in full_node_path if node_id in network.nodes]
        edge_path = [
            edge
            for edge in full_edge_path
            if edge["from_node_id"] in network.nodes and edge["to_node_id"] in network.nodes
        ]
        if not node_path:
            continue
        paths.append(
            {
                "vessel_code": f"VIRTUAL{path_index:03d}",
                "source_node_id": node_path[0],
                "sink_node_id": node_path[-1],
                "node_path": node_path,
                "edge_path": edge_path,
                "total_profit": sum(edge["profit"] for edge in edge_path),
                "is_virtual": True,
            }
        )
    return paths


def _visualize_selected_paths(
    network: nx.DiGraph,
    selected_edge_ids_by_vessel: dict[str, set[str]],
    base_edge_by_id: dict[str, dict[str, Any]],
    ts_state_by_unit_id: dict[str, dict[str, bool]],
    flow_value_by_vessel_edge: dict[str, dict[str, float]] | None = None,
    output_path: str = DEFAULT_OUTPUT_PATH,
) -> None:
    return
    viz_mip_result(
        network=network,
        selected_edge_ids_by_vessel=selected_edge_ids_by_vessel,
        base_edge_by_id=base_edge_by_id,
        ts_state_by_unit_id=ts_state_by_unit_id,
        flow_value_by_vessel_edge=flow_value_by_vessel_edge,
        output_path=output_path,
        interactive=True,
    )


def multicommodity_flow(
    network: nx.DiGraph,
    vessel_codes: list[str],
    output_path: str = DEFAULT_OUTPUT_PATH,
    model_name: str = "multicommodity_flow",
    dd_couplings: list[dict[str, object]] | None = None,
    include_virtual_vessel: bool = INCLUDE_VIRTUAL_VESSEL,
) -> dict[str, Any]:
    if INSTANCE_DATA is None:
        raise ValueError("multicommodity_flow: initialize_local_search() must be called first.")

    try:
        import gurobipy as gp
        from gurobipy import GRB
    except ImportError as exc:
        raise ImportError(
            "multicommodity_flow: gurobipy is required but is not installed in this environment."
        ) from exc

    start_time = perf_counter()
    checkpoint = start_time

    def log_stage(message: str) -> None:
        nonlocal checkpoint
        now = perf_counter()
        _progress_write(
            f"[multicommodity_flow] {message} | " f"step={now - checkpoint:.2f}s total={now - start_time:.2f}s"
        )
        checkpoint = now

    _progress_write(
        "[multicommodity_flow] start | "
        f"vessels={len(vessel_codes)} nodes={network.number_of_nodes()} edges={network.number_of_edges()}"
    )

    target_node_id = _find_unique_target_node_id(network)
    idle_node = network.graph["item_by_event_key"].get(IDLE_EVENT_KEY)
    if not isinstance(idle_node, I):
        raise ValueError("multicommodity_flow: unable to find unique idle node.")
    idle_node_id = idle_node.get_id()
    source_node_id_by_vessel = {vessel_code: _find_source_node_id(network, vessel_code) for vessel_code in vessel_codes}
    sink_node_id_by_vessel = {
        vessel_code: _find_sink_node_id(network, vessel_code, target_node_id) for vessel_code in vessel_codes
    }
    log_stage(f"identified source/sink nodes target={target_node_id}")

    capacity_by_vessel = {vessel_code: int(lookup_vessel(vessel_code)["capacity_teu"]) for vessel_code in vessel_codes}
    reefer_by_vessel = {vessel_code: int(lookup_vessel(vessel_code)["reefer_plug"]) for vessel_code in vessel_codes}
    vessels_by_capacity: dict[int, list[str]] = defaultdict(list)
    for vessel_code, capacity_teu in capacity_by_vessel.items():
        vessels_by_capacity[capacity_teu].append(vessel_code)
    vessel_count_by_capacity = {
        capacity_teu: len(grouped_vessel_codes) for capacity_teu, grouped_vessel_codes in vessels_by_capacity.items()
    }
    unique_capacities = sorted(vessels_by_capacity)
    sail_cost_cache: dict[tuple[Any, ...], dict[int, float]] = {}
    canal_route_cost_cache: dict[tuple[Any, ...], dict[int, float]] = {}
    service_cost_cache: dict[tuple[Any, ...], dict[int, float]] = {}
    log_stage("prepared vessel capacity cache " f"unique_capacities={len(unique_capacities)}")

    compatible_lane_versions_by_vessel: dict[str, set[tuple[str, str]]] = {
        vessel_code: set() for vessel_code in vessel_codes
    }
    for lane in INSTANCE_DATA.service_lanes:
        lane_code = lane["lane_code"]
        for version in lane["versions"]:
            proforma_name = version["proforma_name"]
            required_capacity_teu = version["required_capacity_teu"]
            required_reefer_plug = version["required_reefer_plug"]
            capacity_tolerance = required_capacity_teu * 0.05
            reefer_tolerance = required_reefer_plug * 0.05 + 1000000000
            for vessel_code in vessel_codes:
                if abs(capacity_by_vessel[vessel_code] - required_capacity_teu) > capacity_tolerance:
                    continue
                if abs(reefer_by_vessel[vessel_code] - required_reefer_plug) > reefer_tolerance:
                    continue
                compatible_lane_versions_by_vessel[vessel_code].add((lane_code, proforma_name))
    log_stage("prepared vessel-lane compatibility cache")

    all_node_ids = list(network.nodes)
    base_edge_ids = []
    base_edge_by_id: dict[str, dict[str, Any]] = {}
    sail_edge_ids = []
    canal_route_edge_ids = []
    total_sail_cost_all_vessels = 0.0
    total_portstay_cost_all_vessels = 0.0
    total_canal_cost_all_vessels = 0.0
    total_expected_opportunity_cost = 0.0
    skipped_sail_edges_missing_distance = 0
    base_outgoing_edge_ids_by_node: dict[str, list[str]] = defaultdict(list)
    base_incoming_edge_ids_by_node: dict[str, list[str]] = defaultdict(list)

    network_edge_iterable = (
        network.edges(keys=True, data=True)
        if network.is_multigraph()
        else ((from_node_id, to_node_id, None, data) for from_node_id, to_node_id, data in network.edges(data=True))
    )
    for from_node_id, to_node_id, edge_key, data in _progress(
        network_edge_iterable,
        total=network.number_of_edges(),
        desc="base edges",
    ):
        edge_id = f"base:{data.get('arc_id') or edge_key or f'{from_node_id}->{to_node_id}'}"
        cost_key = None
        canal_route_cost_key = None
        canal_route_fee_key = None
        if data.get("arc_type") in {"SailArc", "HorizonSailArc"}:
            to_node = network.nodes[to_node_id]["node"]
            from_node = network.nodes[from_node_id]["node"]
            if data.get("arc_type") == "HorizonSailArc" and isinstance(from_node, HorizonSailNode):
                from_port_code = from_node.from_port_code
                to_port_code = from_node.to_port_code
                sail_cost_time = from_node.node_out_time
            elif data.get("arc_type") == "HorizonSailArc" and isinstance(to_node, HorizonSailNode):
                from_port_code = event_end_port_code(from_node.event)
                to_port_code = to_node.to_port_code
                sail_cost_time = to_node.node_out_time
            else:
                from_port_code = event_end_port_code(from_node.event)
                to_port_code = event_start_port_code(to_node.event)
                sail_cost_time = to_node.node_in_time
            distance_matrix = ocam_utils.DISTANCE_MATRIX
            if distance_matrix is None or (from_port_code, to_port_code) not in distance_matrix:
                skipped_sail_edges_missing_distance += 1
                continue
            lane_code = _edge_cost_lane_like_evaluation(network, from_node_id, to_node_id)
            cost_year_month = to_year_month(sail_cost_time)
            if network.nodes[from_node_id].get("label") in TS_NODE_LABELS or network.nodes[to_node_id].get(
                "label"
            ) in TS_NODE_LABELS:
                cost_year_month = SAIL_COST_LB_YEAR_MONTH
            cost_key = (
                lane_code,
                cost_year_month,
                from_port_code,
                to_port_code,
                float(data["distance"]),
                float(data["sail_time"]),
            )
            cost_by_capacity = sail_cost_cache.get(cost_key)
            if cost_by_capacity is None:
                cost_by_capacity = _sail_cost_by_capacity_for_key(cost_key, unique_capacities)
                sail_cost_cache[cost_key] = cost_by_capacity
            total_sail_cost_all_vessels += sum(
                cost_by_capacity[capacity_teu] * vessel_count_by_capacity[capacity_teu]
                for capacity_teu in unique_capacities
            )
        elif data.get("arc_type") == "CanalSailArc":
            to_node = network.nodes[to_node_id]["node"]
            from_node = network.nodes[from_node_id]["node"]
            from_port_code = event_end_port_code(from_node.event)
            to_port_code = event_start_port_code(to_node.event)
            lane_code = _edge_cost_lane_like_evaluation(network, from_node_id, to_node_id)
            cost_year_month = to_year_month(to_node.node_in_time)
            if network.nodes[from_node_id].get("label") in TS_NODE_LABELS or network.nodes[to_node_id].get(
                "label"
            ) in TS_NODE_LABELS:
                cost_year_month = SAIL_COST_LB_YEAR_MONTH
            canal_port_code = _canonical_canal_port_code(data["canal_port_code"])
            canal_route_cost_key = (
                lane_code,
                cost_year_month,
                from_port_code,
                canal_port_code,
                to_port_code,
                data["canal_direction"],
                float(data["canal_leg1_distance"]),
                float(data["canal_leg1_eca_distance"]),
                float(data["canal_leg2_distance"]),
                float(data["canal_leg2_eca_distance"]),
                float(data["canal_passage_hours"]),
                float(data["sail_time"]),
            )
            canal_route_fee_key = (canal_port_code, data["canal_direction"])
        service_owner = _service_owner_for_edge(network, from_node_id, to_node_id)
        if service_owner is not None:
            service_cost_key = _service_cost_key(service_owner)
            service_cost_by_capacity = service_cost_cache.get(service_cost_key)
            if service_cost_by_capacity is None:
                service_cost_by_capacity = {
                    capacity_teu: _portstay_bunker_cost_for_capacity(capacity_teu, service_cost_key)
                    for capacity_teu in unique_capacities
                }
                service_cost_cache[service_cost_key] = service_cost_by_capacity
            total_portstay_cost_all_vessels += sum(
                service_cost_by_capacity[capacity_teu] * vessel_count_by_capacity[capacity_teu]
                for capacity_teu in unique_capacities
            )
            canal_cost_key = None
        else:
            service_cost_key = None
            canal_cost_key = None
        virtual_opportunity_cost = _virtual_opportunity_cost(network, from_node_id, to_node_id)
        if service_cost_key is not None:
            coverage_key = _service_coverage_key(service_owner)
        elif data.get("arc_type") in {"SailArc", "HorizonSailArc"}:
            coverage_key = _sail_coverage_key(network, from_node_id, to_node_id)
        else:
            coverage_key = None
        total_expected_opportunity_cost += virtual_opportunity_cost
        base_edge_by_id[edge_id] = {
            "edge_id": edge_id,
            "from_node_id": from_node_id,
            "to_node_id": to_node_id,
            "arc_id": data.get("arc_id"),
            "arc_type": data.get("arc_type"),
            "sail_cost_key": cost_key,
            "canal_route_cost_key": canal_route_cost_key,
            "canal_route_fee_key": canal_route_fee_key,
            "service_cost_key": service_cost_key,
            "canal_cost_key": canal_cost_key,
            "virtual_opportunity_cost": virtual_opportunity_cost,
            "coverage_key": coverage_key,
            "to_is_target": to_node_id == target_node_id,
        }
        base_edge_ids.append(edge_id)
        base_outgoing_edge_ids_by_node[from_node_id].append(edge_id)
        base_incoming_edge_ids_by_node[to_node_id].append(edge_id)
        if data.get("arc_type") in {"SailArc", "HorizonSailArc"}:
            sail_edge_ids.append(edge_id)
        if data.get("arc_type") == "CanalSailArc":
            canal_route_edge_ids.append(edge_id)
    log_stage(
        "prepared base edges "
        f"base_edges={len(base_edge_ids)} sail_edges={len(sail_edge_ids)} "
        f"canal_route_edges={len(canal_route_edge_ids)} "
        f"skipped_sail_missing_distance={skipped_sail_edges_missing_distance} all_nodes={len(all_node_ids)}"
    )

    canal_route_feasible_by_capacity_key: dict[tuple[int, tuple[Any, ...]], bool] = {}
    for edge_id in _progress(canal_route_edge_ids, total=len(canal_route_edge_ids), desc="canal route costs"):
        cost_key = base_edge_by_id[edge_id]["canal_route_cost_key"]
        cost_by_capacity = canal_route_cost_cache.setdefault(cost_key, {})
        for capacity_teu in unique_capacities:
            try:
                if capacity_teu not in cost_by_capacity:
                    cost_by_capacity.update(_canal_route_cost_by_capacity_for_key(cost_key, [capacity_teu]))
                canal_route_feasible_by_capacity_key[(capacity_teu, cost_key)] = True
            except NoFeasibleCanalSpeedPair:
                canal_route_feasible_by_capacity_key[(capacity_teu, cost_key)] = False
    log_stage(
        "prepared canal route cost cache "
        f"keys={len(canal_route_cost_cache)} "
        f"capacity_keys={len(canal_route_feasible_by_capacity_key)}"
    )

    ts_units = _collect_ts_units(network)
    log_stage(f"collected ts units count={len(ts_units)}")

    model_edge_ids_by_vessel: dict[str, list[str]] = {}
    model_edge_id_set_by_vessel: dict[str, set[str]] = {}
    wrap_edge_ids_by_vessel: dict[str, str] = {}
    model_edge_by_id: dict[str, dict[str, Any]] = dict(base_edge_by_id)
    model_outgoing_edge_ids_by_vessel_node: dict[str, dict[str, list[str]]] = {}
    model_incoming_edge_ids_by_vessel_node: dict[str, dict[str, list[str]]] = {}
    reachable_node_ids_by_vessel: dict[str, set[str]] = {}
    pruned_base_edge_count = 0

    def base_edge_allowed_for_vessel(vessel_code: str, edge: dict[str, Any]) -> bool:
        if edge["canal_route_cost_key"] is None:
            return True
        return canal_route_feasible_by_capacity_key[(capacity_by_vessel[vessel_code], edge["canal_route_cost_key"])]

    def build_reachable_subgraph(
        vessel_code: str,
    ) -> tuple[str, set[str], list[str], dict[str, list[str]], dict[str, list[str]]]:
        source_node_id = source_node_id_by_vessel[vessel_code]
        sink_node_id = sink_node_id_by_vessel[vessel_code]

        forward_reachable = {source_node_id}
        forward_stack = [source_node_id]
        while forward_stack:
            current_node_id = forward_stack.pop()
            for edge_id in base_outgoing_edge_ids_by_node.get(current_node_id, []):
                if not base_edge_allowed_for_vessel(vessel_code, base_edge_by_id[edge_id]):
                    continue
                next_node_id = base_edge_by_id[edge_id]["to_node_id"]
                if next_node_id in forward_reachable:
                    continue
                forward_reachable.add(next_node_id)
                forward_stack.append(next_node_id)

        backward_reachable = {sink_node_id}
        backward_stack = [sink_node_id]
        while backward_stack:
            current_node_id = backward_stack.pop()
            for edge_id in base_incoming_edge_ids_by_node.get(current_node_id, []):
                if not base_edge_allowed_for_vessel(vessel_code, base_edge_by_id[edge_id]):
                    continue
                prev_node_id = base_edge_by_id[edge_id]["from_node_id"]
                if prev_node_id in backward_reachable:
                    continue
                backward_reachable.add(prev_node_id)
                backward_stack.append(prev_node_id)

        relevant_node_ids = forward_reachable.intersection(backward_reachable)
        compatible_relevant_node_ids = set()
        compatible_lane_versions = compatible_lane_versions_by_vessel[vessel_code]
        for node_id in relevant_node_ids:
            owner = network.nodes[node_id].get("owner")
            if isinstance(owner, (D, R)) and owner.vessel_code != vessel_code:
                continue
            lane_version = _node_lane_version(network, node_id)
            if lane_version is not None and lane_version not in compatible_lane_versions:
                continue
            compatible_relevant_node_ids.add(node_id)
        relevant_node_ids = compatible_relevant_node_ids
        if source_node_id not in relevant_node_ids or sink_node_id not in relevant_node_ids:
            raise ValueError(
                "multicommodity_flow: no source-to-sink subgraph for vessel. "
                f"vessel_code={vessel_code!r}, source={source_node_id!r}, sink={sink_node_id!r}."
            )

        edge_ids: list[str] = []
        outgoing_by_node = {node_id: [] for node_id in relevant_node_ids}
        incoming_by_node = {node_id: [] for node_id in relevant_node_ids}
        for node_id in relevant_node_ids:
            for edge_id in base_outgoing_edge_ids_by_node.get(node_id, []):
                edge = base_edge_by_id[edge_id]
                if not base_edge_allowed_for_vessel(vessel_code, edge):
                    continue
                if edge["to_node_id"] not in relevant_node_ids:
                    continue
                edge_ids.append(edge_id)
                outgoing_by_node[node_id].append(edge_id)
                incoming_by_node[edge["to_node_id"]].append(edge_id)
        return vessel_code, relevant_node_ids, edge_ids, outgoing_by_node, incoming_by_node

    max_subgraph_workers = min(8, len(vessel_codes), os.cpu_count() or 1)
    with ThreadPoolExecutor(max_workers=max_subgraph_workers) as executor:
        for vessel_code, relevant_node_ids, edge_ids, outgoing_by_node, incoming_by_node in _progress(
            executor.map(build_reachable_subgraph, vessel_codes),
            total=len(vessel_codes),
            desc="reachable subgraphs",
        ):
            reachable_node_ids_by_vessel[vessel_code] = relevant_node_ids
            model_edge_ids_by_vessel[vessel_code] = edge_ids
            model_outgoing_edge_ids_by_vessel_node[vessel_code] = outgoing_by_node
            model_incoming_edge_ids_by_vessel_node[vessel_code] = incoming_by_node
            model_edge_id_set_by_vessel[vessel_code] = set(edge_ids)
            pruned_base_edge_count += len(edge_ids)

    for vessel_code in _progress(vessel_codes, total=len(vessel_codes), desc="wrap edges"):
        edge_id = f"wrap:{vessel_code}:{sink_node_id_by_vessel[vessel_code]}->{source_node_id_by_vessel[vessel_code]}"
        model_edge_by_id[edge_id] = {
            "edge_id": edge_id,
            "from_node_id": sink_node_id_by_vessel[vessel_code],
            "to_node_id": source_node_id_by_vessel[vessel_code],
            "arc_id": None,
            "arc_type": "WrapArc",
            "sail_cost_key": None,
            "canal_route_cost_key": None,
            "canal_route_fee_key": None,
            "service_cost_key": None,
            "canal_cost_key": None,
            "virtual_opportunity_cost": 0.0,
            "coverage_key": None,
            "to_is_target": False,
        }
        model_edge_ids_by_vessel[vessel_code].append(edge_id)
        model_edge_id_set_by_vessel[vessel_code].add(edge_id)
        wrap_edge_ids_by_vessel[vessel_code] = edge_id
        model_outgoing_edge_ids_by_vessel_node[vessel_code][sink_node_id_by_vessel[vessel_code]].append(edge_id)
        model_incoming_edge_ids_by_vessel_node[vessel_code][source_node_id_by_vessel[vessel_code]].append(edge_id)

    virtual_source_edge_count = 0
    virtual_target_edge_count = 0
    virtual_edge_ids: list[str] = []
    model_vessel_codes = list(vessel_codes)
    if include_virtual_vessel:
        virtual_source_node_id = f"virtual_source:{VIRTUAL_VESSEL_CODE}"
        source_node_id_by_vessel[VIRTUAL_VESSEL_CODE] = virtual_source_node_id
        sink_node_id_by_vessel[VIRTUAL_VESSEL_CODE] = target_node_id
        virtual_edge_ids = [
            edge_id
            for edge_id, edge in base_edge_by_id.items()
            if _is_v0_allowed_base_edge(network, edge["from_node_id"], edge["to_node_id"])
        ]
        virtual_node_ids = {virtual_source_node_id, target_node_id}
        virtual_outgoing_by_node: dict[str, list[str]] = defaultdict(list)
        virtual_incoming_by_node: dict[str, list[str]] = defaultdict(list)
        for edge_id in virtual_edge_ids:
            edge = base_edge_by_id[edge_id]
            from_node_id = edge["from_node_id"]
            to_node_id = edge["to_node_id"]
            virtual_node_ids.add(from_node_id)
            virtual_node_ids.add(to_node_id)
            virtual_outgoing_by_node[from_node_id].append(edge_id)
            virtual_incoming_by_node[to_node_id].append(edge_id)

        node_groups_by_id = {}
        for _, node_data in network.nodes(data=True):
            owner = node_data.get("owner")
            if isinstance(owner, NodeGroup):
                node_groups_by_id[owner.get_id()] = owner
        for owner in sorted(node_groups_by_id.values(), key=lambda item: item.get_id()):
            entry_nodes = owner.inbound_nodes()
            exit_nodes = [owner.pilot_out]
            exit_nodes.extend(owner.outbound_nodes())

            for node in {node.get_id(): node for node in entry_nodes}.values():
                edge_id = f"v0_source:{virtual_source_node_id}->{node.get_id()}"
                model_edge_by_id[edge_id] = {
                    "edge_id": edge_id,
                    "from_node_id": virtual_source_node_id,
                    "to_node_id": node.get_id(),
                    "arc_id": None,
                    "arc_type": "VirtualSourceArc",
                    "sail_cost_key": None,
                    "canal_route_cost_key": None,
                    "canal_route_fee_key": None,
                    "service_cost_key": None,
                    "canal_cost_key": None,
                    "virtual_opportunity_cost": 0.0,
                    "coverage_key": None,
                    "to_is_target": False,
                }
                virtual_edge_ids.append(edge_id)
                virtual_node_ids.add(node.get_id())
                virtual_outgoing_by_node[virtual_source_node_id].append(edge_id)
                virtual_incoming_by_node[node.get_id()].append(edge_id)
                virtual_source_edge_count += 1

            for node in {node.get_id(): node for node in exit_nodes}.values():
                edge_id = f"v0_target:{node.get_id()}->{target_node_id}"
                model_edge_by_id[edge_id] = {
                    "edge_id": edge_id,
                    "from_node_id": node.get_id(),
                    "to_node_id": target_node_id,
                    "arc_id": None,
                    "arc_type": "VirtualTargetArc",
                    "sail_cost_key": None,
                    "canal_route_cost_key": None,
                    "canal_route_fee_key": None,
                    "service_cost_key": None,
                    "canal_cost_key": None,
                    "virtual_opportunity_cost": 0.0,
                    "coverage_key": None,
                    "to_is_target": True,
                }
                virtual_edge_ids.append(edge_id)
                virtual_node_ids.add(node.get_id())
                virtual_outgoing_by_node[node.get_id()].append(edge_id)
                virtual_incoming_by_node[target_node_id].append(edge_id)
                virtual_target_edge_count += 1

        virtual_wrap_edge_id = f"wrap:{VIRTUAL_VESSEL_CODE}:{target_node_id}->{virtual_source_node_id}"
        model_edge_by_id[virtual_wrap_edge_id] = {
            "edge_id": virtual_wrap_edge_id,
            "from_node_id": target_node_id,
            "to_node_id": virtual_source_node_id,
            "arc_id": None,
            "arc_type": "WrapArc",
            "sail_cost_key": None,
            "canal_route_cost_key": None,
            "canal_route_fee_key": None,
            "service_cost_key": None,
            "canal_cost_key": None,
            "virtual_opportunity_cost": 0.0,
            "coverage_key": None,
            "to_is_target": False,
        }
        virtual_edge_ids.append(virtual_wrap_edge_id)
        wrap_edge_ids_by_vessel[VIRTUAL_VESSEL_CODE] = virtual_wrap_edge_id
        virtual_outgoing_by_node[target_node_id].append(virtual_wrap_edge_id)
        virtual_incoming_by_node[virtual_source_node_id].append(virtual_wrap_edge_id)
        model_edge_ids_by_vessel[VIRTUAL_VESSEL_CODE] = virtual_edge_ids
        model_edge_id_set_by_vessel[VIRTUAL_VESSEL_CODE] = set(virtual_edge_ids)
        model_outgoing_edge_ids_by_vessel_node[VIRTUAL_VESSEL_CODE] = virtual_outgoing_by_node
        model_incoming_edge_ids_by_vessel_node[VIRTUAL_VESSEL_CODE] = virtual_incoming_by_node
        reachable_node_ids_by_vessel[VIRTUAL_VESSEL_CODE] = virtual_node_ids
        model_vessel_codes.append(VIRTUAL_VESSEL_CODE)
    position_keys = sorted(network.graph["related_lane_keys"])
    position_key_set = set(position_keys)
    fixed_position_keys: set[tuple[str, str, int]] = set()
    output_declared_position_keys: set[tuple[str, str, int]] = set()
    position_keys_by_version: dict[tuple[str, str], list[tuple[str, str, int]]] = defaultdict(list)
    required_position_count_by_version: dict[tuple[str, str], int] = {}
    for lane in INSTANCE_DATA.service_lanes:
        lane_code = lane["lane_code"]
        for version in lane["versions"]:
            proforma_name = version["proforma_name"]
            version_key = (lane_code, proforma_name)
            declared_position_numbers = set(version.get("declared_positions") or [])
            available_position_numbers = set(version.get("available_positions") or [])
            candidate_position_numbers = set(declared_position_numbers or available_position_numbers)
            for assignment in version["vessel_assignments"]:
                assignment_key = (lane_code, proforma_name, assignment["position_no"])
                fixed_position_keys.add(assignment_key)
                candidate_position_numbers.add(assignment["position_no"])
            if declared_position_numbers:
                fixed_position_keys.update(
                    (lane_code, proforma_name, position_no) for position_no in declared_position_numbers
                )
                required_position_count_by_version[version_key] = len(declared_position_numbers)
            elif available_position_numbers:
                required_position_count_by_version[version_key] = int(version.get("own_vessel_count") or 0)
            for position_no in sorted(candidate_position_numbers):
                position_key = (lane_code, proforma_name, position_no)
                if position_key in position_key_set:
                    position_keys_by_version[version_key].append(position_key)
                    if position_no in available_position_numbers:
                        output_declared_position_keys.add(position_key)

    position_key_by_node_id: dict[str, tuple[str, str, int]] = {}
    for node_id, node_data in network.nodes(data=True):
        owner = node_data.get("owner")
        event = None
        if isinstance(owner, NodeGroup):
            event = owner.event
        elif isinstance(owner, HorizonSailNode):
            event = owner.event
        if isinstance(event, InLaneEvent):
            position_key_by_node_id[node_id] = (event.lane_code, event.proforma_name, event.position_no)

    edge_position_keys_by_id: dict[str, set[tuple[str, str, int]]] = {}
    for edge_id, edge in model_edge_by_id.items():
        touched_position_keys = {
            position_key_by_node_id[node_id]
            for node_id in (edge["from_node_id"], edge["to_node_id"])
            if node_id in position_key_by_node_id
        }
        if touched_position_keys:
            edge_position_keys_by_id[edge_id] = touched_position_keys
    log_stage(
        "prepared vessel-specific model edges "
        f"include_virtual_vessel={include_virtual_vessel} "
        f"wrap_edges={len(wrap_edge_ids_by_vessel)} "
        f"virtual_edges={len(virtual_edge_ids)} "
        f"virtual_source_edges={virtual_source_edge_count} "
        f"virtual_target_edges={virtual_target_edge_count} "
        f"pruned_base_edges={pruned_base_edge_count} "
        f"model_edges={sum(len(x) for x in model_edge_ids_by_vessel.values())}"
    )

    log_stage(
        f"total_sail_cost={total_sail_cost_all_vessels:.2f} "
        f"total_portstay_cost={total_portstay_cost_all_vessels:.2f} "
        f"total_canal_cost={total_canal_cost_all_vessels:.2f} "
        f"total_expected_opportunity_cost={total_expected_opportunity_cost:.2f} "
        f"total_ts_cost={sum(ts_unit['cost'] for ts_unit in ts_units):.2f}"
    )

    def dd_adjusted_sail_cost(edge_id: str, vessel_code: str, lane_code: str | None) -> float:
        edge = model_edge_by_id[edge_id]
        if edge["canal_route_cost_key"] is not None:
            cost_key = edge["canal_route_cost_key"]
            adjusted_cost_key = (lane_code, *cost_key[1:])
            cost_by_capacity = canal_route_cost_cache.get(adjusted_cost_key)
            if cost_by_capacity is None:
                cost_by_capacity = _canal_route_cost_by_capacity_for_key(adjusted_cost_key, unique_capacities)
                canal_route_cost_cache[adjusted_cost_key] = cost_by_capacity
            return float(cost_by_capacity[capacity_by_vessel[vessel_code]]) + _canal_arc_cost_for_vessel(
                vessel_code, *edge["canal_route_fee_key"]
            )
        cost_key = edge["sail_cost_key"]
        adjusted_cost_key = (lane_code, *cost_key[1:])
        cost_by_capacity = sail_cost_cache.get(adjusted_cost_key)
        if cost_by_capacity is None:
            cost_by_capacity = _sail_cost_by_capacity_for_key(adjusted_cost_key, unique_capacities)
            sail_cost_cache[adjusted_cost_key] = cost_by_capacity
        return float(cost_by_capacity[capacity_by_vessel[vessel_code]])

    dd_couplings = dd_couplings or []
    dd_cost_infos = []
    dd_skip_sail_cost_keys: set[tuple[str, str]] = set()
    dd_out_sail_cost_by_key: dict[tuple[str, str], float] = {}
    for coupling_index, coupling in enumerate(dd_couplings):
        before_vessel_code = str(coupling["before_vessel_code"])
        after_vessel_code = str(coupling["after_vessel_code"])
        if before_vessel_code not in model_edge_ids_by_vessel or after_vessel_code not in model_edge_ids_by_vessel:
            continue
        before_sink_node_id = sink_node_id_by_vessel[before_vessel_code]
        after_source_node_id = source_node_id_by_vessel[after_vessel_code]
        dd_in_sail_edge_ids = [
            edge_id
            for edge_id in model_incoming_edge_ids_by_vessel_node[before_vessel_code].get(before_sink_node_id, [])
            if model_edge_by_id[edge_id]["arc_type"] in {"SailArc", "HorizonSailArc", "CanalSailArc"}
        ]
        out_edge_ids_by_lane_code: dict[str | None, list[str]] = defaultdict(list)
        for edge_id in model_outgoing_edge_ids_by_vessel_node[after_vessel_code].get(after_source_node_id, []):
            edge = model_edge_by_id[edge_id]
            out_edge_ids_by_lane_code[_node_arrival_lane_code(network, edge["to_node_id"])].append(edge_id)
            if edge["arc_type"] not in {"SailArc", "HorizonSailArc", "CanalSailArc"}:
                continue
            dd_skip_sail_cost_keys.add((after_vessel_code, edge_id))
            dd_out_sail_cost_by_key[(after_vessel_code, edge_id)] = dd_adjusted_sail_cost(
                edge_id,
                after_vessel_code,
                _node_arrival_lane_code(network, edge["to_node_id"]),
            )
        for edge_id in dd_in_sail_edge_ids:
            dd_skip_sail_cost_keys.add((before_vessel_code, edge_id))
        dd_cost_infos.append(
            {
                "coupling_index": coupling_index,
                "original_vessel_code": coupling["original_vessel_code"],
                "before_vessel_code": before_vessel_code,
                "after_vessel_code": after_vessel_code,
                "dd_in_sail_edge_ids": dd_in_sail_edge_ids,
                "out_edge_ids_by_lane_code": dict(out_edge_ids_by_lane_code),
            }
        )
    log_stage(
        "prepared dry-dock cost couplings "
        f"couplings={len(dd_cost_infos)} "
        f"in_sail_edges={sum(len(info['dd_in_sail_edge_ids']) for info in dd_cost_infos)} "
        f"out_categories={sum(len(info['out_edge_ids_by_lane_code']) for info in dd_cost_infos)}"
    )

    canal_context_infos = []
    for edge_id, edge in base_edge_by_id.items():
        service_cost_key = edge["service_cost_key"]
        if service_cost_key is None:
            continue
        canal_cost_key = _service_canal_cost_key(service_cost_key)
        if canal_cost_key is None:
            continue
        service_owner = _service_owner_for_edge(network, edge["from_node_id"], edge["to_node_id"])
        if service_owner is None:
            continue
        previous_edge_ids = [
            previous_edge_id
            for previous_edge_id in base_incoming_edge_ids_by_node.get(edge["from_node_id"], [])
            if _is_adjacent_inlane_sail_for_canal(
                network,
                base_edge_by_id[previous_edge_id]["from_node_id"],
                base_edge_by_id[previous_edge_id]["to_node_id"],
                service_owner,
                "previous",
                base_edge_by_id[previous_edge_id]["arc_type"],
            )
        ]
        next_edge_ids = [
            next_edge_id
            for next_edge_id in base_outgoing_edge_ids_by_node.get(edge["to_node_id"], [])
            if _is_adjacent_inlane_sail_for_canal(
                network,
                base_edge_by_id[next_edge_id]["from_node_id"],
                base_edge_by_id[next_edge_id]["to_node_id"],
                service_owner,
                "next",
                base_edge_by_id[next_edge_id]["arc_type"],
            )
        ]
        if not previous_edge_ids or not next_edge_ids:
            continue
        canal_context_infos.append(
            {
                "service_edge_id": edge_id,
                "previous_edge_ids": previous_edge_ids,
                "next_edge_ids": next_edge_ids,
                "canal_cost_key": canal_cost_key,
            }
        )
        edge["canal_cost_key"] = canal_cost_key
        total_canal_cost_all_vessels += sum(
            _canal_arc_cost_for_vessel(vessel_code, *canal_cost_key) for vessel_code in vessel_codes
        )
    log_stage(
        "prepared canal fee contexts "
        f"contexts={len(canal_context_infos)} "
        f"previous_edges={sum(len(info['previous_edge_ids']) for info in canal_context_infos)} "
        f"next_edges={sum(len(info['next_edge_ids']) for info in canal_context_infos)}"
    )

    model = gp.Model(model_name)
    model.Params.OutputFlag = 1
    model.Params.LogToConsole = 1
    model.Params.Threads = 12
    log_stage(f"created gurobi model name={model_name}")

    x_keys = [
        (vessel_code, edge_id)
        for vessel_code in model_vessel_codes
        for edge_id in model_edge_ids_by_vessel[vessel_code]
    ]
    x_key_counts = Counter(x_keys)
    duplicate_x_keys = [key for key, count in x_key_counts.items() if count > 1]
    if duplicate_x_keys:
        duplicate_vessel_codes = [vessel_code for vessel_code, count in Counter(vessel_codes).items() if count > 1]
        _progress_write(f"Duplicate x_keys before Model.addVars(): total={len(duplicate_x_keys)}")
        if duplicate_vessel_codes:
            _progress_write(f"Duplicate vessel_codes: {duplicate_vessel_codes}")
        _progress_write("First duplicate x_keys:")
        for vessel_code, edge_id in duplicate_x_keys[:20]:
            edge = model_edge_by_id[edge_id]
            _progress_write(
                f"- vessel={vessel_code}, edge_id={edge_id}, "
                f"count={x_key_counts[(vessel_code, edge_id)]}, "
                f"from={edge['from_node_id']}, to={edge['to_node_id']}, "
                f"arc_id={edge['arc_id']}, arc_type={edge['arc_type']}"
            )
        raise ValueError("multicommodity_flow: duplicate x_keys detected before Model.addVars().")

    x = {}
    for vessel_code, edge_id in _progress(x_keys, total=len(x_keys), desc="x variables"):
        if vessel_code == VIRTUAL_VESSEL_CODE and edge_id == wrap_edge_ids_by_vessel[VIRTUAL_VESSEL_CODE]:
            x[vessel_code, edge_id] = model.addVar(lb=0.0, vtype=GRB.CONTINUOUS, name=f"x[{vessel_code},{edge_id}]")
        else:
            x[vessel_code, edge_id] = model.addVar(vtype=GRB.BINARY, name=f"x[{vessel_code},{edge_id}]")
    y = model.addVars([ts_unit["unit_id"] for ts_unit in ts_units], vtype=GRB.BINARY, name="y")
    position_active = {}
    for lane_code, proforma_name, position_no in position_keys:
        position_key = (lane_code, proforma_name, position_no)
        lower_bound = 1.0 if position_key in fixed_position_keys else 0.0
        position_active[position_key] = model.addVar(
            lb=lower_bound,
            ub=1.0,
            vtype=GRB.BINARY,
            name=f"p[{lane_code},{proforma_name},{position_no}]",
        )
    dd_category_active = {}
    dd_in_cost_active = {}
    for info in dd_cost_infos:
        coupling_index = info["coupling_index"]
        for lane_code in info["out_edge_ids_by_lane_code"]:
            lane_label = lane_code if lane_code is not None else "avg"
            dd_category_active[coupling_index, lane_code] = model.addVar(
                vtype=GRB.BINARY,
                name=f"dd_q[{coupling_index},{lane_label}]",
            )
            for edge_id in info["dd_in_sail_edge_ids"]:
                dd_in_cost_active[coupling_index, edge_id, lane_code] = model.addVar(
                    vtype=GRB.BINARY,
                    name=f"dd_w[{coupling_index},{edge_id},{lane_label}]",
                )
    log_stage(
        f"created variables x={len(x_keys)} y={len(ts_units)} p={len(position_active)} "
        f"dd_q={len(dd_category_active)} dd_w={len(dd_in_cost_active)}"
    )

    sail_edge_id_set = set(sail_edge_ids)
    canal_route_edge_id_set = set(canal_route_edge_ids)
    objective_coeffs = []
    objective_vars = []

    for vessel_code in _progress(model_vessel_codes, total=len(model_vessel_codes), desc="objective x"):
        for edge_id in model_edge_ids_by_vessel[vessel_code]:
            coeff = 0.0
            if vessel_code == VIRTUAL_VESSEL_CODE:
                coeff += _edge_cost_from_model_edge(
                    model_edge_by_id[edge_id],
                    vessel_code,
                    capacity_by_vessel,
                    sail_cost_cache,
                    canal_route_cost_cache,
                    service_cost_cache,
                )
            elif (
                edge_id in sail_edge_id_set
                or edge_id in canal_route_edge_id_set
                or model_edge_by_id[edge_id]["service_cost_key"] is not None
                or model_edge_by_id[edge_id]["canal_cost_key"] is not None
            ):
                edge = model_edge_by_id[edge_id]
                if (vessel_code, edge_id) not in dd_skip_sail_cost_keys:
                    coeff += _edge_cost_from_model_edge(
                        edge,
                        vessel_code,
                        capacity_by_vessel,
                        sail_cost_cache,
                        canal_route_cost_cache,
                        service_cost_cache,
                    )
                else:
                    if edge["service_cost_key"] is not None:
                        coeff += float(service_cost_cache[edge["service_cost_key"]][capacity_by_vessel[vessel_code]])
                    if edge["canal_cost_key"] is not None:
                        coeff += _canal_arc_cost_for_vessel(vessel_code, *edge["canal_cost_key"])
                    coeff += dd_out_sail_cost_by_key.get((vessel_code, edge_id), 0.0)
            if coeff == 0.0:
                continue
            objective_coeffs.append(coeff)
            objective_vars.append(x[vessel_code, edge_id])
    for info in _progress(dd_cost_infos, total=len(dd_cost_infos), desc="objective dry-dock"):
        before_vessel_code = info["before_vessel_code"]
        coupling_index = info["coupling_index"]
        for edge_id in info["dd_in_sail_edge_ids"]:
            for lane_code in info["out_edge_ids_by_lane_code"]:
                coeff = dd_adjusted_sail_cost(edge_id, before_vessel_code, lane_code)
                if coeff == 0.0:
                    continue
                objective_coeffs.append(coeff)
                objective_vars.append(dd_in_cost_active[coupling_index, edge_id, lane_code])
    for ts_unit in _progress(ts_units, total=len(ts_units), desc="objective ts"):
        objective_coeffs.append(ts_unit["cost"])
        objective_vars.append(y[ts_unit["unit_id"]])
    # objective_coeffs = [x / 100000.0 for x in objective_coeffs]
    model.setObjective(gp.LinExpr(objective_coeffs, objective_vars), GRB.MINIMIZE)
    log_stage(f"set objective nonzero_terms={len(objective_coeffs)}")

    source_constraint_count = 0
    for vessel_code in _progress(vessel_codes, total=len(vessel_codes), desc="source constraints"):
        source_node_id = source_node_id_by_vessel[vessel_code]
        outgoing_vars = [
            x[vessel_code, edge_id] for edge_id in model_outgoing_edge_ids_by_vessel_node[vessel_code][source_node_id]
        ]
        model.addLConstr(
            gp.LinExpr([1.0] * len(outgoing_vars), outgoing_vars), GRB.EQUAL, 1.0, name=f"source_out[{vessel_code}]"
        )
        source_constraint_count += 1
    log_stage(f"added source constraints count={source_constraint_count}")

    dd_cost_constraint_count = 0
    for info in _progress(dd_cost_infos, total=len(dd_cost_infos), desc="dry-dock cost constraints"):
        coupling_index = info["coupling_index"]
        after_vessel_code = info["after_vessel_code"]
        q_vars = []
        for lane_code, out_edge_ids in info["out_edge_ids_by_lane_code"].items():
            q_var = dd_category_active[coupling_index, lane_code]
            q_vars.append(q_var)
            category_expr = gp.LinExpr([1.0], [q_var])
            category_expr.addTerms(
                [-1.0] * len(out_edge_ids),
                [x[after_vessel_code, edge_id] for edge_id in out_edge_ids],
            )
            model.addLConstr(
                category_expr,
                GRB.EQUAL,
                0.0,
                name=f"dd_out_category[{coupling_index},{lane_code if lane_code is not None else 'avg'}]",
            )
            dd_cost_constraint_count += 1
        if q_vars:
            model.addLConstr(
                gp.LinExpr([1.0] * len(q_vars), q_vars),
                GRB.EQUAL,
                1.0,
                name=f"dd_out_category_one[{coupling_index}]",
            )
            dd_cost_constraint_count += 1
        before_vessel_code = info["before_vessel_code"]
        for edge_id in info["dd_in_sail_edge_ids"]:
            x_var = x[before_vessel_code, edge_id]
            for lane_code in info["out_edge_ids_by_lane_code"]:
                q_var = dd_category_active[coupling_index, lane_code]
                w_var = dd_in_cost_active[coupling_index, edge_id, lane_code]
                model.addLConstr(
                    gp.LinExpr([1.0, -1.0], [w_var, x_var]),
                    GRB.LESS_EQUAL,
                    0.0,
                    name=f"dd_w_le_x[{coupling_index},{edge_id},{lane_code if lane_code is not None else 'avg'}]",
                )
                model.addLConstr(
                    gp.LinExpr([1.0, -1.0], [w_var, q_var]),
                    GRB.LESS_EQUAL,
                    0.0,
                    name=f"dd_w_le_q[{coupling_index},{edge_id},{lane_code if lane_code is not None else 'avg'}]",
                )
                model.addLConstr(
                    gp.LinExpr([1.0, -1.0, -1.0], [w_var, x_var, q_var]),
                    GRB.GREATER_EQUAL,
                    -1.0,
                    name=f"dd_w_ge_x_plus_q_minus_1[{coupling_index},{edge_id},{lane_code if lane_code is not None else 'avg'}]",
                )
                dd_cost_constraint_count += 3
    log_stage(f"added dry-dock cost constraints count={dd_cost_constraint_count}")

    flow_constraint_count = 0
    for vessel_code in _progress(model_vessel_codes, total=len(model_vessel_codes), desc="flow constraints"):
        for node_id in reachable_node_ids_by_vessel[vessel_code]:
            incoming_vars = [
                x[vessel_code, edge_id]
                for edge_id in model_incoming_edge_ids_by_vessel_node[vessel_code].get(node_id, [])
            ]
            outgoing_vars = [
                x[vessel_code, edge_id]
                for edge_id in model_outgoing_edge_ids_by_vessel_node[vessel_code].get(node_id, [])
            ]
            flow_expr = gp.LinExpr()
            if incoming_vars:
                flow_expr.addTerms([1.0] * len(incoming_vars), incoming_vars)
            if outgoing_vars:
                flow_expr.addTerms([-1.0] * len(outgoing_vars), outgoing_vars)
            model.addLConstr(flow_expr, GRB.EQUAL, 0.0, name=f"flow[{vessel_code},{node_id}]")
            flow_constraint_count += 1
    log_stage(f"added flow conservation constraints count={flow_constraint_count}")

    service_cover_constraint_count = 0
    service_edge_ids = [edge_id for edge_id, edge in base_edge_by_id.items() if edge["service_cost_key"] is not None]
    for edge_id in _progress(service_edge_ids, total=len(service_edge_ids), desc="service cover constraints"):
        service_owner = _service_owner_for_edge(
            network,
            base_edge_by_id[edge_id]["from_node_id"],
            base_edge_by_id[edge_id]["to_node_id"],
        )
        service_position_key = (
            service_owner.event.lane_code,
            service_owner.event.proforma_name,
            service_owner.event.position_no,
        )
        coverage_vars = [
            x[vessel_code, edge_id]
            for vessel_code in model_vessel_codes
            if edge_id in model_edge_id_set_by_vessel[vessel_code]
        ]
        if not coverage_vars:
            raise ValueError(f"multicommodity_flow: no service coverage variable for edge {edge_id!r}.")
        cover_expr = gp.LinExpr([1.0] * len(coverage_vars), coverage_vars)
        cover_expr.addTerms([-1.0], [position_active[service_position_key]])
        model.addLConstr(
            cover_expr,
            GRB.EQUAL,
            0.0,
            name=f"service_cover[{service_cover_constraint_count}]",
        )
        service_cover_constraint_count += 1
    log_stage(f"added service cover constraints count={service_cover_constraint_count}")

    position_count_constraint_count = 0
    for version_key, version_position_keys in sorted(position_keys_by_version.items()):
        required_count = required_position_count_by_version.get(version_key)
        if required_count is None:
            continue
        position_vars = [position_active[position_key] for position_key in version_position_keys]
        model.addLConstr(
            gp.LinExpr([1.0] * len(position_vars), position_vars),
            GRB.EQUAL,
            float(required_count),
            name=f"position_count[{version_key[0]},{version_key[1]}]",
        )
        position_count_constraint_count += 1
    log_stage(f"added position count constraints count={position_count_constraint_count}")

    position_edge_activation_constraint_count = 0
    if AGGREGATE_POSITION_ACTIVATION_CONSTRAINTS:
        position_activation_vars_by_key: dict[tuple[str, str, int], list[Any]] = defaultdict(list)
        for edge_id, touched_position_keys in _progress(
            edge_position_keys_by_id.items(),
            total=len(edge_position_keys_by_id),
            desc="position activation aggregation",
        ):
            edge_vars = [
                x[vessel_code, edge_id]
                for vessel_code in model_vessel_codes
                if edge_id in model_edge_id_set_by_vessel[vessel_code]
            ]
            if not edge_vars:
                continue
            for position_key in touched_position_keys:
                position_activation_vars_by_key[position_key].extend(edge_vars)
        for position_key, activation_vars in _progress(
            sorted(position_activation_vars_by_key.items()),
            total=len(position_activation_vars_by_key),
            desc="position activation constraints",
        ):
            activation_expr = gp.LinExpr([1.0] * len(activation_vars), activation_vars)
            activation_expr.addTerms([-float(len(activation_vars))], [position_active[position_key]])
            model.addLConstr(
                activation_expr,
                GRB.LESS_EQUAL,
                0.0,
                name=("position_edge_activation" f"[{position_key[0]},{position_key[1]},{position_key[2]}]"),
            )
            position_edge_activation_constraint_count += 1
    else:
        for edge_id, touched_position_keys in _progress(
            edge_position_keys_by_id.items(),
            total=len(edge_position_keys_by_id),
            desc="position activation constraints",
        ):
            edge_vars = [
                x[vessel_code, edge_id]
                for vessel_code in model_vessel_codes
                if edge_id in model_edge_id_set_by_vessel[vessel_code]
            ]
            if not edge_vars:
                continue
            for position_key in touched_position_keys:
                activation_expr = gp.LinExpr([1.0] * len(edge_vars), edge_vars)
                activation_expr.addTerms([-1.0], [position_active[position_key]])
                model.addLConstr(
                    activation_expr,
                    GRB.LESS_EQUAL,
                    0.0,
                    name=(
                        "position_edge_activation"
                        f"[{position_key[0]},{position_key[1]},{position_key[2]},{position_edge_activation_constraint_count}]"
                    ),
                )
                position_edge_activation_constraint_count += 1
    log_stage(
        "added position activation constraints "
        f"aggregated={AGGREGATE_POSITION_ACTIVATION_CONSTRAINTS} "
        f"count={position_edge_activation_constraint_count}"
    )

    wrap_edge_id_set = set(wrap_edge_ids_by_vessel.values())
    arc_capacity_constraint_count = 0
    for edge_id in _progress(model_edge_by_id, total=len(model_edge_by_id), desc="arc capacity constraints"):
        if edge_id in wrap_edge_id_set:
            continue
        edge = model_edge_by_id[edge_id]
        if edge["from_node_id"] == idle_node_id and edge["to_node_id"] == target_node_id:
            continue
        capacity_vars = [
            x[vessel_code, edge_id]
            for vessel_code in model_vessel_codes
            if edge_id in model_edge_id_set_by_vessel[vessel_code]
        ]
        if len(capacity_vars) <= 1:
            continue
        model.addLConstr(
            gp.LinExpr([1.0] * len(capacity_vars), capacity_vars),
            GRB.LESS_EQUAL,
            1.0,
            name=f"arc_capacity[{arc_capacity_constraint_count}]",
        )
        arc_capacity_constraint_count += 1
    log_stage(f"added arc capacity constraints count={arc_capacity_constraint_count}")

    ts_constraint_count = 0
    for ts_unit in _progress(ts_units, total=len(ts_units), desc="ts constraints"):
        incoming_vars = [
            x[vessel_code, edge_id]
            for vessel_code in model_vessel_codes
            for edge_id in model_incoming_edge_ids_by_vessel_node[vessel_code].get(ts_unit["tsi_node_id"], [])
            if edge_id in model_edge_id_set_by_vessel[vessel_code]
        ]
        outgoing_vars = [
            x[vessel_code, edge_id]
            for vessel_code in model_vessel_codes
            for edge_id in model_outgoing_edge_ids_by_vessel_node[vessel_code].get(ts_unit["tso_node_id"], [])
            if edge_id in model_edge_id_set_by_vessel[vessel_code]
        ]
        activation_expr = gp.LinExpr([2.0], [y[ts_unit["unit_id"]]])
        if incoming_vars:
            activation_expr.addTerms([-1.0] * len(incoming_vars), incoming_vars)
        if outgoing_vars:
            activation_expr.addTerms([-1.0] * len(outgoing_vars), outgoing_vars)
        model.addLConstr(activation_expr, GRB.GREATER_EQUAL, 0.0, name=f"ts_activation[{ts_unit['unit_id']}]")
        ts_constraint_count += 1

        balance_expr = gp.LinExpr()
        if outgoing_vars:
            balance_expr.addTerms([1.0] * len(outgoing_vars), outgoing_vars)
        if incoming_vars:
            balance_expr.addTerms([-1.0] * len(incoming_vars), incoming_vars)
        model.addLConstr(balance_expr, GRB.EQUAL, 0.0, name=f"ts_balance[{ts_unit['unit_id']}]")
        ts_constraint_count += 1
    log_stage(f"added ts constraints count={ts_constraint_count}")

    # model_network_output_path = output_path.replace("_mip_paths.html", "_model_network.html")
    # viz_model_network(network, output_path=model_network_output_path, interactive=True)
    # _progress_write(f"Model network visualization written to {model_network_output_path}")

    model.setParam("MIPGap", 0)
    model.update()
    _progress_write(
        "[multicommodity_flow] optimize begin | "
        f"vars={model.NumVars} constrs={model.NumConstrs} "
        f"is_mip={model.IsMIP} binaries={model.NumBinVars} integers={model.NumIntVars}"
    )
    print(model.IsMIP, model.NumBinVars, model.NumIntVars)
    model.optimize()
    log_stage(f"optimize finished status={model.Status} solcount={model.SolCount}")
    status_name = {
        GRB.OPTIMAL: "OPTIMAL",
        GRB.INFEASIBLE: "INFEASIBLE",
        GRB.INF_OR_UNBD: "INF_OR_UNBD",
        GRB.UNBOUNDED: "UNBOUNDED",
        GRB.TIME_LIMIT: "TIME_LIMIT",
        GRB.INTERRUPTED: "INTERRUPTED",
        GRB.SUBOPTIMAL: "SUBOPTIMAL",
    }.get(model.Status, str(model.Status))
    if model.Status in {GRB.INFEASIBLE, GRB.INF_OR_UNBD}:
        model.dispose()
        gp.disposeDefaultEnv()
        log_stage("disposed gurobi model")
        raise NoResultError(
            "mcf_v6_lb did not produce a lower-bound result "
            f"because the optimization status is {status_name} ({model.Status})."
        )
    if model.SolCount > 0:
        declared_positions = [
            DeclaredPosition(
                lane_code=lane_code,
                proforma_name=proforma_name,
                declared_position_no=position_no,
            )
            for lane_code, proforma_name, position_no in position_keys
            if (lane_code, proforma_name, position_no) in output_declared_position_keys
            and float(position_active[lane_code, proforma_name, position_no].X) > 0.5
        ]
        log_stage(f"collected active positions count={len(declared_positions)}")
        solution_pickle_path = Path(output_path.replace("_mip_paths.html", "_solution_values.pkl"))
        solution_pickle_path.parent.mkdir(parents=True, exist_ok=True)
        log_stage(
            f"collect solution values for pickle begin x_vars={len(x)} y_vars={len(y)} path={solution_pickle_path}"
        )

        x_nonzero = {}
        for vessel_edge_key, var in _progress(x.items(), total=len(x), desc="pickle x values"):
            value = float(var.X)
            if abs(value) > 1e-9:
                x_nonzero[vessel_edge_key] = value
        log_stage(f"collected nonzero x values count={len(x_nonzero)}")

        y_values = {}
        for unit_id, var in _progress(y.items(), total=len(y), desc="pickle y values"):
            y_values[unit_id] = float(var.X)
        log_stage(f"collected y values count={len(y_values)}")

        solution_values = {
            "model_name": model_name,
            "status": int(model.Status),
            "status_name": status_name,
            "sol_count": int(model.SolCount),
            "objective_value": float(model.ObjVal),
            "objective_bound": float(model.ObjBound),
            "mip_gap": float(model.MIPGap),
            "vessel_codes": list(vessel_codes),
            "model_vessel_codes": list(model_vessel_codes),
            "source_node_id_by_vessel": source_node_id_by_vessel,
            "sink_node_id_by_vessel": sink_node_id_by_vessel,
            "base_edge_by_id": base_edge_by_id,
            "model_edge_ids_by_vessel": model_edge_ids_by_vessel,
            "wrap_edge_ids_by_vessel": wrap_edge_ids_by_vessel,
            "ts_units": ts_units,
            "declared_positions": declared_positions,
            "position_active_values": {
                position_key: float(var.X) for position_key, var in position_active.items() if float(var.X) > 1e-9
            },
            "x_nonzero": x_nonzero,
            "y_values": y_values,
        }
        log_stage(f"write solution pickle begin path={solution_pickle_path}")
        with solution_pickle_path.open("wb") as handle:
            pickle.dump(solution_values, handle, protocol=pickle.HIGHEST_PROTOCOL)
        _progress_write(f"Solution values written to {solution_pickle_path}")
        log_stage(f"saved solution values path={solution_pickle_path}")
    else:
        log_stage("skipped solution value pickle because no solution exists")
    if model.Status != GRB.OPTIMAL:
        model.dispose()
        gp.disposeDefaultEnv()
        log_stage("disposed gurobi model")
        raise ValueError(f"multicommodity_flow: optimization did not finish optimally. status={model.Status}.")
    declared_positions = [
        DeclaredPosition(
            lane_code=lane_code,
            proforma_name=proforma_name,
            declared_position_no=position_no,
        )
        for lane_code, proforma_name, position_no in position_keys
        if (lane_code, proforma_name, position_no) in output_declared_position_keys
        and float(position_active[lane_code, proforma_name, position_no].X) > 0.5
    ]

    activated_ts_units = [ts_unit for ts_unit in ts_units if y[ts_unit["unit_id"]].X > 0.5]
    if activated_ts_units:
        _progress_write("Activated TS units from y_k:")
        for ts_unit in activated_ts_units:
            _progress_write(
                f"- unit_id={ts_unit['unit_id']}, "
                f"port={ts_unit['port_code']}, "
                f"lane={ts_unit['lane_code']}, "
                f"proforma={ts_unit['proforma_name']}, "
                f"position={ts_unit['position_no']}"
            )
    else:
        _progress_write("Activated TS units from y_k: none")

    ts_state_by_unit_id = {
        ts_unit["unit_id"]: {
            "y": y[ts_unit["unit_id"]].X > 0.5,
            "s": False,
        }
        for ts_unit in ts_units
    }
    log_stage(f"prepared ts state active={len(activated_ts_units)}")

    selected_edge_ids_by_vessel: dict[str, set[str]] = {vessel_code: set() for vessel_code in model_vessel_codes}
    selected_wrap_edge_ids_by_vessel: dict[str, set[str]] = {vessel_code: set() for vessel_code in model_vessel_codes}
    flow_value_by_vessel_edge: dict[str, dict[str, float]] = {vessel_code: {} for vessel_code in model_vessel_codes}
    for vessel_code in _progress(model_vessel_codes, total=len(model_vessel_codes), desc="selected edges"):
        for edge_id in model_edge_ids_by_vessel[vessel_code]:
            flow_value = float(x[vessel_code, edge_id].X)
            if flow_value <= 1e-9:
                continue
            if edge_id.startswith("wrap:"):
                if flow_value > 0.5:
                    selected_wrap_edge_ids_by_vessel[vessel_code].add(edge_id)
            else:
                flow_value_by_vessel_edge[vessel_code][edge_id] = flow_value
                if flow_value > 0.5:
                    selected_edge_ids_by_vessel[vessel_code].add(edge_id)
    log_stage(
        "collected selected edges "
        f"selected={sum(len(edges) for edges in selected_edge_ids_by_vessel.values())} "
        f"nonzero={sum(len(edges) for edges in flow_value_by_vessel_edge.values())} "
        f"wrap={sum(len(edges) for edges in selected_wrap_edge_ids_by_vessel.values())}"
    )

    if any(flow_value_by_vessel_edge.values()):
        _visualize_selected_paths(
            network,
            selected_edge_ids_by_vessel,
            base_edge_by_id,
            ts_state_by_unit_id,
            flow_value_by_vessel_edge=flow_value_by_vessel_edge,
            output_path=output_path,
        )
        log_stage(f"wrote visualization path={output_path}")
    else:
        log_stage("skipped visualization because no nonzero flow exists")

    paths = [
        _build_selected_path(
            vessel_code=vessel_code,
            source_node_id=source_node_id_by_vessel[vessel_code],
            sink_node_id=sink_node_id_by_vessel[vessel_code],
            model_edge_by_id=model_edge_by_id,
            selected_edge_ids_by_vessel=selected_edge_ids_by_vessel,
            capacity_by_vessel=capacity_by_vessel,
            sail_cost_cache=sail_cost_cache,
            canal_route_cost_cache=canal_route_cost_cache,
            service_cost_cache=service_cost_cache,
        )
        for vessel_code in vessel_codes
    ]
    if include_virtual_vessel:
        virtual_paths = _build_virtual_selected_paths(
            virtual_source_node_id=source_node_id_by_vessel[VIRTUAL_VESSEL_CODE],
            target_node_id=target_node_id,
            network=network,
            model_edge_by_id=model_edge_by_id,
            selected_edge_ids=selected_edge_ids_by_vessel[VIRTUAL_VESSEL_CODE],
        )
        paths.extend(virtual_paths)
    log_stage(f"built selected paths count={len(paths)}")

    result = {
        "model_name": model_name,
        "status": int(model.Status),
        "status_name": status_name,
        "sol_count": int(model.SolCount),
        "objective_value": float(model.ObjVal),
        "objective_bound": float(model.ObjBound),
        "mip_gap": float(model.MIPGap),
        "paths": paths,
        "network": network,
        "base_edge_by_id": base_edge_by_id,
        "source_node_id_by_vessel": source_node_id_by_vessel,
        "sink_node_id_by_vessel": sink_node_id_by_vessel,
        "selected_edge_ids_by_vessel": selected_edge_ids_by_vessel,
        "selected_wrap_edge_ids_by_vessel": selected_wrap_edge_ids_by_vessel,
        "flow_value_by_vessel_edge": flow_value_by_vessel_edge,
        "ts_state_by_unit_id": ts_state_by_unit_id,
        "ts_units": ts_units,
        "declared_positions": declared_positions,
    }
    model.dispose()
    gp.disposeDefaultEnv()
    log_stage("disposed gurobi model")
    log_stage("finished")
    return result


def local_search(
    current_solution: CascadingSolution,
    destroy_batch: list[str],
) -> CascadingSolution:
    global LAST_DESTROY_BATCH_FLOWS, LAST_MULTICOMMODITY_RESULT

    if INSTANCE_DATA is None:
        raise ValueError("local_search: initialize_local_search() must be called first.")

    network = construct_network_cpp(INSTANCE_DATA, current_solution.declared_positions)

    if not nx.is_directed_acyclic_graph(network):
        raise ValueError("local_search: the constructed network is not a DAG.")

    LAST_MULTICOMMODITY_RESULT = multicommodity_flow(network, destroy_batch)
    print("1")
    LAST_DESTROY_BATCH_FLOWS = list(LAST_MULTICOMMODITY_RESULT["paths"])
    print("2")
    exit()

    return current_solution
