from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from typing import Any

from ocam.models import InputBundle, InstanceData


def _require_non_null(value: Any, message: str) -> Any:
    if value is None:
        raise ValueError(message)
    return value


def _coerce_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    if isinstance(value, str):
        normalized = value.strip()
        if normalized.endswith("Z"):
            normalized = normalized[:-1]
        normalized = normalized.replace(" ", "T")
        return datetime.fromisoformat(normalized)
    raise TypeError("ocam.preprocessing._coerce_datetime: unsupported value type " f"{type(value)!r}.")


def _minutes_from_day_time(day_number: int | None, hhmm: str | None) -> int | None:
    if day_number is None or hhmm is None:
        return None
    value = hhmm.strip()
    if len(value) != 4 or not value.isdigit():
        raise ValueError("ocam.preprocessing._minutes_from_day_time: " f"expected HHMM, got {hhmm!r}.")
    hours = int(value[:2])
    minutes = int(value[2:])
    return day_number * 24 * 60 + hours * 60 + minutes


def _to_int_minutes(hours: float | int | None) -> int | None:
    if hours is None:
        return None
    return int(round(float(hours) * 60))


def _build_metadata(bundle: InputBundle, tables: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    payload_metadata = bundle.payload.get("metadata", {})
    scenario_name = payload_metadata.get("scenario_name") or bundle.input_dir.name

    planning_start = _coerce_datetime(payload_metadata.get("planning_horizon", {}).get("start"))
    planning_end = _coerce_datetime(payload_metadata.get("planning_horizon", {}).get("end"))

    if planning_start is None:
        # 오늘
        planning_start = datetime.combine(date.today(), time.min)
    if planning_end is None:
        # 오늘로부터 6개월 뒤
        planning_end = planning_start + timedelta(days=30 * 6)

    return {
        "scenario_name": scenario_name,
        "planning_horizon": {
            "start": planning_start,
            "end": planning_end,
        },
    }


def _build_service_lanes(
    tables: dict[str, list[dict[str, Any]]],
    planning_horizon_start: datetime,
    planning_horizon_end: datetime,
) -> list[dict[str, Any]]:
    # Base lane/version rows are enriched by detail, assignment, and declared-position tables.
    schedule_proforma = sorted(
        tables.get("schedule_proforma", []),
        key=lambda row: (
            row["lane_code"],
            row["effective_from_date"],
            row["proforma_name"],
        ),
    )
    proforma_detail = tables.get("schedule_proforma_detail", [])
    current_assignment = tables.get("vessel_current_assignment", [])
    cascading_positions = tables.get("schedule_cascading_vessel_position", [])
    vessel_capacity = tables.get("vessel_capacity", [])

    detail_by_version: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in proforma_detail:
        detail_by_version[(row["lane_code"], row["proforma_name"])].append(row)

    assignment_by_version: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in current_assignment:
        assignment_by_version[(row["lane_code"], row["proforma_name"])].append(row)

    declared_positions_by_version: dict[tuple[str, str], list[int]] = defaultdict(list)
    anchor_by_version: dict[tuple[str, str], datetime] = {}
    for row in cascading_positions:
        key = (row["lane_code"], row["proforma_name"])
        declared_positions_by_version[key].append(row["vessel_position"])
        if row["vessel_position"] == 1:
            anchor_by_version[key] = row["vessel_position_date"]

    reefer_by_version: dict[tuple[str, str], int] = defaultdict(int)
    for row in vessel_capacity:
        key = (row["lane_code"], row["vessel_code"])
        reefer_by_version[key] = max(reefer_by_version[key], row["reefer_capacity"])

    versions_by_lane: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in schedule_proforma:
        versions_by_lane[row["lane_code"]].append(row)

    service_lanes: list[dict[str, Any]] = []
    for lane_code in sorted(versions_by_lane):
        source_versions = versions_by_lane[lane_code]
        lane_versions: list[dict[str, Any]] = []

        # Planning horizon에 걸쳐 유효한 버전들만 선별.
        # 각 버전의 effective_from_date가 planning_horizon_end보다 늦거나,
        # 다음 버전의 effective_from_date가 planning_horizon_start보다 빠르면
        # 해당 버전은 계획 수립에 영향을 미치지 않으므로 제외.
        for index, row in enumerate(source_versions):
            source_effective_to = (
                source_versions[index + 1]["effective_from_date"] if index + 1 < len(source_versions) else None
            )
            starts_after_horizon = row["effective_from_date"] > planning_horizon_end
            ends_before_horizon = source_effective_to is not None and source_effective_to < planning_horizon_start
            if starts_after_horizon or ends_before_horizon:
                continue
            lane_versions.append(row)
        if not lane_versions:
            continue
        serialized_versions: list[dict[str, Any]] = []

        # 유효 버전 전처리
        for index, version_row in enumerate(lane_versions):
            key = (version_row["lane_code"], version_row["proforma_name"])
            # After trimming, the last surviving version is clipped to planning_horizon_end.
            next_effective_from = (
                lane_versions[index + 1]["effective_from_date"]
                if index + 1 < len(lane_versions)
                else planning_horizon_end
            )

            # 현행 버전인지 미래 버전인지 판단하여 포지션 및 선박 할당 정보 처리
            declared_positions = sorted(declared_positions_by_version.get(key, []))
            is_future_lane = version_row["effective_from_date"] > planning_horizon_start
            if is_future_lane:
                if declared_positions:
                    raise ValueError(
                        "ocam.preprocessing._build_service_lanes: "
                        "future lane "
                        f"(lane_code={key[0]!r}, proforma_name={key[1]!r}) "
                        "must not have declared_positions."
                    )
                available_positions = list(range(1, version_row["declared_count"] + 1))
            else:
                if not declared_positions:
                    raise ValueError(
                        "ocam.preprocessing._build_service_lanes: "
                        "currently operated lane "
                        f"(lane_code={key[0]!r}, proforma_name={key[1]!r}) "
                        "must have declared_positions."
                    )
                available_positions = []

            assignments = sorted(
                assignment_by_version.get(key, []),
                key=lambda row: (row["vessel_position"], row["vessel_code"]),
            )

            if is_future_lane and assignments:
                raise ValueError(
                    "ocam.preprocessing._build_service_lanes: "
                    "future lane "
                    f"(lane_code={key[0]!r}, proforma_name={key[1]!r}) "
                    "must not have vessel assignments."
                )

            if not is_future_lane and not assignments:
                raise ValueError(
                    "ocam.preprocessing._build_service_lanes: "
                    "cannot determine available_positions for "
                    f"(lane_code={key[0]!r}, proforma_name={key[1]!r}) "
                    "without vessel assignments."
                )

            if declared_positions and not assignments:
                raise ValueError(
                    "ocam.preprocessing._build_service_lanes: "
                    "cannot determine required_reefer_plug for "
                    f"(lane_code={key[0]!r}, proforma_name={key[1]!r}) "
                    "without vessel assignments."
                )

            # TODO
            required_reefer_plug = 0
            # for assignment in assignments:
            #     reefer_key = (version_row["lane_code"], assignment["vessel_code"])
            #     if reefer_key not in reefer_by_version:
            #         raise ValueError(
            #             "ocam.preprocessing._build_service_lanes: "
            #             f"missing reefer capacity for vessel {assignment['vessel_code']} "
            #             f"on lane {version_row['lane_code']}."
            #         )
            #     required_reefer_plug = max(
            #         required_reefer_plug,
            #         reefer_by_version[reefer_key],
            #     )

            vessel_assignments = [
                {
                    "position_no": row["vessel_position"],
                    "vessel_code": row["vessel_code"],
                }
                for row in assignments
            ]

            # port rotation 전처리
            port_rows = sorted(
                detail_by_version.get(key, []),
                key=lambda row: row["calling_port_seq"],
            )
            port_rotation: list[dict[str, Any]] = []
            first_port_row = port_rows[0]
            first_port_values: dict[str, int | None] | None = None

            offset = _minutes_from_day_time(
                first_port_row["etb_day_number"], first_port_row["etb_day_time"]
            ) - _to_int_minutes(first_port_row["pilot_in_hours"])

            for port_index, port_row in enumerate(port_rows):
                is_last = port_index == len(port_rows) - 1
                etb_minutes = _minutes_from_day_time(
                    port_row["etb_day_number"],
                    port_row["etb_day_time"],
                )
                etd_minutes = _minutes_from_day_time(
                    port_row["etd_day_number"],
                    port_row["etd_day_time"],
                )
                pilot_in_minutes = _to_int_minutes(port_row["pilot_in_hours"])
                pilot_out_minutes = _to_int_minutes(port_row["pilot_out_hours"])

                if is_last:
                    pilot_in_minutes = first_port_values["pilot_in_minutes"]
                    pilot_out_minutes = first_port_values["pilot_out_minutes"]

                etb_offset_minutes = etb_minutes
                eta_offset_minutes = etb_offset_minutes - pilot_in_minutes

                if is_last:
                    etd_offset_minutes = etb_offset_minutes + first_port_values["etd-etb"]
                else:
                    etd_offset_minutes = etd_minutes

                berthing_minutes = etd_offset_minutes - etb_offset_minutes
                in_port_minutes = berthing_minutes + pilot_in_minutes + pilot_out_minutes

                if port_index == 0:
                    first_port_values = {
                        "pilot_in_minutes": pilot_in_minutes,
                        "pilot_out_minutes": pilot_out_minutes,
                        "etd-etb": etd_offset_minutes - etb_offset_minutes,
                    }

                port_rotation.append(
                    {
                        "port_seq": port_row["calling_port_seq"],
                        "port_code": port_row["port_code"],
                        "direction": port_row["direction"],
                        "pilot_in_minutes": pilot_in_minutes,
                        "pilot_out_minutes": pilot_out_minutes,
                        "berthing_minutes": berthing_minutes,
                        "in_port_minutes": in_port_minutes,
                        "eta_offset_minutes": eta_offset_minutes - offset,
                        "etb_offset_minutes": etb_offset_minutes - offset,
                        "etd_offset_minutes": etd_offset_minutes - offset,
                    }
                )

            # HERE
            # anchor_date
            weekday_to_num = {
                "MON": 0,
                "TUE": 1,
                "WED": 2,
                "THU": 3,
                "FRI": 4,
                "SAT": 5,
                "SUN": 6,
            }
            weekday_code = first_port_row.get("etb_day_code").upper()
            if weekday_code not in weekday_to_num:
                raise ValueError(
                    "ocam.preprocessing._build_service_lanes: "
                    f"invalid etb_day_code {weekday_code!r} for "
                    f"(lane_code={key[0]!r}, proforma_name={key[1]!r})."
                )
            first_etb_hhmm = first_port_row.get("etb_day_time")
            first_pilot_in_minutes = first_port_values["pilot_in_minutes"]
            effective_from = version_row["effective_from_date"]
            aligned_first_etb = effective_from.replace(
                hour=0,
                minute=0,
                second=0,
                microsecond=0,
            )
            days_ahead = (weekday_to_num[weekday_code] - aligned_first_etb.weekday()) % 7
            aligned_first_etb = aligned_first_etb + timedelta(days=days_ahead)
            aligned_first_etb = aligned_first_etb + timedelta(minutes=_minutes_from_day_time(0, first_etb_hhmm))
            if aligned_first_etb < effective_from:
                aligned_first_etb = aligned_first_etb + timedelta(days=7)
            anchor_date = aligned_first_etb - timedelta(minutes=first_pilot_in_minutes)

            serialized_versions.append(
                {
                    "proforma_name": version_row["proforma_name"],
                    "effective_from": version_row["effective_from_date"],
                    "effective_to": next_effective_from,
                    "required_capacity_teu": int(version_row["declared_capacity"]),
                    "required_reefer_plug": required_reefer_plug,
                    "service_duration": int(round(version_row["duration"])),
                    "total_vessel_count": version_row["declared_count"],
                    "own_vessel_count": version_row["own_vessel_count"],
                    "declared_positions": declared_positions,
                    "available_positions": available_positions,
                    "anchor_date": anchor_date,
                    "vessel_assignments": vessel_assignments,
                    "port_rotation": port_rotation,
                }
            )

        service_lanes.append(
            {
                "lane_code": lane_code,
                "versions": serialized_versions,
            }
        )

    return service_lanes


def _build_vessels(tables: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    vessel_info = tables.get("vessel_info", [])
    vessel_capacity = tables.get("vessel_capacity", [])
    vessel_current_assignment = tables.get("vessel_current_assignment", [])

    assignment_by_vessel = {row["vessel_code"]: row for row in vessel_current_assignment}

    vessels: list[dict[str, Any]] = []
    for row in sorted(vessel_info, key=lambda value: value["vessel_code"]):
        vessel_code = row["vessel_code"]

        # TODO vessel capacity가 선박별로 단일해야 하는데 현재 Engine input interface에서는 그렇지 않음.
        # 추후 변경될 것 같으므로 수정 필요함.
        cap_rows = [cap_row for cap_row in vessel_capacity if cap_row["vessel_code"] == vessel_code]
        if not cap_rows:
            raise ValueError(
                "ocam.preprocessing._build_vessels: " f"missing vessel_capacity for vessel {vessel_code!r}."
            )
        capacity_teu = cap_rows[0]["capacity"]
        reefer_plug = cap_rows[0]["reefer_capacity"]
        delivery_date = row["delivery_date"]
        delivery_port_code = row["delivery_port_code"]
        redelivery_date = row["redelivery_date"]
        redelivery_port_code = row["redelivery_port_code"]
        built_date = row["built_date"]
        built_port_code = row["built_port_code"]

        is_own = row["own_yn"] == "O"
        is_newly_built = is_own and built_port_code is not None

        available_from = delivery_date
        available_from_port_code = delivery_port_code
        available_to = redelivery_date
        available_to_port_code = redelivery_port_code

        next_dock_in = row["next_dock_in_date"]
        next_dock_out = row["next_dock_out_date"]
        next_dock_port_code = row["next_dock_port_code"]

        if next_dock_in is not None:
            if available_to is not None and next_dock_in >= available_to:
                next_dock_in = None
                next_dock_out = None
                next_dock_port_code = None

        if is_own and is_newly_built:
            available_from = built_date
            available_from_port_code = built_port_code

        # 자사선은 dlivery/redelivery 정보 None이어야 함
        # 용선은 built 정보 None이어야 함
        if is_own and (delivery_date is not None or redelivery_date is not None):
            raise ValueError(
                "ocam.preprocessing._build_vessels: "
                f"owned vessel {vessel_code!r} must not have delivery/redelivery info."
            )
        if not is_own and (built_date is not None or built_port_code is not None):
            raise ValueError(
                "ocam.preprocessing._build_vessels: " f"chartered vessel {vessel_code!r} must not have built info."
            )

        assignment = assignment_by_vessel.get(row["vessel_code"])
        current_assignment = None
        if assignment is not None:
            current_assignment = {  # TODO: LRS로부터 이 정보를 가져와야 함.
                "lane_code": assignment["lane_code"],
                "proforma_name": assignment["proforma_name"],
                "position_no": assignment["vessel_position"],
            }

        vessels.append(
            {
                "vessel_code": vessel_code,
                "capacity_teu": capacity_teu,
                "reefer_plug": reefer_plug,
                "is_own": int(is_own),
                "is_newly_built": int(is_newly_built),
                "available_from": available_from,
                "available_from_port_code": available_from_port_code,
                "available_to": available_to,
                "available_to_port_code": available_to_port_code,
                "next_dock_in": next_dock_in,
                "next_dock_out": next_dock_out,
                "next_dock_port_code": next_dock_port_code,
                "current_assignment": current_assignment,
            }
        )

    return vessels


def _build_bunker_consumption_port(
    tables: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    return [
        {
            "capacity_teu": row["vessel_capacity"],
            "consumption": {
                "consumption_for_berthing": row["port_stay_bunker_consumption"],
                "consumption_for_idling": row["idling_bunker_consumption"],
                "consumption_for_pilot": row["pilot_inout_bunker_consumption"],
            },
        }
        for row in sorted(
            tables.get("bunker_consumption_port", []),
            key=lambda value: value["vessel_capacity"],
        )
    ]


def _build_bunker_consumption_sea(
    tables: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in tables.get("bunker_consumption_sea", []):
        grouped[row["vessel_capacity"]].append(row)

    serialized: list[dict[str, Any]] = []
    for capacity_teu in sorted(grouped):
        serialized.append(
            {
                "capacity_teu": capacity_teu,
                "consumption": [
                    {
                        "speed": row["sea_speed"],
                        "consumption_for_sailing": row["bunker_consumption"],
                    }
                    for row in sorted(grouped[capacity_teu], key=lambda value: value["sea_speed"])
                ],
            }
        )
    return serialized


def _build_bunker_price(
    tables: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    return [
        {
            "year_month": row["year_month"],
            "lane_code": row["lane_code"],
            "bunker_type": row["bunker_type"],
            "price": row["bunker_price"],
        }
        for row in sorted(
            tables.get("bunker_price", []),
            key=lambda value: (
                value["year_month"],
                value["lane_code"],
                value["bunker_type"],
            ),
        )
    ]


def _build_transshipment_cost(
    tables: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in tables.get("cost_ts_cost", []):
        grouped[(row["year_month"], row["lane_code"])].append(row)

    serialized: list[dict[str, Any]] = []
    for year_month, lane_code in sorted(grouped):
        serialized.append(
            {
                "year_month": year_month,
                "lane_code": lane_code,
                "ports": [
                    {
                        "port_code": row["port_code"],
                        "ts_cost": row["ts_cost"],
                    }
                    for row in sorted(
                        grouped[(year_month, lane_code)],
                        key=lambda value: value["port_code"],
                    )
                ],
            }
        )
    return serialized


def _build_distances(tables: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    rows = tables.get("cost_distance", [])
    by_leg: dict[tuple[str, str], dict[str, Any]] = {
        (row["from_port_code"], row["to_port_code"]): dict(row) for row in rows
    }

    for row in rows:
        reverse_key = (row["to_port_code"], row["from_port_code"])
        if reverse_key in by_leg:
            continue
        by_leg[reverse_key] = {
            "from_port_code": row["to_port_code"],
            "to_port_code": row["from_port_code"],
            "distance": row["distance"],
            "eca_distance": row["eca_distance"],
        }

    return sorted(
        by_leg.values(),
        key=lambda row: (row["from_port_code"], row["to_port_code"]),
    )


def _build_eca_ports(tables: dict[str, list[dict[str, Any]]]) -> set[str]:
    return {row["port_code"] for row in tables.get("cost_eca_port", [])}


def _build_canal_fee(tables: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    return sorted(
        tables.get("cost_canal_fee", []),
        key=lambda row: (row["vessel_code"], row["port_code"], row["direction"]),
    )


def _build_canal_direction(
    tables: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    return sorted(
        tables.get("cost_canal_direction", []),
        key=lambda row: (
            row["canal_port_code"],
            row["from_port_code"],
            row["to_port_code"],
        ),
    )


def _build_canal_passage_time(tables: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    return sorted(
        tables.get("canal_passage_time", []),
        key=lambda row: (row["port_code"], row["direction"]),
    )


def _build_opportunity_cost(
    tables: dict[str, list[dict[str, Any]]],
    planning_horizon_start: datetime,
    planning_horizon_end: datetime,
) -> list[dict[str, Any]]:
    opportunity_cost = tables.get("cost_lane_opportunity", [])
    # HERE
    valid_versions: set[tuple[str, str]] = set()
    versions_by_lane: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in sorted(
        tables.get("schedule_proforma", []),
        key=lambda value: (
            value["lane_code"],
            value["effective_from_date"],
            value["proforma_name"],
        ),
    ):
        versions_by_lane[row["lane_code"]].append(row)

    for lane_code, rows in versions_by_lane.items():
        for index, row in enumerate(rows):
            effective_to = rows[index + 1]["effective_from_date"] if index + 1 < len(rows) else planning_horizon_end
            if row["effective_from_date"] > planning_horizon_end:
                continue
            if effective_to < planning_horizon_start:
                continue
            valid_versions.add((lane_code, row["proforma_name"]))

    directions_by_lane: dict[str, set[str]] = defaultdict(set)
    for row in tables.get("schedule_proforma_detail", []):
        key = (row["lane_code"], row["proforma_name"])
        if key in valid_versions:
            directions_by_lane[row["lane_code"]].add(row["direction"])

    for lane_code, directions in directions_by_lane.items():
        if directions not in ({"E", "W"}, {"N", "S"}):
            raise ValueError(
                "ocam.preprocessing._build_opportunity_cost: "
                f"lane {lane_code!r} has invalid direction domain {sorted(directions)}. "
                "expected exactly ['E', 'W'] or ['N', 'S']."
            )

    available_keys = {(row["lane_code"], row["proforma_name"], row["direction"]) for row in opportunity_cost}
    missing_keys: list[tuple[str, str, str]] = []
    for lane_code, proforma_name in sorted(valid_versions):
        directions = directions_by_lane.get(lane_code, set())
        if not directions:
            raise ValueError(
                "ocam.preprocessing._build_opportunity_cost: "
                f"cannot determine direction domain for lane {lane_code!r}."
            )
        for direction in sorted(directions):
            key = (lane_code, proforma_name, direction)
            if key not in available_keys:
                missing_keys.append(key)

    if missing_keys:
        raise ValueError(
            "ocam.preprocessing._build_opportunity_cost: missing opportunity_cost rows for " f"{missing_keys}."
        )
    return opportunity_cost


def preprocess(bundle: InputBundle) -> InstanceData:
    try:
        tables = bundle.payload.get("tables", {})
        metadata = _build_metadata(bundle, tables)
        planning_horizon = metadata["planning_horizon"]

        return InstanceData(
            raw=bundle,
            scenario_name=metadata["scenario_name"],
            planning_horizon=planning_horizon,
            service_lanes=_build_service_lanes(
                tables,
                planning_horizon["start"],
                planning_horizon["end"],
            ),
            vessels=_build_vessels(tables),
            distances=_build_distances(tables),
            eca_ports=_build_eca_ports(tables),
            canal_fee=_build_canal_fee(tables),
            canal_direction=_build_canal_direction(tables),
            canal_passage_time=_build_canal_passage_time(tables),
            bunker_consumption_port=_build_bunker_consumption_port(tables),
            bunker_consumption_sea=_build_bunker_consumption_sea(tables),
            bunker_price=_build_bunker_price(tables),
            transshipment_cost=_build_transshipment_cost(tables),
            opportunity_cost=_build_opportunity_cost(
                tables,
                planning_horizon["start"],
                planning_horizon["end"],
            ),
        )
    except Exception as exc:
        raise RuntimeError(
            "ocam.preprocessing.preprocess: failed to build InstanceData: " f"{type(exc).__name__}: {exc}"
        ) from exc
