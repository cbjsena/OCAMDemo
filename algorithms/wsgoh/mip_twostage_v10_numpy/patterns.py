from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime
import math
import time
from typing import Any

from algorithms.wsgoh.utils_mip import (
    CoverageKey,
    Pattern,
    PositionKey,
    _drop_orphan_patterns_with_count,
    _estimate_pattern_cost,
    _position_key_from_schedule,
    _safe_name,
    _schedule_coverage_keys,
    _schedule_payload_signature,
    _try_assign_service_as_primary,
    _try_insert_service_schedule,
)
from algorithms.wsgoh.twostage_support.cascade_chain import solver as lite_chain_solver
from ocam.models import Delivery, DryDock, Idle, InstanceData, OutLaneSail, Redelivery, VesselScheduleEvent, CascadingSolution
from ocam.utils import event_end_port_code, event_end_time, event_start_port_code, event_start_time

from .config import (
    MAX_BASE_ACTUAL_CANDIDATE_PER_POSITION,
    MAX_BASE_ACTUAL_CANDIDATE_TOTAL_PER_SEED,
    MAX_BASE_ACTUAL_PRIMARY_PER_TARGET,
    MAX_BASE_ACTUAL_PRIMARY_TOTAL_PER_SEED,
    MAX_BASE_SCREENED_PER_TARGET,
    MAX_CANDIDATE_VESSELS_PER_HOLE,
    MAX_CASCADE_DEPTH,
    MAX_CHAIN_PATTERNS,
    MAX_HANDOVER_VARIANTS_PER_PAIR,
    MAX_PATTERNS_PER_VESSEL,
    MAX_TARGET_HOLES_PER_ROUND,
    _PATTERN_COST_CACHE,
)
from .numpy_screening import (
    STATUS_INCOMPATIBLE,
    STATUS_PASSED,
    ScreenConnection,
    ScreenRow,
    numpy_screening_may_run,
    screen_actual_candidate_batch,
    screen_actual_candidate_compact_batch,
    screen_actual_primary_compact_batch,
    screen_actual_primary_batch,
)
from .types import CoverageContext, PoolPruneStats
from .utils import (
    _can_reposition_ports,
    _connection_events,
    _family_label,
    _schedule_is_consistent,
)

_PATTERN_TIMING: Counter[str] = Counter()
_PATTERN_SIGNATURE_BY_ID: dict[str, tuple[Any, ...]] = {}
_FAST_COST_INDEX_BY_INSTANCE_ID: dict[int, "_FastCostIndex"] = {}


def reset_pattern_timing_stats() -> None:
    _PATTERN_TIMING.clear()
    _PATTERN_SIGNATURE_BY_ID.clear()
    _FAST_COST_INDEX_BY_INSTANCE_ID.clear()


def format_pattern_timing_stats() -> str:
    if not _PATTERN_TIMING:
        return "(none)"
    time_keys = sorted(key for key in _PATTERN_TIMING if key.endswith("_seconds"))
    count_keys = sorted(key for key in _PATTERN_TIMING if key.endswith("_calls"))
    parts = [f"{key}={_PATTERN_TIMING[key]:.3f}" for key in time_keys]
    parts.extend(f"{key}={int(_PATTERN_TIMING[key])}" for key in count_keys)
    return ", ".join(parts) if parts else "(none)"


def _add_timing(key: str, started: float) -> None:
    _PATTERN_TIMING[key] += time.monotonic() - started


def _pattern_schedule_signature(pattern: Pattern) -> tuple[Any, ...]:
    signature = _PATTERN_SIGNATURE_BY_ID.get(pattern.pattern_id)
    if signature is not None:
        _PATTERN_TIMING["pattern_signature_cache_hit_calls"] += 1
        return signature

    started = time.monotonic()
    signature = _schedule_payload_signature(pattern.schedule_payload)
    _PATTERN_SIGNATURE_BY_ID[pattern.pattern_id] = signature
    _add_timing("pattern_signature_cache_miss_seconds", started)
    _PATTERN_TIMING["pattern_signature_cache_miss_calls"] += 1
    return signature


@dataclass(frozen=True)
class _FastCostIndex:
    vessel_capacity_by_code: dict[str, int]
    bunker_port_by_capacity: dict[int, dict[str, float]]
    bunker_sea_by_capacity: dict[int, dict[float, float]]
    bunker_price_by_key: dict[tuple[str, str, str], float]
    average_bunker_prices_by_year_month_type: dict[tuple[str, str], float]
    distance_by_leg: dict[tuple[str, str], tuple[float, float]]
    canal_fee_by_key: dict[tuple[str, str, str], float]
    canal_direction_by_key: dict[tuple[str, str, str], str]
    ts_cost_by_key: dict[tuple[str, str, str], float]
    eca_ports: frozenset[str]


def _fast_cost_index(instance_data: InstanceData) -> _FastCostIndex:
    cache_key = id(instance_data)
    cached = _FAST_COST_INDEX_BY_INSTANCE_ID.get(cache_key)
    if cached is not None:
        return cached

    started = time.monotonic()
    prices_by_year_month_type: dict[tuple[str, str], list[float]] = {}
    for row in instance_data.bunker_price:
        prices_by_year_month_type.setdefault((row["year_month"], row["bunker_type"]), []).append(float(row["price"]))

    ts_cost_by_key: dict[tuple[str, str, str], float] = {}
    for row in instance_data.transshipment_cost:
        year_month = row["year_month"]
        lane_code = row["lane_code"]
        for port in row["ports"]:
            ts_cost_by_key[(year_month, lane_code, port["port_code"])] = float(port["ts_cost"])

    index = _FastCostIndex(
        vessel_capacity_by_code={row["vessel_code"]: int(row["capacity_teu"]) for row in instance_data.vessels},
        bunker_port_by_capacity={
            int(row["capacity_teu"]): row["consumption"] for row in instance_data.bunker_consumption_port
        },
        bunker_sea_by_capacity={
            int(row["capacity_teu"]): {
                float(consumption["speed"]): float(consumption["consumption_for_sailing"])
                for consumption in row["consumption"]
            }
            for row in instance_data.bunker_consumption_sea
        },
        bunker_price_by_key={
            (row["year_month"], row["lane_code"], row["bunker_type"]): float(row["price"])
            for row in instance_data.bunker_price
        },
        average_bunker_prices_by_year_month_type={
            key: sum(values) / len(values) for key, values in prices_by_year_month_type.items()
        },
        distance_by_leg={
            (row["from_port_code"], row["to_port_code"]): (float(row["distance"]), float(row["eca_distance"]))
            for row in instance_data.distances
        },
        canal_fee_by_key={
            (row["vessel_code"], row["direction"], row["port_code"]): float(row["fee"])
            for row in instance_data.canal_fee
        },
        canal_direction_by_key={
            (row["from_port_code"], row["canal_port_code"], row["to_port_code"]): row["direction"]
            for row in instance_data.canal_direction
        },
        ts_cost_by_key=ts_cost_by_key,
        eca_ports=frozenset(instance_data.eca_ports),
    )
    _FAST_COST_INDEX_BY_INSTANCE_ID[cache_key] = index
    _add_timing("fast_cost_index_seconds", started)
    _PATTERN_TIMING["fast_cost_index_calls"] += 1
    return index


def _payload_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise TypeError(f"unsupported datetime payload {type(value)!r}")


def _payload_year_month(value: Any) -> str:
    return _payload_datetime(value).strftime("%Y%m")


def _payload_event_start_time(event: dict[str, Any]) -> datetime:
    status = event.get("status")
    if status in {"INLANE_SAIL", "OUTLANE_SAIL"}:
        return _payload_datetime(event["sea_sail_start"])
    if status == "PORT_STAY":
        return _payload_datetime(event["pilot_in_start"])
    if status == "PHASE_IN":
        return _payload_datetime(event["phase_in_time"])
    if status == "PHASE_OUT":
        return _payload_datetime(event["phase_out_time"])
    if status == "TRANSSHIPMENT_UNLOAD":
        return _payload_datetime(event["unload_start"])
    if status == "TRANSSHIPMENT_LOAD":
        return _payload_datetime(event["load_start"])
    if status == "DELIVERY":
        return _payload_datetime(event["delivery_time"])
    if status == "DRY_DOCK":
        return _payload_datetime(event["dock_in"])
    if status == "IDLE":
        return _payload_datetime(event["idle_start"])
    if status == "REDELIVERY":
        return _payload_datetime(event["redelivery_time"])
    raise TypeError(f"unsupported event status {status!r}")


def _payload_event_end_time(event: dict[str, Any]) -> datetime:
    status = event.get("status")
    if status in {"INLANE_SAIL", "OUTLANE_SAIL"}:
        return _payload_datetime(event["sea_sail_end"])
    if status == "PORT_STAY":
        return _payload_datetime(event["pilot_out_end"])
    if status == "PHASE_IN":
        return _payload_datetime(event["phase_in_time"])
    if status == "PHASE_OUT":
        return _payload_datetime(event["phase_out_time"])
    if status == "TRANSSHIPMENT_UNLOAD":
        return _payload_datetime(event["unload_end"])
    if status == "TRANSSHIPMENT_LOAD":
        return _payload_datetime(event["load_end"])
    if status == "DELIVERY":
        return _payload_datetime(event["delivery_time"])
    if status == "DRY_DOCK":
        return _payload_datetime(event["dock_out"])
    if status == "IDLE":
        return _payload_datetime(event["idle_end"])
    if status == "REDELIVERY":
        return _payload_datetime(event["redelivery_time"])
    raise TypeError(f"unsupported event status {status!r}")


def _payload_is_inlane_event(event: dict[str, Any]) -> bool:
    return event.get("status") in {
        "INLANE_SAIL",
        "PORT_STAY",
        "PHASE_IN",
        "PHASE_OUT",
        "TRANSSHIPMENT_UNLOAD",
        "TRANSSHIPMENT_LOAD",
    }


def _canonical_canal_port_code(port_code: str) -> str:
    return "EGSCA" if port_code == "EGSUZ" else port_code


def _round_sailing_speed(speed: float) -> float:
    rounded = math.ceil(speed * 2.0) / 2.0
    return min(20.0, max(14.0, rounded))


def _average_bunker_prices(index: _FastCostIndex, year_month: str) -> dict[str, float]:
    return {
        "LSFO": index.average_bunker_prices_by_year_month_type[(year_month, "LSFO")],
        "MGO": index.average_bunker_prices_by_year_month_type[(year_month, "MGO")],
    }


def _bunker_event_cost_component_payload(
    index: _FastCostIndex,
    events: list[dict[str, Any]],
    event_index: int,
    lsfo_consumption: float,
    mgo_consumption: float,
) -> float:
    event = events[event_index]
    year_month = _payload_event_end_time(event).strftime("%Y%m")
    base_event = event
    if not _payload_is_inlane_event(event):
        for next_event in events[event_index:]:
            if _payload_is_inlane_event(next_event):
                base_event = next_event
                break

    if not _payload_is_inlane_event(base_event):
        prices = _average_bunker_prices(index, year_month)
        return lsfo_consumption * prices["LSFO"] + mgo_consumption * prices["MGO"]

    lane_code = base_event["lane_code"]
    mgo_price = index.bunker_price_by_key.get((year_month, lane_code, "MGO"))
    lsfo_price = index.bunker_price_by_key.get((year_month, lane_code, "LSFO"))
    if mgo_price is None or lsfo_price is None:
        prices = _average_bunker_prices(index, year_month)
        mgo_price = prices["MGO"] if mgo_price is None else mgo_price
        lsfo_price = prices["LSFO"] if lsfo_price is None else lsfo_price
    return lsfo_consumption * lsfo_price + mgo_consumption * mgo_price


def _transshipment_unload_cost_fast(index: _FastCostIndex, events: list[dict[str, Any]]) -> float:
    cost = 0.0
    for event in events:
        if event.get("status") != "TRANSSHIPMENT_UNLOAD":
            continue
        key = (_payload_year_month(event["unload_start"]), event["lane_code"], event["ts_port_code"])
        cost += index.ts_cost_by_key[key]
    return cost


def _fast_estimate_actual_pattern_cost(
    instance_data: InstanceData,
    vessel_code: str,
    schedule_payload: dict[str, Any],
) -> float | None:
    events = schedule_payload.get("events", [])

    started = time.monotonic()
    try:
        index = _fast_cost_index(instance_data)
        capacity_teu = index.vessel_capacity_by_code[vessel_code]
        port_consumption = index.bunker_port_by_capacity[capacity_teu]

        bunker_cost = 0.0
        for event_index, event in enumerate(events):
            status = event.get("status")
            if status in {"INLANE_SAIL", "OUTLANE_SAIL"}:
                distance, eca_distance = index.distance_by_leg[(event["from_port_code"], event["to_port_code"])]
                duration_hours = (
                    _payload_datetime(event["sea_sail_end"]) - _payload_datetime(event["sea_sail_start"])
                ).total_seconds() / 3600.0
                if duration_hours <= 0.0:
                    return None
                rounded_speed = _round_sailing_speed(distance / duration_hours)
                sail_hours = distance / rounded_speed if rounded_speed > 0.0 else 0.0
                daily_consumption = index.bunker_sea_by_capacity[capacity_teu][rounded_speed]
                bunker_consumption_tons = (daily_consumption / 24.0) * sail_hours
                mgo_rate = eca_distance / distance
                mgo_consumption = bunker_consumption_tons * mgo_rate
                lsfo_consumption = bunker_consumption_tons - mgo_consumption
                bunker_cost += _bunker_event_cost_component_payload(
                    index,
                    events,
                    event_index,
                    lsfo_consumption,
                    mgo_consumption,
                )
            elif status == "PORT_STAY":
                pilot_hours = (
                    _payload_datetime(event["berthing_start"]) - _payload_datetime(event["pilot_in_start"])
                ).total_seconds() / 3600.0
                pilot_hours += (
                    _payload_datetime(event["pilot_out_end"]) - _payload_datetime(event["berthing_end"])
                ).total_seconds() / 3600.0
                bunker_consumption_tons = port_consumption["consumption_for_pilot"] * pilot_hours
                if event["port_code"] in index.eca_ports:
                    mgo_consumption = bunker_consumption_tons
                    lsfo_consumption = 0.0
                else:
                    mgo_consumption = 0.0
                    lsfo_consumption = bunker_consumption_tons
                bunker_cost += _bunker_event_cost_component_payload(
                    index,
                    events,
                    event_index,
                    lsfo_consumption,
                    mgo_consumption,
                )

        canal_fee_cost = 0.0
        for prev_event, mid_event, next_event in zip(events, events[1:], events[2:]):
            if (
                prev_event.get("status") == "INLANE_SAIL"
                and mid_event.get("status") == "PORT_STAY"
                and next_event.get("status") == "INLANE_SAIL"
                and mid_event["port_code"] in {"EGSUZ", "EGSCA", "PAPCA"}
            ):
                canal_port_code = _canonical_canal_port_code(mid_event["port_code"])
                direction = index.canal_direction_by_key[
                    (prev_event["from_port_code"], canal_port_code, next_event["to_port_code"])
                ]
                canal_fee_cost += index.canal_fee_by_key[(vessel_code, direction, canal_port_code)]

        for prev_event, next_event in zip(events, events[1:]):
            if (
                prev_event.get("status") == "OUTLANE_SAIL"
                and next_event.get("status") == "OUTLANE_SAIL"
                and prev_event["to_port_code"] in {"EGSUZ", "EGSCA", "PAPCA"}
                and prev_event["to_port_code"] == next_event["from_port_code"]
            ):
                canal_port_code = _canonical_canal_port_code(prev_event["to_port_code"])
                direction = index.canal_direction_by_key[
                    (prev_event["from_port_code"], canal_port_code, next_event["to_port_code"])
                ]
                canal_fee_cost += index.canal_fee_by_key[(vessel_code, direction, canal_port_code)]

        cost = bunker_cost + canal_fee_cost + _transshipment_unload_cost_fast(index, events)
    except Exception:
        _PATTERN_TIMING["fast_cost_fallback_calls"] += 1
        return None

    _add_timing("fast_cost_estimate_seconds", started)
    _PATTERN_TIMING["fast_cost_estimate_calls"] += 1
    return cost


def _make_pattern(
    *,
    instance_data: InstanceData,
    context: CoverageContext,
    pattern_id: str,
    vessel_code: str,
    is_virtual: bool,
    schedule: list[Any],
    requires_pattern_ids: frozenset[str] = frozenset(),
    depth: int = 0,
    priority: float = 0.0,
    source_fragment_id: str | None = None,
    target_position_key: PositionKey | None = None,
    split_mode: str | None = None,
) -> Pattern:
    started_total = time.monotonic()
    _PATTERN_TIMING["make_pattern_calls"] += 1
    started_payload = time.monotonic()
    schedule_payload = {"events": [event.to_dict() for event in schedule]}
    _add_timing("make_pattern_payload_seconds", started_payload)
    started_signature = time.monotonic()
    schedule_signature = _schedule_payload_signature(schedule_payload)
    _PATTERN_SIGNATURE_BY_ID[pattern_id] = schedule_signature
    _add_timing("make_pattern_signature_seconds", started_signature)
    started_coverage = time.monotonic()
    coverage_keys = _schedule_coverage_keys(schedule, context.coverage_positions)
    _add_timing("make_pattern_coverage_seconds", started_coverage)
    _add_timing("make_pattern_total_seconds", started_total)
    return Pattern(
        pattern_id=pattern_id,
        vessel_code=vessel_code,
        is_virtual=is_virtual,
        schedule_payload=schedule_payload,
        coverage_keys=coverage_keys,
        cost=-1.0,
        requires_pattern_ids=requires_pattern_ids,
        depth=depth,
        priority=priority,
        source_fragment_id=source_fragment_id,
        target_position_key=target_position_key,
        split_mode=split_mode,
    )


def _ensure_pattern_costs(
    patterns: list[Pattern],
    instance_data: InstanceData,
    context: CoverageContext,
) -> list[Pattern]:
    started_total = time.monotonic()
    costed: list[Pattern] = []
    for pattern in patterns:
        if pattern.cost >= 0.0:
            costed.append(pattern)
            continue
        started_signature = time.monotonic()
        schedule_signature = _pattern_schedule_signature(pattern)
        _add_timing("deferred_cost_signature_seconds", started_signature)
        cost_cache_key = (
            "__virtual__" if pattern.is_virtual else pattern.vessel_code,
            pattern.is_virtual,
            schedule_signature,
        )
        cost = _PATTERN_COST_CACHE.get(cost_cache_key)
        if cost is None:
            if pattern.is_virtual:
                cost = None
            else:
                cost = _fast_estimate_actual_pattern_cost(instance_data, pattern.vessel_code, pattern.schedule_payload)
            if cost is None:
                started_cost = time.monotonic()
                cost = _estimate_pattern_cost(
                    instance_data,
                    context.declared_positions_payload,
                    pattern.vessel_code,
                    pattern.schedule_payload,
                    is_virtual=pattern.is_virtual,
                )
                _add_timing("deferred_cost_estimate_seconds", started_cost)
                _PATTERN_TIMING["deferred_cost_estimate_calls"] += 1
            _PATTERN_COST_CACHE[cost_cache_key] = cost
        costed.append(replace(pattern, cost=cost))
    _add_timing("ensure_pattern_costs_seconds", started_total)
    return costed

def _seconds_from_horizon_start(instance_data: InstanceData, value) -> float:
    return (value - instance_data.planning_horizon["start"]).total_seconds()


def _vessel_capacity_reefer(instance_data: InstanceData, vessel_code: str) -> tuple[float, float]:
    vessel = next((item for item in instance_data.vessels if item["vessel_code"] == vessel_code), None)
    if vessel is None:
        return 0.0, 0.0
    return float(vessel["capacity_teu"]), float(vessel["reefer_plug"])


@dataclass
class _NumericScreeningProfile:
    vessel_codes: list[str]
    vessel_capacity: Any
    vessel_reefer: Any
    has_fixed_coverage: Any
    event_count: Any
    event_start_seconds: Any
    event_end_seconds: Any
    event_start_port_ids: Any
    event_end_port_ids: Any
    event_insertable: Any
    protected_count: Any
    protected_start_seconds: Any
    protected_end_seconds: Any
    protected_start_port_ids: Any
    protected_end_port_ids: Any
    protected_type_codes: Any
    port_to_id: dict[str, int]
    distance_matrix: Any


def _event_type_code(event: Any) -> int:
    if isinstance(event, Delivery):
        return 1
    if isinstance(event, DryDock):
        return 2
    if isinstance(event, Redelivery):
        return 3
    return 0


def _collect_schedule_port_codes(schedule: list[Any], port_codes: set[str]) -> None:
    for event in schedule:
        start_port = event_start_port_code(event)
        end_port = event_end_port_code(event)
        if start_port is not None:
            port_codes.add(start_port)
        if end_port is not None:
            port_codes.add(end_port)


def _build_numeric_screening_profile(
    instance_data: InstanceData,
    vessel_schedules: dict[str, Any],
    fixed_positions: set[PositionKey],
    extra_schedules: list[list[Any]],
) -> _NumericScreeningProfile | None:
    if not numpy_screening_may_run():
        return None

    started = time.monotonic()
    try:
        import numpy as np
    except Exception:
        return None

    vessel_codes = sorted(vessel_schedules)
    vessel_count = len(vessel_codes)
    schedules = {vessel_code: list(vessel_schedules[vessel_code]) for vessel_code in vessel_codes}
    max_events = max(1, max((len(schedule) for schedule in schedules.values()), default=0))
    max_protected = max(
        1,
        max(
            (
                sum(1 for event in schedule if isinstance(event, (Delivery, DryDock, Redelivery)))
                for schedule in schedules.values()
            ),
            default=0,
        ),
    )

    port_codes: set[str] = set()
    for distance in instance_data.distances:
        if distance.get("from_port_code") is not None:
            port_codes.add(distance["from_port_code"])
        if distance.get("to_port_code") is not None:
            port_codes.add(distance["to_port_code"])
    for vessel_code in vessel_codes:
        schedule = schedules[vessel_code]
        _collect_schedule_port_codes(schedule, port_codes)
    for schedule in extra_schedules:
        _collect_schedule_port_codes(schedule, port_codes)

    ordered_ports = sorted(port_codes)
    port_to_id = {port_code: index for index, port_code in enumerate(ordered_ports)}
    port_count = max(1, len(ordered_ports))
    distance_matrix = np.full((port_count, port_count), -1.0, dtype=np.float64)
    for index in range(port_count):
        distance_matrix[index, index] = 0.0
    for distance in instance_data.distances:
        from_port = distance.get("from_port_code")
        to_port = distance.get("to_port_code")
        if from_port in port_to_id and to_port in port_to_id:
            distance_matrix[port_to_id[from_port], port_to_id[to_port]] = float(distance["distance"])

    vessel_capacity = np.zeros(vessel_count, dtype=np.float64)
    vessel_reefer = np.zeros(vessel_count, dtype=np.float64)
    has_fixed_coverage = np.zeros(vessel_count, dtype=np.uint8)
    event_count = np.zeros(vessel_count, dtype=np.int64)
    event_start_seconds = np.zeros((vessel_count, max_events), dtype=np.float64)
    event_end_seconds = np.zeros((vessel_count, max_events), dtype=np.float64)
    event_start_port_ids = np.full((vessel_count, max_events), -1, dtype=np.int64)
    event_end_port_ids = np.full((vessel_count, max_events), -1, dtype=np.int64)
    event_insertable = np.zeros((vessel_count, max_events), dtype=np.uint8)
    protected_count = np.zeros(vessel_count, dtype=np.int64)
    protected_start_seconds = np.zeros((vessel_count, max_protected), dtype=np.float64)
    protected_end_seconds = np.zeros((vessel_count, max_protected), dtype=np.float64)
    protected_start_port_ids = np.full((vessel_count, max_protected), -1, dtype=np.int64)
    protected_end_port_ids = np.full((vessel_count, max_protected), -1, dtype=np.int64)
    protected_type_codes = np.zeros((vessel_count, max_protected), dtype=np.int64)

    for vessel_index, vessel_code in enumerate(vessel_codes):
        capacity, reefer = _vessel_capacity_reefer(instance_data, vessel_code)
        vessel_capacity[vessel_index] = capacity
        vessel_reefer[vessel_index] = reefer
        schedule = schedules[vessel_code]
        has_fixed_coverage[vessel_index] = 1 if _schedule_coverage_keys(schedule, fixed_positions) else 0
        event_count[vessel_index] = len(schedule)
        protected_index = 0
        for event_index, event in enumerate(schedule[:max_events]):
            event_start_seconds[vessel_index, event_index] = _seconds_from_horizon_start(instance_data, event_start_time(event))
            event_end_seconds[vessel_index, event_index] = _seconds_from_horizon_start(instance_data, event_end_time(event))
            event_start_port_ids[vessel_index, event_index] = port_to_id.get(event_start_port_code(event), -1)
            event_end_port_ids[vessel_index, event_index] = port_to_id.get(event_end_port_code(event), -1)
            event_insertable[vessel_index, event_index] = 1 if isinstance(event, (Idle, OutLaneSail)) else 0
            protected_type = _event_type_code(event)
            if protected_type and protected_index < max_protected:
                protected_start_seconds[vessel_index, protected_index] = event_start_seconds[vessel_index, event_index]
                protected_end_seconds[vessel_index, protected_index] = event_end_seconds[vessel_index, event_index]
                protected_start_port_ids[vessel_index, protected_index] = event_start_port_ids[vessel_index, event_index]
                protected_end_port_ids[vessel_index, protected_index] = event_end_port_ids[vessel_index, event_index]
                protected_type_codes[vessel_index, protected_index] = protected_type
                protected_index += 1
        protected_count[vessel_index] = protected_index

    _add_timing("numpy_numeric_profile_seconds", started)
    _PATTERN_TIMING["numpy_numeric_profile_calls"] += 1
    return _NumericScreeningProfile(
        vessel_codes=vessel_codes,
        vessel_capacity=vessel_capacity,
        vessel_reefer=vessel_reefer,
        has_fixed_coverage=has_fixed_coverage,
        event_count=event_count,
        event_start_seconds=event_start_seconds,
        event_end_seconds=event_end_seconds,
        event_start_port_ids=event_start_port_ids,
        event_end_port_ids=event_end_port_ids,
        event_insertable=event_insertable,
        protected_count=protected_count,
        protected_start_seconds=protected_start_seconds,
        protected_end_seconds=protected_end_seconds,
        protected_start_port_ids=protected_start_port_ids,
        protected_end_port_ids=protected_end_port_ids,
        protected_type_codes=protected_type_codes,
        port_to_id=port_to_id,
        distance_matrix=distance_matrix,
    )


def _target_arrays(
    instance_data: InstanceData,
    profile: _NumericScreeningProfile,
    target_items: list[tuple[Any, PositionKey, list[Any]]],
) -> tuple[Any, Any, Any, Any, Any, Any, Any] | None:
    started = time.monotonic()
    try:
        import numpy as np
    except Exception:
        return None

    target_count = len(target_items)
    required_capacity = np.zeros(target_count, dtype=np.float64)
    required_reefer = np.zeros(target_count, dtype=np.float64)
    start_seconds = np.zeros(target_count, dtype=np.float64)
    end_seconds = np.zeros(target_count, dtype=np.float64)
    start_port_ids = np.full(target_count, -1, dtype=np.int64)
    end_port_ids = np.full(target_count, -1, dtype=np.int64)
    for target_index, (_, position_key, schedule) in enumerate(target_items):
        capacity, reefer = _position_requirements(instance_data, position_key)
        required_capacity[target_index] = capacity
        required_reefer[target_index] = reefer
        start_seconds[target_index] = _seconds_from_horizon_start(instance_data, event_start_time(schedule[0]))
        end_seconds[target_index] = _seconds_from_horizon_start(instance_data, event_end_time(schedule[-1]))
        start_port_ids[target_index] = profile.port_to_id.get(event_start_port_code(schedule[0]), -1)
        end_port_ids[target_index] = profile.port_to_id.get(event_end_port_code(schedule[-1]), -1)
    _add_timing("numpy_target_array_seconds", started)
    _PATTERN_TIMING["numpy_target_array_calls"] += 1
    return (
        required_capacity,
        required_reefer,
        start_seconds,
        end_seconds,
        start_port_ids,
        end_port_ids,
        profile.distance_matrix,
    )


def _screen_row(
    *,
    instance_data: InstanceData,
    vessel_code: str,
    position_key: PositionKey,
    base_valid: bool,
    connections: list[ScreenConnection],
) -> ScreenRow:
    capacity, reefer = _vessel_capacity_reefer(instance_data, vessel_code)
    required_capacity, required_reefer = _position_requirements(instance_data, position_key)
    return ScreenRow(
        vessel_capacity=capacity,
        vessel_reefer=reefer,
        required_capacity=required_capacity,
        required_reefer=required_reefer,
        base_valid=base_valid,
        connections=tuple(connections),
    )


def _actual_candidate_numpy_statuses(
    instance_data: InstanceData,
    vessel_schedules: dict[str, Any],
    selectable_schedules: dict[PositionKey, list[Any]],
    profile: _NumericScreeningProfile | None,
) -> dict[tuple[PositionKey, str], tuple[int, int, int]] | None:
    if not numpy_screening_may_run():
        return None
    if profile is not None:
        started = time.monotonic()
        target_items = [
            (position_key, position_key, list(selectable_schedules[position_key]))
            for position_key in sorted(selectable_schedules)
        ]
        arrays = _target_arrays(instance_data, profile, target_items)
        if arrays is not None:
            (
                required_capacity,
                required_reefer,
                start_seconds,
                end_seconds,
                start_port_ids,
                end_port_ids,
                distance_matrix,
            ) = arrays
            result = screen_actual_candidate_compact_batch(
                vessel_capacity=profile.vessel_capacity,
                vessel_reefer=profile.vessel_reefer,
                event_count=profile.event_count,
                event_start_seconds=profile.event_start_seconds,
                event_end_seconds=profile.event_end_seconds,
                event_start_port_ids=profile.event_start_port_ids,
                event_end_port_ids=profile.event_end_port_ids,
                event_insertable=profile.event_insertable,
                target_required_capacity=required_capacity,
                target_required_reefer=required_reefer,
                target_start_seconds=start_seconds,
                target_end_seconds=end_seconds,
                target_start_port_ids=start_port_ids,
                target_end_port_ids=end_port_ids,
                distance_matrix=distance_matrix,
            )
            _add_timing("numpy_candidate_compact_total_seconds", started)
            if result is None:
                return None
            statuses, first_indices, last_indices = result
            output: dict[tuple[PositionKey, str], tuple[int, int, int]] = {}
            row_index = 0
            for position_key, _, _ in target_items:
                for vessel_code in profile.vessel_codes:
                    output[(position_key, vessel_code)] = (
                        statuses[row_index],
                        first_indices[row_index],
                        last_indices[row_index],
                    )
                    row_index += 1
            return output

    started_rows = time.monotonic()
    rows: list[ScreenRow] = []
    keys: list[tuple[PositionKey, str]] = []
    for position_key, service_schedule in sorted(selectable_schedules.items()):
        service_start = event_start_time(service_schedule[0])
        service_end = event_end_time(service_schedule[-1])
        service_start_port = event_start_port_code(service_schedule[0])
        service_end_port = event_end_port_code(service_schedule[-1])

        for vessel_code, raw_schedule in sorted(vessel_schedules.items()):
            if not _vessel_matches_position(instance_data, vessel_code, position_key):
                continue
            vessel_schedule = list(raw_schedule)
            base_valid = True
            connections: list[ScreenConnection] = []
            overlapping = [
                (index, event)
                for index, event in enumerate(vessel_schedule)
                if event_end_time(event) >= service_start and event_start_time(event) <= service_end
            ]
            if not overlapping or any(not isinstance(event, (Idle, OutLaneSail)) for _, event in overlapping):
                base_valid = False
            else:
                first_index = overlapping[0][0]
                last_index = overlapping[-1][0]
                prefix = vessel_schedule[:first_index]
                suffix = vessel_schedule[last_index + 1 :]
                if not prefix:
                    base_valid = False
                else:
                    depart_event = prefix[-1]
                    connections.append(
                        ScreenConnection(
                            from_port_code=event_end_port_code(depart_event),
                            from_time_seconds=_seconds_from_horizon_start(instance_data, event_end_time(depart_event)),
                            to_port_code=service_start_port,
                            to_time_seconds=_seconds_from_horizon_start(instance_data, service_start),
                        )
                    )
                    if suffix:
                        return_event = suffix[0]
                        connections.append(
                            ScreenConnection(
                                from_port_code=service_end_port,
                                from_time_seconds=_seconds_from_horizon_start(instance_data, service_end),
                                to_port_code=event_start_port_code(return_event),
                                to_time_seconds=_seconds_from_horizon_start(instance_data, event_start_time(return_event)),
                            )
                        )

            rows.append(
                _screen_row(
                    instance_data=instance_data,
                    vessel_code=vessel_code,
                    position_key=position_key,
                    base_valid=base_valid,
                    connections=connections,
                )
            )
            keys.append((position_key, vessel_code))

    _add_timing("numpy_candidate_legacy_row_build_seconds", started_rows)
    statuses = screen_actual_candidate_batch(instance_data, rows)
    if statuses is None:
        return None
    return {key: (status, -1, -1) for key, status in zip(keys, statuses)}


def _numeric_connection_sort_score(
    profile: _NumericScreeningProfile,
    from_port_id: int,
    from_time_seconds: float,
    to_port_id: int,
    to_time_seconds: float,
) -> float:
    if from_port_id < 0 or to_port_id < 0:
        return 1.0e18
    duration_hours = (to_time_seconds - from_time_seconds) / 3600.0
    if from_port_id == to_port_id:
        return max(0.0, duration_hours) * 0.001
    port_count = int(profile.distance_matrix.shape[0])
    if from_port_id >= port_count or to_port_id >= port_count:
        return 1.0e18
    distance = float(profile.distance_matrix[from_port_id, to_port_id])
    if distance < 0.0 or duration_hours <= 0.0:
        return 1.0e18
    required_speed = distance / (duration_hours + 1e-5)
    speed_penalty = max(0.0, required_speed - 14.0) * distance * 0.03
    return distance + speed_penalty


def _candidate_connection_sort_score(
    instance_data: InstanceData,
    profile: _NumericScreeningProfile | None,
    vessel_index_by_code: dict[str, int],
    vessel_code: str,
    service_schedule: list[Any],
    first_index: int,
    last_index: int,
) -> float:
    if profile is None or first_index <= 0 or last_index < first_index:
        return 1.0e18
    vessel_index = vessel_index_by_code.get(vessel_code)
    if vessel_index is None:
        return 1.0e18
    event_count = int(profile.event_count[vessel_index])
    if event_count <= 0 or first_index >= event_count or last_index >= event_count:
        return 1.0e18

    service_start = _seconds_from_horizon_start(instance_data, event_start_time(service_schedule[0]))
    service_end = _seconds_from_horizon_start(instance_data, event_end_time(service_schedule[-1]))
    service_start_port = profile.port_to_id.get(event_start_port_code(service_schedule[0]), -1)
    service_end_port = profile.port_to_id.get(event_end_port_code(service_schedule[-1]), -1)

    depart_index = first_index - 1
    score = _numeric_connection_sort_score(
        profile,
        int(profile.event_end_port_ids[vessel_index, depart_index]),
        float(profile.event_end_seconds[vessel_index, depart_index]),
        service_start_port,
        service_start,
    )

    suffix_index = last_index + 1
    if suffix_index < event_count:
        score += _numeric_connection_sort_score(
            profile,
            service_end_port,
            service_end,
            int(profile.event_start_port_ids[vessel_index, suffix_index]),
            float(profile.event_start_seconds[vessel_index, suffix_index]),
        )
    return score


def _candidate_vessel_sort_key(
    instance_data: InstanceData,
    profile: _NumericScreeningProfile | None,
    vessel_index_by_code: dict[str, int],
    position_key: PositionKey,
    service_schedule: list[Any],
    vessel_code: str,
    candidate_statuses: dict[tuple[PositionKey, str], tuple[int, int, int]] | None,
) -> tuple[int, float, str]:
    if candidate_statuses is None:
        return (0, 0.0, vessel_code)
    status, first_index, last_index = candidate_statuses.get(
        (position_key, vessel_code),
        (STATUS_INCOMPATIBLE, -1, -1),
    )
    if status == STATUS_PASSED:
        return (
            0,
            _candidate_connection_sort_score(
                instance_data,
                profile,
                vessel_index_by_code,
                vessel_code,
                service_schedule,
                first_index,
                last_index,
            ),
            vessel_code,
        )
    if status == STATUS_INCOMPATIBLE:
        return (2, 1.0e18, vessel_code)
    return (1, 1.0e18, vessel_code)


def _primary_numpy_statuses(
    instance_data: InstanceData,
    vessel_schedules: dict[str, Any],
    virtual_schedules: dict[str, Any],
    fixed_positions: set[PositionKey],
    profile: _NumericScreeningProfile | None,
) -> dict[tuple[str, str], int] | None:
    if not numpy_screening_may_run():
        return None
    if profile is not None:
        started = time.monotonic()
        target_items: list[tuple[str, PositionKey, list[Any]]] = []
        for virtual_code, raw_virtual_schedule in sorted(virtual_schedules.items()):
            virtual_schedule = list(raw_virtual_schedule)
            position_key = _position_key_from_schedule(virtual_schedule)
            if position_key is not None:
                target_items.append((virtual_code, position_key, virtual_schedule))
        arrays = _target_arrays(instance_data, profile, target_items)
        if arrays is not None:
            (
                required_capacity,
                required_reefer,
                start_seconds,
                end_seconds,
                start_port_ids,
                end_port_ids,
                distance_matrix,
            ) = arrays
            statuses = screen_actual_primary_compact_batch(
                vessel_capacity=profile.vessel_capacity,
                vessel_reefer=profile.vessel_reefer,
                has_fixed_coverage=profile.has_fixed_coverage,
                protected_count=profile.protected_count,
                protected_start_seconds=profile.protected_start_seconds,
                protected_end_seconds=profile.protected_end_seconds,
                protected_start_port_ids=profile.protected_start_port_ids,
                protected_end_port_ids=profile.protected_end_port_ids,
                protected_type_codes=profile.protected_type_codes,
                target_required_capacity=required_capacity,
                target_required_reefer=required_reefer,
                target_start_seconds=start_seconds,
                target_end_seconds=end_seconds,
                target_start_port_ids=start_port_ids,
                target_end_port_ids=end_port_ids,
                distance_matrix=distance_matrix,
            )
            _add_timing("numpy_primary_compact_total_seconds", started)
            if statuses is None:
                return None
            output: dict[tuple[str, str], int] = {}
            row_index = 0
            for virtual_code, _, _ in target_items:
                for vessel_code in profile.vessel_codes:
                    output[(virtual_code, vessel_code)] = statuses[row_index]
                    row_index += 1
            return output

    started_rows = time.monotonic()
    rows: list[ScreenRow] = []
    keys: list[tuple[str, str]] = []
    for virtual_code, raw_virtual_schedule in sorted(virtual_schedules.items()):
        virtual_schedule = list(raw_virtual_schedule)
        position_key = _position_key_from_schedule(virtual_schedule)
        if position_key is None:
            continue

        service_start = event_start_time(virtual_schedule[0])
        service_end = event_end_time(virtual_schedule[-1])

        for vessel_code, raw_schedule in sorted(vessel_schedules.items()):
            if not _vessel_matches_position(instance_data, vessel_code, position_key):
                continue
            vessel_schedule = list(raw_schedule)
            if _schedule_coverage_keys(vessel_schedule, fixed_positions):
                continue

            protected_events = [event for event in vessel_schedule if isinstance(event, (Delivery, DryDock, Redelivery))]
            base_valid = bool(protected_events)
            blocks: list[list[Any]] = [[event] for event in protected_events] + [virtual_schedule]
            if base_valid:
                for event in protected_events:
                    if isinstance(event, Delivery) and service_start < event.delivery_time:
                        base_valid = False
                    elif isinstance(event, Redelivery) and service_end > event.redelivery_time:
                        base_valid = False
                    elif isinstance(event, DryDock):
                        if event.dock_in < service_end and service_start < event.dock_out:
                            base_valid = False
                blocks.sort(key=lambda block: event_start_time(block[0]))

            connections: list[ScreenConnection] = []
            if base_valid:
                for previous_block, next_block in zip(blocks, blocks[1:]):
                    previous_event = previous_block[-1]
                    next_event = next_block[0]
                    connections.append(
                        ScreenConnection(
                            from_port_code=event_end_port_code(previous_event),
                            from_time_seconds=_seconds_from_horizon_start(instance_data, event_end_time(previous_event)),
                            to_port_code=event_start_port_code(next_event),
                            to_time_seconds=_seconds_from_horizon_start(instance_data, event_start_time(next_event)),
                        )
                    )

            rows.append(
                _screen_row(
                    instance_data=instance_data,
                    vessel_code=vessel_code,
                    position_key=position_key,
                    base_valid=base_valid,
                    connections=connections,
                )
            )
            keys.append((virtual_code, vessel_code))

    _add_timing("numpy_primary_legacy_row_build_seconds", started_rows)
    statuses = screen_actual_primary_batch(instance_data, rows)
    if statuses is None:
        return None
    return dict(zip(keys, statuses))


def _try_insert_service_schedule_with_window(
    instance_data: InstanceData,
    vessel_code: str,
    vessel_schedule: list[Any],
    position_key: PositionKey,
    service_schedule: list[Any],
    first_index: int,
    last_index: int,
) -> list[Any] | None:
    if first_index < 0 or last_index < first_index or last_index >= len(vessel_schedule):
        return _try_insert_service_schedule(instance_data, vessel_code, vessel_schedule, position_key, service_schedule)
    if not _vessel_matches_position(instance_data, vessel_code, position_key):
        return None
    overlapping = vessel_schedule[first_index : last_index + 1]
    if not overlapping or any(not isinstance(event, (Idle, OutLaneSail)) for event in overlapping):
        return None
    prefix = vessel_schedule[:first_index]
    suffix = vessel_schedule[last_index + 1 :]
    if not prefix:
        return None

    service_start = event_start_time(service_schedule[0])
    service_end = event_end_time(service_schedule[-1])
    service_start_port = event_start_port_code(service_schedule[0])
    service_end_port = event_end_port_code(service_schedule[-1])

    depart_event = prefix[-1]
    depart_time = event_end_time(depart_event)
    depart_port = event_end_port_code(depart_event)
    if not _can_reposition_ports(depart_port, depart_time, service_start_port, service_start):
        return None

    candidate = list(prefix)
    candidate.extend(_connection_events(depart_port, depart_time, service_start_port, service_start))
    candidate.extend(service_schedule)

    if suffix:
        return_time = event_start_time(suffix[0])
        return_port = event_start_port_code(suffix[0])
        if not _can_reposition_ports(service_end_port, service_end, return_port, return_time):
            return None
        candidate.extend(_connection_events(service_end_port, service_end, return_port, return_time))
        candidate.extend(suffix)
    elif service_end < instance_data.planning_horizon["end"]:
        candidate.append(
            Idle(
                port_code=service_end_port,
                idle_start=service_end,
                idle_end=instance_data.planning_horizon["end"],
            )
        )

    if not _schedule_is_consistent(candidate):
        return None
    return candidate


def _build_seed_patterns(
    instance_data: InstanceData,
    seed_solution: CascadingSolution,
    context: CoverageContext,
    seed_name: str,
    *,
    canonical: bool,
) -> list[Pattern]:
    patterns: list[Pattern] = []
    safe_seed = _safe_name(seed_name)
    actual_priority = 950_000.0
    virtual_priority = 930_000.0

    for vessel_code, schedule in sorted(seed_solution.vessel_schedules.items()):
        pattern_id = f"actual-seed:{safe_seed}:{vessel_code}"
        patterns.append(
            _make_pattern(
                instance_data=instance_data,
                context=context,
                pattern_id=pattern_id,
                vessel_code=vessel_code,
                is_virtual=False,
                schedule=list(schedule),
                priority=actual_priority,
                source_fragment_id=f"seed:{seed_name}",
            )
        )

    for vessel_code, schedule in sorted(seed_solution.virtual_vessel_schedules.items()):
        pattern_id = f"virtual-seed:{safe_seed}:{vessel_code}"
        virtual_vessel_code = f"MIPTWOSTAGE_{safe_seed}_{vessel_code}"
        patterns.append(
            _make_pattern(
                instance_data=instance_data,
                context=context,
                pattern_id=pattern_id,
                vessel_code=virtual_vessel_code,
                is_virtual=True,
                schedule=list(schedule),
                priority=virtual_priority,
                source_fragment_id=f"seed:{seed_name}",
            )
        )

    fixed_positions = context.coverage_positions - context.selectable_positions
    numeric_profile = _build_numeric_screening_profile(
        instance_data,
        seed_solution.vessel_schedules,
        fixed_positions,
        list(seed_solution.virtual_vessel_schedules.values()) + list(context.selectable_schedules.values()),
    )
    primary_statuses = _primary_numpy_statuses(
        instance_data,
        seed_solution.vessel_schedules,
        seed_solution.virtual_vessel_schedules,
        fixed_positions,
        numeric_profile,
    )
    replacement_count = 0
    for virtual_code, virtual_schedule in sorted(seed_solution.virtual_vessel_schedules.items()):
        if replacement_count >= MAX_BASE_ACTUAL_PRIMARY_TOTAL_PER_SEED:
            break
        position_key = _position_key_from_schedule(virtual_schedule)
        if position_key is None:
            continue
        accepted_for_target = 0
        screened_for_target = 0
        for vessel_code, vessel_schedule in sorted(seed_solution.vessel_schedules.items()):
            if accepted_for_target >= MAX_BASE_ACTUAL_PRIMARY_PER_TARGET:
                break
            if replacement_count >= MAX_BASE_ACTUAL_PRIMARY_TOTAL_PER_SEED:
                break
            if screened_for_target >= MAX_BASE_SCREENED_PER_TARGET:
                break
            if _schedule_coverage_keys(vessel_schedule, fixed_positions):
                continue
            if primary_statuses is None:
                if not _vessel_matches_position(instance_data, vessel_code, position_key):
                    continue
            else:
                numpy_status = primary_statuses.get((virtual_code, vessel_code), STATUS_INCOMPATIBLE)
                if numpy_status == STATUS_INCOMPATIBLE:
                    continue
            screened_for_target += 1
            if primary_statuses is not None and numpy_status != STATUS_PASSED:
                continue
            _PATTERN_TIMING["primary_schedule_builder_calls"] += 1
            started_builder = time.monotonic()
            primary_schedule = _try_assign_service_as_primary(
                instance_data,
                vessel_code,
                list(vessel_schedule),
                position_key,
                list(virtual_schedule),
            )
            _add_timing("primary_schedule_builder_seconds", started_builder)
            if primary_schedule is None:
                continue
            replacement_count += 1
            patterns.append(
                _make_pattern(
                    instance_data=instance_data,
                    context=context,
                    pattern_id=(
                        "actual-primary-virtual:"
                        f"{safe_seed}:{vessel_code}:{virtual_code}:{position_key[0]}:"
                        f"{position_key[1]}:{position_key[2]}:{replacement_count}"
                    ),
                    vessel_code=vessel_code,
                    is_virtual=False,
                    schedule=primary_schedule,
                    priority=760_000.0,
                    source_fragment_id=f"seed-initial-virtual:{seed_name}:{virtual_code}",
                    target_position_key=position_key,
                )
            )
            accepted_for_target += 1

    selectable_items = sorted(context.selectable_schedules.items())
    vessel_items = sorted(seed_solution.vessel_schedules.items())
    vessel_index_by_code = (
        {vessel_code: index for index, vessel_code in enumerate(numeric_profile.vessel_codes)}
        if numeric_profile is not None
        else {}
    )
    candidate_statuses: dict[tuple[PositionKey, str], tuple[int, int, int]] | None = None
    started_candidate_status = time.monotonic()
    if numeric_profile is not None:
        candidate_statuses = _actual_candidate_numpy_statuses(
            instance_data,
            seed_solution.vessel_schedules,
            dict(selectable_items),
            numeric_profile,
        )
    _add_timing("candidate_status_precompute_seconds", started_candidate_status)
    _PATTERN_TIMING["candidate_status_precompute_calls"] += 1
    insertion_count = 0
    for position_key, service_schedule in selectable_items:
        if insertion_count >= MAX_BASE_ACTUAL_CANDIDATE_TOTAL_PER_SEED:
            break
        accepted_for_position = 0
        screened_for_position = 0
        ordered_vessel_items = (
            sorted(
                vessel_items,
                key=lambda item: _candidate_vessel_sort_key(
                    instance_data,
                    numeric_profile,
                    vessel_index_by_code,
                    position_key,
                    list(service_schedule),
                    item[0],
                    candidate_statuses,
                ),
            )
            if candidate_statuses is not None
            else vessel_items
        )
        for vessel_code, schedule in ordered_vessel_items:
            if accepted_for_position >= MAX_BASE_ACTUAL_CANDIDATE_PER_POSITION:
                break
            if insertion_count >= MAX_BASE_ACTUAL_CANDIDATE_TOTAL_PER_SEED:
                break
            if screened_for_position >= MAX_BASE_SCREENED_PER_TARGET:
                break
            if candidate_statuses is None:
                if not _vessel_matches_position(instance_data, vessel_code, position_key):
                    continue
                numpy_record = (STATUS_PASSED, -1, -1)
            else:
                numpy_record = candidate_statuses.get((position_key, vessel_code), (STATUS_INCOMPATIBLE, -1, -1))
                numpy_status = numpy_record[0]
                if numpy_status == STATUS_INCOMPATIBLE:
                    continue
            screened_for_position += 1
            if candidate_statuses is not None and numpy_record[0] != STATUS_PASSED:
                continue
            _PATTERN_TIMING["candidate_schedule_builder_calls"] += 1
            started_builder = time.monotonic()
            first_index, last_index = numpy_record[1], numpy_record[2]
            if candidate_statuses is not None and first_index >= 0 and last_index >= first_index:
                candidate = _try_insert_service_schedule_with_window(
                    instance_data,
                    vessel_code,
                    list(schedule),
                    position_key,
                    service_schedule,
                    first_index,
                    last_index,
                )
            else:
                candidate = _try_insert_service_schedule(
                    instance_data,
                    vessel_code,
                    list(schedule),
                    position_key,
                    service_schedule,
                )
            _add_timing("candidate_schedule_builder_seconds", started_builder)
            if candidate is None:
                continue
            insertion_count += 1
            patterns.append(
                _make_pattern(
                    instance_data=instance_data,
                    context=context,
                    pattern_id=(
                        "actual-candidate:"
                        f"{safe_seed}:{vessel_code}:{position_key[0]}:{position_key[1]}:"
                        f"{position_key[2]}:{insertion_count}"
                    ),
                    vessel_code=vessel_code,
                    is_virtual=False,
                    schedule=candidate,
                    priority=95_000.0,
                    source_fragment_id=f"seed-selectable:{seed_name}:{position_key[0]}:{position_key[1]}:{position_key[2]}",
                    target_position_key=position_key,
                )
            )
            accepted_for_position += 1

    return patterns

def _build_virtual_full_fallback_patterns(
    instance_data: InstanceData,
    context: CoverageContext,
) -> list[Pattern]:
    patterns: list[Pattern] = []
    for position_key, schedule in sorted(context.coverage_schedules.items()):
        patterns.append(
            _make_pattern(
                instance_data=instance_data,
                context=context,
                pattern_id=f"virtual-fallback:{position_key[0]}:{position_key[1]}:{position_key[2]}",
                vessel_code=f"MIPTWOSTAGE_FULL_{position_key[0]}_{position_key[1]}_{position_key[2]}",
                is_virtual=True,
                schedule=schedule,
                priority=900_000.0,
                target_position_key=position_key,
                source_fragment_id="virtual-full-fallback",
            )
        )
    return patterns

def _build_empty_actual_patterns(
    instance_data: InstanceData,
    context: CoverageContext,
) -> list[Pattern]:
    patterns: list[Pattern] = []
    for vessel in sorted(instance_data.vessels, key=lambda item: item["vessel_code"]):
        vessel_code = vessel["vessel_code"]
        schedule = _build_empty_actual_schedule(instance_data, vessel)
        if schedule is None:
            continue
        patterns.append(
            _make_pattern(
                instance_data=instance_data,
                context=context,
                pattern_id=f"actual-empty:{vessel_code}",
                vessel_code=vessel_code,
                is_virtual=False,
                schedule=schedule,
                priority=100_000.0,
                source_fragment_id="obligation-only-empty-coverage",
            )
        )
    return patterns

def _build_empty_actual_schedule(
    instance_data: InstanceData,
    vessel: dict[str, Any],
) -> list[VesselScheduleEvent] | None:
    if vessel.get("current_assignment") is not None:
        return None

    planning_start = instance_data.planning_horizon["start"]
    planning_end = instance_data.planning_horizon["end"]
    available_from = vessel.get("available_from")
    available_from_port_code = vessel.get("available_from_port_code")
    if available_from_port_code is None:
        return None

    schedule: list[VesselScheduleEvent] = []
    if available_from is not None and planning_start <= available_from <= planning_end:
        schedule.append(
            Delivery(
                delivery_port_code=available_from_port_code,
                delivery_time=available_from,
            )
        )
        current_time = available_from
    else:
        current_time = planning_start
    current_port = available_from_port_code

    next_dock_in = vessel.get("next_dock_in")
    next_dock_out = vessel.get("next_dock_out")
    next_dock_port_code = vessel.get("next_dock_port_code")
    if (
        next_dock_in is None
        or next_dock_out is None
        or next_dock_port_code is None
        or next_dock_in > planning_end
        or next_dock_in <= current_time
    ):
        next_dock_in = None
        next_dock_out = None
        next_dock_port_code = None

    available_to = vessel.get("available_to")
    redelivery_port_code = vessel.get("available_to_port_code")
    if (
        available_to is None
        or redelivery_port_code is None
        or available_to > planning_end
        or available_to <= current_time
    ):
        available_to = None
        redelivery_port_code = None

    if next_dock_in is not None and available_to is not None and next_dock_in >= available_to:
        next_dock_in = None
        next_dock_out = None
        next_dock_port_code = None
    if next_dock_out is not None and available_to is not None and next_dock_out >= available_to:
        available_to = None
        redelivery_port_code = None

    if next_dock_in is not None and next_dock_out is not None and next_dock_port_code is not None:
        if not _can_reposition_ports(current_port, current_time, next_dock_port_code, next_dock_in):
            return None
        schedule.extend(_connection_events(current_port, current_time, next_dock_port_code, next_dock_in))
        schedule.append(
            DryDock(
                dock_port_code=next_dock_port_code,
                dock_in=next_dock_in,
                dock_out=next_dock_out,
            )
        )
        current_port = next_dock_port_code
        current_time = next_dock_out

    if available_to is not None and redelivery_port_code is not None:
        if not _can_reposition_ports(current_port, current_time, redelivery_port_code, available_to):
            return None
        schedule.extend(_connection_events(current_port, current_time, redelivery_port_code, available_to))
        schedule.append(
            Redelivery(
                redelivery_port_code=redelivery_port_code,
                redelivery_time=available_to,
            )
        )
    elif current_time < planning_end:
        schedule.append(
            Idle(
                port_code=current_port,
                idle_start=current_time,
                idle_end=planning_end,
            )
        )

    if not schedule:
        return None
    return schedule if _schedule_is_consistent(schedule) else None

def _protected_pattern_ids(patterns: list[Pattern], selected_pattern_ids: set[str]) -> set[str]:
    protected = set(selected_pattern_ids)
    for pattern in patterns:
        if pattern.pattern_id.startswith(("actual-empty:", "virtual-fallback:")):
            protected.add(pattern.pattern_id)
        protected.update(pattern.requires_pattern_ids)
    return protected

def _prefer_pattern(candidate: Pattern, incumbent: Pattern, protected_ids: set[str]) -> Pattern:
    candidate_key = (
        candidate.pattern_id in protected_ids,
        candidate.priority,
        -candidate.cost,
    )
    incumbent_key = (
        incumbent.pattern_id in protected_ids,
        incumbent.priority,
        -incumbent.cost,
    )
    return candidate if candidate_key > incumbent_key else incumbent

def _pattern_prune_signature(pattern: Pattern) -> tuple[Any, ...]:
    return (
        pattern.vessel_code,
        pattern.is_virtual,
        pattern.requires_pattern_ids,
        pattern.coverage_keys,
        _family_label(pattern),
        pattern.source_fragment_id,
        pattern.target_position_key,
        pattern.split_mode,
    )

def _prune_pattern_pool(
    patterns: list[Pattern],
    selected_pattern_ids: set[str],
    *,
    instance_data: InstanceData,
    context: CoverageContext,
    max_total_patterns: int,
) -> tuple[list[Pattern], PoolPruneStats]:
    started_prune = time.monotonic()
    started = time.monotonic()
    protected_ids = _protected_pattern_ids(patterns, selected_pattern_ids)
    _add_timing("prune_protected_ids_seconds", started)
    stats = PoolPruneStats(input_count=len(patterns), retained_count=0)

    started = time.monotonic()
    by_schedule: dict[tuple[Any, ...], Pattern] = {}
    for pattern in patterns:
        started_signature = time.monotonic()
        schedule_signature = _pattern_schedule_signature(pattern)
        _add_timing("prune_schedule_signature_seconds", started_signature)
        signature = (
            pattern.vessel_code,
            pattern.is_virtual,
            pattern.requires_pattern_ids,
            schedule_signature,
        )
        incumbent = by_schedule.get(signature)
        by_schedule[signature] = pattern if incumbent is None else _prefer_pattern(pattern, incumbent, protected_ids)
    stats.schedule_duplicate_pruned = len(patterns) - len(by_schedule)
    _add_timing("prune_schedule_dedup_seconds", started)

    started = time.monotonic()
    deduplicated_by_schedule = _ensure_pattern_costs(list(by_schedule.values()), instance_data, context)
    _add_timing("prune_costing_seconds", started)

    started = time.monotonic()
    by_coverage: dict[tuple[Any, ...], Pattern] = {}
    for pattern in deduplicated_by_schedule:
        started_signature = time.monotonic()
        signature = _pattern_prune_signature(pattern)
        _add_timing("prune_coverage_signature_seconds", started_signature)
        incumbent = by_coverage.get(signature)
        by_coverage[signature] = pattern if incumbent is None else _prefer_pattern(pattern, incumbent, protected_ids)
    stats.coverage_duplicate_pruned = len(by_schedule) - len(by_coverage)
    _add_timing("prune_coverage_dedup_seconds", started)

    started = time.monotonic()
    retained = list(by_coverage.values())
    actual_by_vessel: dict[str, list[Pattern]] = {}
    virtual_patterns: list[Pattern] = []
    for pattern in retained:
        if pattern.is_virtual:
            virtual_patterns.append(pattern)
        else:
            actual_by_vessel.setdefault(pattern.vessel_code, []).append(pattern)

    capped: list[Pattern] = list(virtual_patterns)
    for vessel_patterns in actual_by_vessel.values():
        protected = [pattern for pattern in vessel_patterns if pattern.pattern_id in protected_ids]
        candidates = [pattern for pattern in vessel_patterns if pattern.pattern_id not in protected_ids]
        candidates.sort(key=lambda pattern: (-pattern.priority, pattern.cost, pattern.pattern_id))
        capped.extend(protected + candidates[: max(0, MAX_PATTERNS_PER_VESSEL - len(protected))])
    stats.vessel_cap_pruned = len(retained) - len(capped)
    _add_timing("prune_vessel_cap_seconds", started)

    started = time.monotonic()
    capped, orphan_count = _drop_orphan_patterns_with_count(capped)
    stats.orphan_pruned += orphan_count
    _add_timing("prune_orphan_seconds", started)
    if len(capped) > max_total_patterns:
        started = time.monotonic()
        protected = [pattern for pattern in capped if pattern.pattern_id in protected_ids]
        candidates = [pattern for pattern in capped if pattern.pattern_id not in protected_ids]
        candidates.sort(key=lambda pattern: (-pattern.priority, pattern.cost, pattern.pattern_id))
        capped = protected + candidates[: max(0, max_total_patterns - len(protected))]
        stats.total_cap_pruned = len(retained) - len(capped)
        capped, orphan_count = _drop_orphan_patterns_with_count(capped)
        stats.orphan_pruned += orphan_count
        _add_timing("prune_total_cap_seconds", started)

    stats.retained_count = len(capped)
    started = time.monotonic()
    output = sorted(capped, key=lambda pattern: pattern.pattern_id)
    _add_timing("prune_final_sort_seconds", started)
    _add_timing("prune_total_seconds", started_prune)
    return output, stats

def _virtual_fragments(solution: CascadingSolution) -> list[tuple[str, PositionKey, list[Any]]]:
    fragments: list[tuple[str, PositionKey, list[Any]]] = []
    for virtual_code, schedule in sorted(solution.virtual_vessel_schedules.items()):
        position_key = _position_key_from_schedule(schedule)
        if position_key is not None:
            fragments.append((virtual_code, position_key, list(schedule)))
    return fragments

def _position_requirements(instance_data: InstanceData, position_key: PositionKey) -> tuple[float, float]:
    lane_code, proforma_name, _ = position_key
    for lane in instance_data.service_lanes:
        if lane["lane_code"] != lane_code:
            continue
        for version in lane["versions"]:
            if version["proforma_name"] == proforma_name:
                return float(version["required_capacity_teu"]), float(version["required_reefer_plug"])
    return 0.0, 0.0

def _vessel_matches_position(instance_data: InstanceData, vessel_code: str, position_key: PositionKey) -> bool:
    required_capacity_teu, required_reefer_plug = _position_requirements(instance_data, position_key)
    vessel = next((item for item in instance_data.vessels if item["vessel_code"] == vessel_code), None)
    if vessel is None:
        return False
    return (
        required_capacity_teu * 0.95 <= float(vessel["capacity_teu"]) <= required_capacity_teu * 1.05
        and required_reefer_plug <= float(vessel["reefer_plug"])
    )

def _configure_lite_chain_limits() -> None:
    lite_chain_solver.MAX_CASCADE_DEPTH = MAX_CASCADE_DEPTH
    lite_chain_solver.MAX_TARGET_HOLES_PER_ROUND = MAX_TARGET_HOLES_PER_ROUND
    lite_chain_solver.MAX_CANDIDATE_VESSELS_PER_HOLE = MAX_CANDIDATE_VESSELS_PER_HOLE
    lite_chain_solver.MAX_HANDOVER_OPTIONS_PER_VESSEL_HOLE = MAX_HANDOVER_VARIANTS_PER_PAIR
    lite_chain_solver.MAX_CHAIN_PATTERNS_PER_ROUND = MAX_CHAIN_PATTERNS

def _generate_chain_patterns_from_seeds(
    instance_data: InstanceData,
    context: CoverageContext,
    seed_solutions: dict[str, CascadingSolution],
) -> tuple[list[Pattern], list[Pattern], Counter[str]]:
    _configure_lite_chain_limits()
    actual_patterns: list[Pattern] = []
    virtual_patterns: list[Pattern] = []
    diagnostics: Counter[str] = Counter()
    round_index = 0
    for seed_name, solution in sorted(seed_solutions.items()):
        round_index += 1
        generated, seed_diagnostics = lite_chain_solver.generate_chain_patterns(
            instance_data,
            context,
            solution,
            round_index,
        )
        diagnostics.update({f"{seed_name}_{key}": value for key, value in seed_diagnostics.items()})
        for pattern in generated:
            if pattern.is_virtual:
                virtual_patterns.append(pattern)
            else:
                actual_patterns.append(pattern)
            if len(actual_patterns) >= MAX_CHAIN_PATTERNS:
                break
        if len(actual_patterns) >= MAX_CHAIN_PATTERNS:
            break
    diagnostics["cascade_chain_actual"] = len(actual_patterns)
    diagnostics["cascade_chain_virtual"] = len(virtual_patterns)
    return actual_patterns[:MAX_CHAIN_PATTERNS], virtual_patterns, diagnostics

def _actual_only_patterns(patterns: list[Pattern]) -> list[Pattern]:
    actual_ids = {pattern.pattern_id for pattern in patterns if not pattern.is_virtual}
    retained = [
        pattern
        for pattern in patterns
        if not pattern.is_virtual and pattern.requires_pattern_ids.issubset(actual_ids)
    ]
    retained, _ = _drop_orphan_patterns_with_count(retained)
    return retained

def _warm_start_z_values(
    context: CoverageContext,
    patterns: list[Pattern],
    warm_start_ids: set[str],
) -> dict[PositionKey, int]:
    values = {key: 0 for key in sorted(context.selectable_positions)}
    for pattern in patterns:
        if pattern.pattern_id not in warm_start_ids:
            continue
        for coverage_key in pattern.coverage_keys:
            position_key = (coverage_key[0], coverage_key[1], coverage_key[2])
            if position_key in values:
                values[position_key] = 1
    return values

def _seed_actual_pattern_ids(patterns: list[Pattern], seed_name: str) -> set[str]:
    prefix = f"actual-seed:{_safe_name(seed_name)}:"
    return {pattern.pattern_id for pattern in patterns if pattern.pattern_id.startswith(prefix)}

def _seed_all_pattern_ids(patterns: list[Pattern], seed_name: str) -> set[str]:
    safe_seed = _safe_name(seed_name)
    return {
        pattern.pattern_id
        for pattern in patterns
        if pattern.pattern_id.startswith(f"actual-seed:{safe_seed}:")
        or pattern.pattern_id.startswith(f"virtual-seed:{safe_seed}:")
    }

def _complete_actual_warm_start(
    patterns: list[Pattern],
    warm_start_ids: set[str],
    instance_data: InstanceData,
) -> set[str]:
    completed = set(warm_start_ids)
    selected_by_vessel = {
        pattern.vessel_code
        for pattern in patterns
        if not pattern.is_virtual and pattern.pattern_id in completed
    }
    by_vessel: dict[str, list[Pattern]] = {}
    for pattern in patterns:
        if not pattern.is_virtual:
            by_vessel.setdefault(pattern.vessel_code, []).append(pattern)
    for vessel in sorted(instance_data.vessels, key=lambda item: item["vessel_code"]):
        vessel_code = vessel["vessel_code"]
        if vessel_code in selected_by_vessel:
            continue
        candidates = by_vessel.get(vessel_code, [])
        if candidates:
            completed.add(min(candidates, key=lambda pattern: (pattern.cost, pattern.pattern_id)).pattern_id)
    return completed
