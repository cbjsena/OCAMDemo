from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ocam.models import AlgorithmResult, InputBundle, LowerBoundResult


@dataclass(frozen=True)
class ColumnSchema:
    data_type: str
    nullable: bool = False


INPUT_SCHEMA: dict[str, dict[str, ColumnSchema]] = {
    "bunker_consumption_port": {
        "vessel_capacity": ColumnSchema("int"),
        "port_stay_bunker_consumption": ColumnSchema("numeric"),
        "idling_bunker_consumption": ColumnSchema("numeric"),
        "pilot_inout_bunker_consumption": ColumnSchema("numeric"),
    },
    "bunker_consumption_sea": {
        "vessel_capacity": ColumnSchema("int"),
        "sea_speed": ColumnSchema("numeric"),
        "bunker_consumption": ColumnSchema("numeric"),
    },
    "bunker_price": {
        "year_month": ColumnSchema("string"),
        "trade_code": ColumnSchema("string"),
        "lane_code": ColumnSchema("string"),
        "bunker_type": ColumnSchema("string"),
        "bunker_price": ColumnSchema("numeric"),
    },
    "cost_canal_fee": {
        "vessel_code": ColumnSchema("string"),
        "direction": ColumnSchema("string"),
        "port_code": ColumnSchema("string"),
        "fee": ColumnSchema("numeric"),
    },
    "cost_canal_direction": {
        "from_port_code": ColumnSchema("string"),
        "canal_port_code": ColumnSchema("string"),
        "to_port_code": ColumnSchema("string"),
        "direction": ColumnSchema("string"),
    },
    "canal_passage_time": {
        "port_code": ColumnSchema("string"),
        "direction": ColumnSchema("string"),
        "passage_hours": ColumnSchema("numeric"),
    },
    "cost_distance": {
        "from_port_code": ColumnSchema("string"),
        "to_port_code": ColumnSchema("string"),
        "distance": ColumnSchema("int"),
        "eca_distance": ColumnSchema("int"),
    },
    "cost_eca_port": {
        "port_code": ColumnSchema("string"),
    },
    "cost_ts_cost": {
        "year_month": ColumnSchema("string"),
        "lane_code": ColumnSchema("string"),
        "port_code": ColumnSchema("string"),
        "ts_cost": ColumnSchema("int"),
    },
    "cost_lane_opportunity": {
        "lane_code": ColumnSchema("string"),
        "proforma_name": ColumnSchema("string"),
        "direction": ColumnSchema("string"),
        "opportunity_cost": ColumnSchema("numeric"),
    },
    "schedule_long_range": {
        "proforma_name": ColumnSchema("string"),
        "lane_code": ColumnSchema("string"),
        "vessel_code": ColumnSchema("string"),
        "voyage_number": ColumnSchema("string"),
        "direction": ColumnSchema("string"),
        "port_code": ColumnSchema("string"),
        "calling_port_indicator": ColumnSchema("string"),
        "calling_port_seq": ColumnSchema("int"),
        "start_port_berthing_year_week": ColumnSchema("string"),
        "schedule_change_status_code": ColumnSchema("string"),
        "eta": ColumnSchema("datetime"),
        "etb": ColumnSchema("datetime"),
        "etd": ColumnSchema("datetime"),
        "terminal_code": ColumnSchema("string"),
    },
    "vessel_current_assignment": {
        "vessel_code": ColumnSchema("string"),
        "lane_code": ColumnSchema("string"),
        "proforma_name": ColumnSchema("string"),
        "vessel_position": ColumnSchema("int"),
    },  # LRS 대체용 정보 - 생성된 인스턴스의 경우에 사용
    "schedule_proforma": {
        "lane_code": ColumnSchema("string"),
        "proforma_name": ColumnSchema("string"),
        "duration": ColumnSchema("numeric"),
        "declared_capacity": ColumnSchema("string"),
        "declared_count": ColumnSchema("int"),
        "own_vessel_count": ColumnSchema("int"),
        "effective_from_date": ColumnSchema("datetime"),
    },
    "schedule_cascading_vessel_position": {
        "lane_code": ColumnSchema("string"),
        "proforma_name": ColumnSchema("string"),
        "vessel_position": ColumnSchema("int"),
        "vessel_position_date": ColumnSchema("datetime"),
    },
    "schedule_proforma_detail": {
        "lane_code": ColumnSchema("string"),
        "proforma_name": ColumnSchema("string"),
        "direction": ColumnSchema("string"),
        "port_code": ColumnSchema("string"),
        "calling_port_indicator": ColumnSchema("string"),
        "calling_port_seq": ColumnSchema("int"),
        "turn_port_info_code": ColumnSchema("string", nullable=True),
        "pilot_in_hours": ColumnSchema("numeric", nullable=True),
        "etb_day_number": ColumnSchema("int"),
        "etb_day_code": ColumnSchema("string"),
        "etb_day_time": ColumnSchema("string"),
        "actual_work_hours": ColumnSchema("numeric", nullable=True),
        "etd_day_number": ColumnSchema("int", nullable=True),
        "etd_day_code": ColumnSchema("string", nullable=True),
        "etd_day_time": ColumnSchema("string", nullable=True),
        "pilot_out_hours": ColumnSchema("numeric", nullable=True),
        "link_distance": ColumnSchema("int", nullable=True),
        "link_eca_distance": ColumnSchema("int", nullable=True),
        "link_speed": ColumnSchema("numeric", nullable=True),
        "sea_time_hours": ColumnSchema("numeric", nullable=True),
        "terminal_code": ColumnSchema("string"),
    },
    "vessel_capacity": {
        "trade_code": ColumnSchema("string"),
        "lane_code": ColumnSchema("string"),
        "vessel_code": ColumnSchema("string"),
        "direction": ColumnSchema("string"),
        "capacity": ColumnSchema("int"),
        "reefer_capacity": ColumnSchema("int"),
    },
    "vessel_charter_cost": {
        "vessel_code": ColumnSchema("string"),
        "hire_from_date": ColumnSchema("datetime"),
        "hire_to_date": ColumnSchema("datetime"),
        "hire_rate": ColumnSchema("numeric"),
    },
    "vessel_info": {
        "vessel_code": ColumnSchema("string"),
        "vessel_name": ColumnSchema("string"),
        "own_yn": ColumnSchema("string"),
        "delivery_port_code": ColumnSchema("string", nullable=True),
        "delivery_date": ColumnSchema("datetime", nullable=True),
        "built_port_code": ColumnSchema("string", nullable=True),
        "built_date": ColumnSchema("string", nullable=True),
        "redelivery_port_code": ColumnSchema("string", nullable=True),
        "redelivery_date": ColumnSchema("datetime", nullable=True),
        "next_dock_port_code": ColumnSchema("string", nullable=True),
        "next_dock_in_date": ColumnSchema("datetime", nullable=True),
        "next_dock_out_date": ColumnSchema("datetime", nullable=True),
    },
    "week_period": {
        "year": ColumnSchema("int"),
        "week": ColumnSchema("int"),
        "month": ColumnSchema("int"),
        "week_start_date": ColumnSchema("datetime"),
        "week_end_date": ColumnSchema("datetime"),
    },
}

DATETIME_FORMATS = (
    "%Y/%m/%d %H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y/%m/%d",
    "%Y-%m-%d",
)
ENUM_VALUES: dict[str, set[str]] = {
    "direction": {"W", "E", "S", "N"},
    "bunker_type": {"LSFO", "MGO"},
    "own_yn": {"O", "C"},
}
PRIMARY_KEYS: dict[str, tuple[str, ...]] = {
    "bunker_consumption_port": ("vessel_capacity",),
    "bunker_consumption_sea": ("vessel_capacity", "sea_speed"),
    "bunker_price": ("year_month", "trade_code", "lane_code", "bunker_type"),
    "cost_canal_fee": ("vessel_code", "direction", "port_code"),
    # 운하 방향은 A->C->B route마다 하나로 결정되어야 하므로 중복 입력을 허용하지 않는다.
    "cost_canal_direction": ("from_port_code", "canal_port_code", "to_port_code"),
    "canal_passage_time": ("port_code", "direction"),
    "cost_distance": ("from_port_code", "to_port_code"),
    "cost_eca_port": ("port_code",),
    "cost_ts_cost": ("year_month", "lane_code", "port_code"),
    "schedule_long_range": (
        "proforma_name",
        "lane_code",
        "vessel_code",
        "voyage_number",
        "direction",
        "port_code",
        "calling_port_indicator",
    ),
    "vessel_current_assignment": ("vessel_code",),
    "schedule_proforma": ("lane_code", "proforma_name"),
    "schedule_cascading_vessel_position": (
        "lane_code",
        "proforma_name",
        "vessel_position",
    ),
    "schedule_proforma_detail": (
        "lane_code",
        "proforma_name",
        "direction",
        "port_code",
        "calling_port_indicator",
    ),
    "vessel_capacity": ("trade_code", "lane_code", "vessel_code", "direction"),
    "vessel_charter_cost": ("vessel_code", "hire_from_date"),
    "vessel_info": ("vessel_code",),
    "week_period": ("week_start_date",),
}
FOREIGN_KEYS: tuple[tuple[str, tuple[str, ...], str, tuple[str, ...]], ...] = (
    ("cost_ts_cost", ("lane_code",), "schedule_proforma", ("lane_code",)),
    (
        "schedule_cascading_vessel_position",
        ("lane_code", "proforma_name"),
        "schedule_proforma",
        ("lane_code", "proforma_name"),
    ),
    (
        "schedule_proforma_detail",
        ("lane_code", "proforma_name"),
        "schedule_proforma",
        ("lane_code", "proforma_name"),
    ),
    ("schedule_long_range", ("vessel_code",), "vessel_info", ("vessel_code",)),
    ("schedule_long_range", ("lane_code",), "schedule_proforma", ("lane_code",)),
    (
        "vessel_current_assignment",
        ("vessel_code",),
        "vessel_info",
        ("vessel_code",),
    ),
    (
        "vessel_current_assignment",
        ("lane_code", "proforma_name"),
        "schedule_proforma",
        ("lane_code", "proforma_name"),
    ),
)

ALTERNATIVE_TABLE_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"schedule_long_range", "vessel_current_assignment"}),
)


def _validate_primary_keys(tables: dict[str, list[dict[str, Any]]]) -> None:
    for table_name, key_columns in PRIMARY_KEYS.items():
        if table_name not in tables:
            continue
        seen: set[tuple[Any, ...]] = set()
        for row_number, row in enumerate(tables[table_name], start=2):
            key = tuple(row[column_name] for column_name in key_columns)
            if key in seen:
                raise ValueError(
                    "ocam.io._validate_primary_keys: " f"duplicate PK in {table_name} at row {row_number}: {key}"
                )
            seen.add(key)


def _validate_enums(tables: dict[str, list[dict[str, Any]]]) -> None:
    for table_name, rows in tables.items():
        for row_number, row in enumerate(rows, start=2):
            for column_name in ("direction", "bunker_type", "own_yn"):
                if column_name not in row or row[column_name] is None:
                    continue
                if row[column_name] not in ENUM_VALUES[column_name]:
                    raise ValueError(
                        "ocam.io._validate_enums: "
                        f"{table_name}.{column_name} at row {row_number} has invalid "
                        f"value {row[column_name]!r}. allowed={sorted(ENUM_VALUES[column_name])}"
                    )


def _validate_year_month(tables: dict[str, list[dict[str, Any]]]) -> None:
    for table_name, rows in tables.items():
        if "year_month" not in INPUT_SCHEMA[table_name]:
            continue
        for row_number, row in enumerate(rows, start=2):
            value = row["year_month"]
            if value is None:
                continue
            if len(value) != 6 or not value.isdigit():
                raise ValueError(
                    "ocam.io._validate_year_month: " f"{table_name}.year_month at row {row_number} must match YYYYMM."
                )
            month = int(value[4:6])
            if not 1 <= month <= 12:
                raise ValueError(
                    "ocam.io._validate_year_month: "
                    f"{table_name}.year_month at row {row_number} has invalid month {month}."
                )


def _validate_date_order(tables: dict[str, list[dict[str, Any]]]) -> None:
    def ensure_order(
        table_name: str,
        row_number: int,
        row: dict[str, Any],
        earlier: str,
        later: str,
    ) -> None:
        early = row.get(earlier)
        late = row.get(later)
        if early is None or late is None:
            return
        if early > late:
            raise ValueError(
                "ocam.io._validate_date_order: " f"{table_name} row {row_number} violates {earlier} <= {later}."
            )

    for row_number, row in enumerate(tables.get("vessel_charter_cost", []), start=2):
        ensure_order("vessel_charter_cost", row_number, row, "hire_from_date", "hire_to_date")
    for row_number, row in enumerate(tables.get("vessel_info", []), start=2):
        ensure_order("vessel_info", row_number, row, "delivery_date", "redelivery_date")
        ensure_order("vessel_info", row_number, row, "next_dock_in_date", "next_dock_out_date")
    for row_number, row in enumerate(tables.get("schedule_long_range", []), start=2):
        ensure_order("schedule_long_range", row_number, row, "eta", "etb")
        ensure_order("schedule_long_range", row_number, row, "etb", "etd")


def _validate_foreign_keys(tables: dict[str, list[dict[str, Any]]]) -> None:
    for source_table, source_columns, target_table, target_columns in FOREIGN_KEYS:
        if source_table not in tables or target_table not in tables:
            continue
        target_keys = {tuple(row[column_name] for column_name in target_columns) for row in tables[target_table]}
        for row_number, row in enumerate(tables[source_table], start=2):
            source_key = tuple(row[column_name] for column_name in source_columns)
            if any(value is None for value in source_key):
                continue
            if source_key not in target_keys:
                raise ValueError(
                    "ocam.io._validate_foreign_keys: "
                    f"{source_table} row {row_number} references missing "
                    f"{target_table} key {source_key}."
                )


def _validate_non_empty_tables(tables: dict[str, list[dict[str, Any]]]) -> None:
    for table_name, rows in tables.items():
        if not rows:
            raise ValueError(
                f"ocam.io._validate_non_empty_tables: {table_name}.csv must contain at least one data row."
            )


def _validate_week_period(tables: dict[str, list[dict[str, Any]]]) -> None:
    rows = sorted(tables["week_period"], key=lambda row: row["week_start_date"])
    seen_year_week: set[tuple[int, int]] = set()

    for row_number, row in enumerate(rows, start=2):
        pair = (row["year"], row["week"])
        if pair in seen_year_week:
            raise ValueError("ocam.io._validate_week_period: " f"duplicate (year, week) pair found: {pair}.")
        seen_year_week.add(pair)

        if row["week_start_date"] > row["week_end_date"]:
            raise ValueError(
                "ocam.io._validate_week_period: "
                f"week_period row {row_number} has week_start_date after week_end_date."
            )

    for previous, current in zip(rows, rows[1:]):
        if current["week_start_date"] <= previous["week_end_date"]:
            raise ValueError("ocam.io._validate_week_period: week_period ranges must not overlap.")
        if current["week_start_date"].date() != previous["week_end_date"].date() + timedelta(days=1):
            raise ValueError("ocam.io._validate_week_period: week_period ranges must be continuous by date.")


def _parse_value(
    table_name: str,
    column_name: str,
    raw_value: str,
    schema: ColumnSchema,
    row_number: int,
) -> Any:
    value = raw_value.strip()
    if value == "":
        if schema.nullable:
            return None
        raise ValueError("ocam.io._parse_value: " f"{table_name}.{column_name} at row {row_number} cannot be null.")

    try:
        if schema.data_type == "string":
            return value
        if schema.data_type == "int":
            return int(value)
        if schema.data_type == "numeric":
            return float(value)
        if schema.data_type == "datetime":
            for datetime_format in DATETIME_FORMATS:
                try:
                    return datetime.strptime(value, datetime_format)
                except ValueError:
                    continue
            raise ValueError
    except ValueError as exc:
        raise ValueError(
            "ocam.io._parse_value: "
            f"{table_name}.{column_name} at row {row_number} "
            f"must be {schema.data_type}, got {raw_value!r}."
        ) from exc

    raise ValueError(f"ocam.io._parse_value: unsupported schema type {schema.data_type!r}.")


def _validate_header(table_name: str, fieldnames: list[str] | None) -> list[str]:
    if fieldnames is None:
        raise ValueError(f"ocam.io._validate_header: {table_name}.csv is missing a header row.")

    actual = [field.strip() for field in fieldnames]
    expected = list(INPUT_SCHEMA[table_name].keys())
    if actual != expected:
        raise ValueError(
            "ocam.io._validate_header: " f"{table_name}.csv header mismatch. expected={expected}, actual={actual}"
        )
    return actual


def _load_table(table_name: str, file_path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with file_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = _validate_header(table_name, reader.fieldnames)

        for row_number, row in enumerate(reader, start=2):
            typed_row: dict[str, Any] = {}
            for column_name in fieldnames:
                typed_row[column_name] = _parse_value(
                    table_name=table_name,
                    column_name=column_name,
                    raw_value=row.get(column_name, ""),
                    schema=INPUT_SCHEMA[table_name][column_name],
                    row_number=row_number,
                )
            rows.append(typed_row)
    return rows


def _load_metadata(file_path: Path) -> dict[str, Any]:
    with file_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != ["key", "value"]:
            raise ValueError(
                "ocam.io._load_metadata: metadata.csv header must be ['key', 'value'], " f"got {reader.fieldnames!r}."
            )

        values = {row["key"]: row["value"] for row in reader}

    metadata: dict[str, Any] = {}
    if "scenario_name" in values:
        metadata["scenario_name"] = values["scenario_name"]

    planning_start = values.get("planning_horizon_start")
    planning_end = values.get("planning_horizon_end")
    if planning_start is not None or planning_end is not None:
        metadata["planning_horizon"] = {
            "start": planning_start,
            "end": planning_end,
        }

    return metadata


def load_inputs(input_dir: Path) -> InputBundle:
    """
    Collect and validate input CSV files against the engine input schema.

    Known CSV files are parsed into typed rows and stored under
    InputBundle.payload["tables"].
    """

    input_dir = input_dir.resolve()
    if not input_dir.exists():
        raise FileNotFoundError(f"ocam.io.load_inputs: input directory does not exist: {input_dir}")

    files = {
        path.name: path
        for path in sorted(input_dir.iterdir())
        if path.is_file() and not path.name.startswith(".") and path.suffix.lower() == ".csv"
    }

    alternative_table_names = set().union(*ALTERNATIVE_TABLE_GROUPS)
    expected_file_names = {
        f"{table_name}.csv" for table_name in INPUT_SCHEMA if table_name not in alternative_table_names
    }
    actual_file_names = set(files.keys())
    missing_file_names = sorted(expected_file_names - actual_file_names)
    for alternative_group in ALTERNATIVE_TABLE_GROUPS:
        if not any(f"{table_name}.csv" in actual_file_names for table_name in alternative_group):
            missing_file_names.append(
                "one of " + ", ".join(f"{table_name}.csv" for table_name in sorted(alternative_group))
            )
    if missing_file_names:
        raise FileNotFoundError("ocam.io.load_inputs: missing required input CSV files: " f"{missing_file_names}")

    tables: dict[str, list[dict[str, Any]]] = {}
    metadata: dict[str, Any] = {}
    for file_name, file_path in files.items():
        table_name = file_path.stem
        if table_name == "metadata":
            metadata = _load_metadata(file_path)
            continue
        if table_name not in INPUT_SCHEMA:
            raise ValueError(
                "ocam.io.load_inputs: "
                f"unknown input table file {file_name!r}. expected one of "
                f"{sorted(INPUT_SCHEMA.keys())}."
            )
        tables[table_name] = _load_table(table_name, file_path)

    _validate_non_empty_tables(tables)
    _validate_primary_keys(tables)
    _validate_enums(tables)
    _validate_year_month(tables)
    _validate_date_order(tables)
    _validate_foreign_keys(tables)
    _validate_week_period(tables)

    return InputBundle(
        input_dir=input_dir,
        files=files,
        payload={"tables": tables, "metadata": metadata},
    )


def _serialize_scalar(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    if isinstance(value, (datetime, timedelta)):
        return str(value)
    return json.dumps(value, ensure_ascii=True, default=str)


def _algorithm_file_prefix(algorithm_name: str) -> str:
    return algorithm_name.replace("/", "_")


def build_output_texts(result: AlgorithmResult) -> dict[str, str]:
    file_prefix = _algorithm_file_prefix(result.algorithm)
    solution_text = json.dumps(result.solution.to_dict(), indent=2, ensure_ascii=True, default=str)

    metadata_rows: list[dict[str, str]] = [
        {"field": "algorithm", "value": result.algorithm},
        {"field": "status", "value": _serialize_scalar(result.status)},
        {"field": "solution_file", "value": f"{file_prefix}_solution.json"},
        {"field": "logs_file", "value": f"{file_prefix}_logs.txt"},
    ]
    if isinstance(result.objective, dict):
        metadata_rows.extend(
            {"field": key, "value": _serialize_scalar(value)} for key, value in sorted(result.objective.items())
        )
    metadata_rows.extend(
        {"field": key, "value": _serialize_scalar(value)} for key, value in sorted(result.metadata.items())
    )

    metadata_buffer = io.StringIO()
    writer = csv.DictWriter(metadata_buffer, fieldnames=("field", "value"))
    writer.writeheader()
    writer.writerows(metadata_rows)

    return {
        "solution_json": solution_text,
        "metadata_csv": metadata_buffer.getvalue(),
        "logs_txt": result.logs,
    }


def build_lower_bound_output_texts(result: LowerBoundResult) -> dict[str, str]:
    file_prefix = _algorithm_file_prefix(result.algorithm)
    payload = {
        "format": "ocam_lower_bound_result_v1",
        "algorithm": result.algorithm,
        "status": result.status,
        "lower_bound": result.lower_bound,
        "objective": result.objective,
        "metadata": result.metadata,
    }
    lower_bound_text = json.dumps(payload, indent=2, ensure_ascii=True, default=str)

    metadata_rows: list[dict[str, str]] = [
        {"field": "algorithm", "value": result.algorithm},
        {"field": "status", "value": _serialize_scalar(result.status)},
        {"field": "lower_bound_file", "value": f"{file_prefix}_lower_bound.json"},
        {"field": "logs_file", "value": f"{file_prefix}_lower_bound_logs.txt"},
    ]
    if result.lower_bound is not None:
        metadata_rows.append({"field": "lower_bound", "value": _serialize_scalar(result.lower_bound)})
    if isinstance(result.objective, dict):
        for key, value in sorted(result.objective.items()):
            if key == "lower_bound":
                continue
            field = {
                "status": "model_status",
                "status_name": "model_status_name",
            }.get(key, key)
            metadata_rows.append({"field": field, "value": _serialize_scalar(value)})
    metadata_rows.extend(
        {"field": key, "value": _serialize_scalar(value)} for key, value in sorted(result.metadata.items())
    )

    metadata_buffer = io.StringIO()
    writer = csv.DictWriter(metadata_buffer, fieldnames=("field", "value"))
    writer.writeheader()
    writer.writerows(metadata_rows)

    return {
        "lower_bound_json": lower_bound_text,
        "metadata_csv": metadata_buffer.getvalue(),
        "logs_txt": result.logs,
    }


def write_outputs(outputs_dir: Path, result: AlgorithmResult) -> Path:
    """
    Persist run outputs in researcher-friendly files.

    - solution: JSON
    - metadata: CSV
    - logs: TXT
    """

    outputs_dir.mkdir(parents=True, exist_ok=True)
    file_prefix = _algorithm_file_prefix(result.algorithm)
    solution_path = outputs_dir / f"{file_prefix}_solution.json"
    metadata_path = outputs_dir / f"{file_prefix}_metadata.csv"
    logs_path = outputs_dir / f"{file_prefix}_logs.txt"

    texts = build_output_texts(result)
    solution_path.parent.mkdir(parents=True, exist_ok=True)
    solution_path.write_text(texts["solution_json"], encoding="utf-8")
    with metadata_path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(texts["metadata_csv"])
    logs_path.write_text(texts["logs_txt"], encoding="utf-8")
    return solution_path


def write_lower_bound_outputs(outputs_dir: Path, result: LowerBoundResult) -> Path:
    """Persist lower-bound run outputs without creating a solution artifact."""

    outputs_dir.mkdir(parents=True, exist_ok=True)
    file_prefix = _algorithm_file_prefix(result.algorithm)
    lower_bound_path = outputs_dir / f"{file_prefix}_lower_bound.json"
    metadata_path = outputs_dir / f"{file_prefix}_lower_bound_metadata.csv"
    logs_path = outputs_dir / f"{file_prefix}_lower_bound_logs.txt"

    texts = build_lower_bound_output_texts(result)
    lower_bound_path.write_text(texts["lower_bound_json"], encoding="utf-8")
    with metadata_path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(texts["metadata_csv"])
    logs_path.write_text(texts["logs_txt"], encoding="utf-8")
    return lower_bound_path
