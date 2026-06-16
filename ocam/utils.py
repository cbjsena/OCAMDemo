from __future__ import annotations

from heapq import heappop, heappush

from ocam.models import *
from datetime import datetime, timedelta

INSTANCE_DATA: InstanceData | None = None
DISTANCE_MATRIX: dict[tuple[str, str], dict[str, int | float]] | None = None
DISTANCE_ADJACENCY: dict[str, list[tuple[str, int | float]]] | None = None
TRANSSHIPMENT_COST_BY_KEY: dict[tuple[str, str, str], int | float] | None = None


def init_utils(instance_data: InstanceData) -> None:
    global INSTANCE_DATA, DISTANCE_MATRIX, DISTANCE_ADJACENCY, TRANSSHIPMENT_COST_BY_KEY

    INSTANCE_DATA = instance_data

    DISTANCE_MATRIX = {
        (row["from_port_code"], row["to_port_code"]): {
            "distance": row["distance"],
            "eca_distance": row["eca_distance"],
        }
        for row in instance_data.distances
    }

    DISTANCE_ADJACENCY = {}
    for (from_port_code, to_port_code), distance_info in DISTANCE_MATRIX.items():
        DISTANCE_ADJACENCY.setdefault(from_port_code, []).append((to_port_code, distance_info["distance"]))

    TRANSSHIPMENT_COST_BY_KEY = {}
    for row in instance_data.transshipment_cost:
        year_month = row["year_month"]
        lane_code = row["lane_code"]
        for port in row["ports"]:
            TRANSSHIPMENT_COST_BY_KEY[(year_month, lane_code, port["port_code"])] = port["ts_cost"]


def lookup_version(lane_code: str, proforma_name: str) -> dict:
    if INSTANCE_DATA is None:
        raise ValueError("lookup_version: utils are not initialized.")

    for lane in INSTANCE_DATA.service_lanes:
        if lane["lane_code"] != lane_code:
            continue
        for version in lane["versions"]:
            if version["proforma_name"] == proforma_name:
                return version

    raise ValueError("lookup_version: could not find " f"lane_code={lane_code!r}, proforma_name={proforma_name!r}.")


def lookup_vessel(vessel_code: str) -> dict:
    if INSTANCE_DATA is None:
        raise ValueError("lookup_vessel: utils are not initialized.")

    vessel = next((v for v in INSTANCE_DATA.vessels if v["vessel_code"] == vessel_code), None)
    if vessel is None:
        raise ValueError(f"lookup_vessel: Vessel code {vessel_code} not found in instance data.")

    return vessel


def get_service_end_datetime(lane_code: str, proforma_name: str, position_no: int) -> datetime:
    if INSTANCE_DATA is None:
        raise ValueError("get_service_end_datetime: utils are not initialized.")

    for lane in INSTANCE_DATA.service_lanes:
        if lane["lane_code"] != lane_code:
            continue
        for version in lane["versions"]:
            if version["proforma_name"] != proforma_name:
                continue

            effective_to = version["effective_to"]
            if effective_to is None:
                return INSTANCE_DATA.planning_horizon["end"]

            service_duration = version["service_duration"]
            anchor_date = version["anchor_date"]
            position_start_date = anchor_date + timedelta(days=7 * (position_no - 1))
            offset = timedelta(0)
            while position_start_date + offset < effective_to:
                offset += timedelta(days=service_duration)
            return position_start_date + offset

    raise ValueError(
        "get_service_end_datetime: could not find " f"lane_code={lane_code!r}, proforma_name={proforma_name!r}."
    )


def get_service_start_datetime(lane_code: str, proforma_name: str, position_no: int) -> datetime:
    if INSTANCE_DATA is None:
        raise ValueError("get_service_start_datetime: utils are not initialized.")

    for lane in INSTANCE_DATA.service_lanes:
        if lane["lane_code"] != lane_code:
            continue
        for version in lane["versions"]:
            if version["proforma_name"] != proforma_name:
                continue

            anchor_date = version["anchor_date"]
            position_start_date = anchor_date + timedelta(days=7 * (position_no - 1))
            return position_start_date

    raise ValueError(
        "get_service_start_datetime: could not find " f"lane_code={lane_code!r}, proforma_name={proforma_name!r}."
    )


def lookup_distance(from_port_code: str, to_port_code: str) -> int | float:
    if INSTANCE_DATA is None or DISTANCE_MATRIX is None or DISTANCE_ADJACENCY is None:
        raise ValueError("lookup_distance: utils are not initialized.")

    pair = (from_port_code, to_port_code)
    if from_port_code == to_port_code:
        return 0
    if pair in DISTANCE_MATRIX:
        return DISTANCE_MATRIX[pair]["distance"]

    alias_map = {"EGSUZ": "EGSCA", "EGSCA": "EGSUZ"}
    from_candidates = (from_port_code, alias_map.get(from_port_code, from_port_code))
    to_candidates = (to_port_code, alias_map.get(to_port_code, to_port_code))
    for candidate_pair in {
        (candidate_from, candidate_to) for candidate_from in from_candidates for candidate_to in to_candidates
    }:
        if candidate_pair[0] == candidate_pair[1]:
            return 0
        if candidate_pair in DISTANCE_MATRIX:
            return DISTANCE_MATRIX[candidate_pair]["distance"]

    queue: list[tuple[int | float, str]] = []
    best_distances: dict[str, int | float] = {}
    target_ports = set(to_candidates)
    for candidate_from in set(from_candidates):
        heappush(queue, (0, candidate_from))
        best_distances[candidate_from] = 0

    while queue:
        curr_distance, curr_port_code = heappop(queue)
        if curr_distance > best_distances.get(curr_port_code, float("inf")):
            continue
        if curr_port_code in target_ports:
            return curr_distance * 0.8

        for next_port_code, leg_distance in DISTANCE_ADJACENCY.get(curr_port_code, []):
            next_distance = curr_distance + leg_distance
            if next_distance < best_distances.get(next_port_code, float("inf")):
                best_distances[next_port_code] = next_distance
                heappush(queue, (next_distance, next_port_code))

    raise KeyError(
        "distance lookup failed for "
        f"{from_port_code!r} -> {to_port_code!r}, "
        "including EGSUZ/EGSCA and network shortest-path fallback."
    )


def is_canal_port(port_code: str) -> bool:
    return port_code in ("EGSUZ", "EGSCA", "PAPCA")


def lookup_ts_cost(year_month: str, lane_code: str, port_code: str) -> int | float:
    if INSTANCE_DATA is None or TRANSSHIPMENT_COST_BY_KEY is None:
        raise ValueError("lookup_ts_cost: utils are not initialized.")

    if is_canal_port(port_code):
        raise ValueError(
            f"lookup_ts_cost: port_code {port_code!r} is a canal port, which should not have transshipment cost."
        )
    key = (year_month, lane_code, port_code)
    try:
        return TRANSSHIPMENT_COST_BY_KEY[key]
    except KeyError as exc:
        raise KeyError(
            "lookup_ts_cost: could not find "
            f"year_month={year_month!r}, lane_code={lane_code!r}, port_code={port_code!r}."
        ) from exc


def to_year_month(dt: datetime) -> str:
    return dt.strftime("%Y%m")


def is_interval_event(event: VesselScheduleEvent) -> bool:
    return event_end_time(event) > event_start_time(event)


def _lookup_event_attr(event: VesselScheduleEvent, attr_name: str, kind: str):
    if attr_name is None:
        raise TypeError(f"{type(event).__name__} does not define {kind}.")
    return getattr(event, attr_name)


def event_start_time(event: VesselScheduleEvent) -> datetime:
    return _lookup_event_attr(event, event.start_time_attr, "start time")


def event_end_time(event: VesselScheduleEvent) -> datetime:
    return _lookup_event_attr(event, event.end_time_attr, "end time")


def event_start_port_code(event: VesselScheduleEvent) -> str:
    return _lookup_event_attr(event, event.start_port_attr, "start port")


def event_end_port_code(event: VesselScheduleEvent) -> str:
    return _lookup_event_attr(event, event.end_port_attr, "end port")


def print_events(schedules: list[VesselSchedule]) -> None:
    print("=" * 20 + " Vessel Schedules " + "=" * 20)
    print("\n".join(map(repr, schedules)))
    print("=" * 20 + " End of Vessel Schedules " + "=" * (20 - 7))
