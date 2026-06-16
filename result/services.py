# result/services.py
"""
outputs 폴더를 스캔하여 결과 목록/상세를 반환.

OCAM 원소스 출력 규칙:
  outputs/<YYMMDD_HHMM>/
      <researcher>_<algo>_solution.json
      <researcher>_<algo>_metadata.csv
      <researcher>_<algo>_logs.txt
"""

import csv
import json
from pathlib import Path

from django.conf import settings

from common.utils.date_utils import parse_datetime_folder

_OBJECTIVE_KEYS = [
    "total_cost",
    "bunker_cost",
    "canal_fee_cost",
    "transshipment_cost",
    "opportunity_cost",
    "charter_cost",
    "vessel_cost",
    "port_cost",
    "penalty_cost",
    "num_virtual_vessels",
    "num_virtual_vessels_used",
    "actual_port_calls",
    "total_port_calls",
    "service_lane_coverage_rate",
    "vessel_service_days",
    "vessel_available_days",
    "vessel_drydock_days",
    "vessel_utilization_rate",
    "slot_utilization_rate",
    "slot_utilization_required_teu_days",
    "slot_utilization_deployed_teu_days",
    "bunker_cost_by_inlane_sail",
    "bunker_cost_by_port_stay",
    "bunker_cost_by_outlane_sail",
    "transshipment_count",
]

_SKIP_FOLDERS = {"leaderboard", "lower_bounds"}


def get_outputs_dir() -> Path:
    return Path(settings.OUTPUTS_DIR)


# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────


def _read_output_metadata_csv(filepath: Path) -> dict:
    """OCAM 결과 metadata.csv (field,value) 를 dict 로 변환."""
    metadata: dict[str, str] = {}
    try:
        with open(filepath, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            for i, row in enumerate(reader):
                if not row:
                    continue
                key = row[0].strip()
                value = ",".join(row[1:]).strip()
                if i == 0 and key.lower() in ("field", "key") and value.lower() == "value":
                    continue
                if key == "status" and "status" in metadata:
                    metadata["model_status"] = value
                    continue
                if key:
                    metadata[key] = value
    except (OSError, csv.Error):
        pass
    return metadata


def _parse_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _compute_objective_parsed(metadata: dict) -> dict | None:
    """metadata 의 숫자 필드에서 objective dict 를 구성."""
    result: dict[str, float] = {}
    objective_raw = metadata.get("objective")
    if objective_raw:
        try:
            parsed = json.loads(objective_raw)
            if isinstance(parsed, dict):
                result.update(
                    {k: float(v) for k, v in parsed.items() if isinstance(v, (int, float))}
                )
        except (json.JSONDecodeError, ValueError):
            try:
                result["total_cost"] = float(objective_raw)
            except (ValueError, TypeError):
                pass
    all_keys = list(_OBJECTIVE_KEYS) + [
        k for k in metadata if k.endswith("_cost") or k.endswith("_costs")
    ]
    for key in all_keys:
        if key in result:
            continue
        v = _parse_float(metadata.get(key))
        if v is not None:
            result[key] = v
    return result if result else None


def _scan_run_dir(folder_path: Path) -> tuple[list[dict], list[dict]]:
    """한 run 폴더에서 알고리즘/하한 결과를 스캔한다."""
    algorithms: list[dict] = []
    lower_bounds: list[dict] = []

    meta_files = sorted(folder_path.glob("*_metadata.csv"))
    has_ocam = bool(meta_files)

    if has_ocam:
        for meta_file in meta_files:
            stem = meta_file.stem
            if not stem.endswith("_metadata"):
                continue
            algo_key = stem[: -len("_metadata")]

            lb_json_path = folder_path / f"{algo_key}_lower_bound.json"
            if lb_json_path.exists():
                metadata = _read_output_metadata_csv(meta_file)
                logs = ""
                logs_path = folder_path / f"{algo_key}_lower_bound_logs.txt"
                if logs_path.exists():
                    try:
                        logs = logs_path.read_text(encoding="utf-8")
                    except OSError:
                        pass
                lb_data: dict = {}
                try:
                    lb_data = json.loads(lb_json_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    pass
                display_name = metadata.get("algorithm") or algo_key.replace("_", "/", 1)
                lower_bounds.append(
                    {
                        "key": algo_key,
                        "displayName": display_name,
                        "status": metadata.get("status") or lb_data.get("status") or "",
                        "lowerBound": _parse_float(
                            metadata.get("lower_bound") or str(lb_data.get("lower_bound", ""))
                        ),
                        "objective": lb_data.get("objective"),
                        "metadata": {
                            **metadata,
                            "objectiveParsed": _compute_objective_parsed(metadata),
                        },
                        "logs": logs,
                        "files": {},
                    }
                )
                continue

            metadata = _read_output_metadata_csv(meta_file)
            display_name = metadata.get("algorithm") or algo_key.replace("_", "/", 1)

            solution = None
            solution_path = folder_path / f"{algo_key}_solution.json"
            if solution_path.exists():
                try:
                    solution = json.loads(solution_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    pass

            logs = ""
            logs_path = folder_path / f"{algo_key}_logs.txt"
            if logs_path.exists():
                try:
                    logs = logs_path.read_text(encoding="utf-8")
                except OSError:
                    pass

            algorithms.append(
                {
                    "key": algo_key,
                    "displayName": display_name,
                    "metadata": {
                        **metadata,
                        "objectiveParsed": _compute_objective_parsed(metadata),
                    },
                    "solution": solution,
                    "logs": logs,
                    "files": {},
                }
            )
    else:
        for json_file in sorted(folder_path.glob("*.json")):
            if "_solution" in json_file.name or "_lower_bound" in json_file.name:
                continue
            try:
                data = json.loads(json_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            algo_key = json_file.stem
            metadata = {
                "algorithm": data.get("algorithm_name", algo_key),
                "status": data.get("status", ""),
                "total_cost": str(data.get("objective_value") or ""),
                "elapsed_seconds": str(data.get("execution_time") or ""),
            }
            algorithms.append(
                {
                    "key": algo_key,
                    "displayName": data.get("algorithm_name", algo_key),
                    "metadata": {
                        **metadata,
                        "objectiveParsed": _compute_objective_parsed(metadata),
                    },
                    "solution": data.get("solution"),
                    "logs": "",
                    "files": {},
                }
            )

    return algorithms, lower_bounds


# ──────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────


def discover_results() -> list[dict]:
    """
    outputs 폴더를 스캔하여 결과 목록을 반환.
    각 항목은 (folder, algo_key) 쌍이며 result_list 테이블 표시용.
    """
    root = get_outputs_dir()
    if not root.exists():
        return []

    results = []
    for folder in sorted(root.iterdir(), reverse=True):
        if not folder.is_dir() or folder.name in _SKIP_FOLDERS:
            continue
        dt = parse_datetime_folder(folder.name)
        if dt is None:
            continue

        algos, lbs = _scan_run_dir(folder)
        for algo in algos:
            meta = algo["metadata"]
            results.append(
                {
                    "folder": folder.name,
                    "algo_key": algo["key"],
                    "filename": f"{algo['key']}_solution.json",
                    "datetime": dt,
                    "algorithm_name": algo["displayName"],
                    "instance_name": meta.get("input_dir_name") or meta.get("instance_name") or "",
                    "status": meta.get("status", ""),
                    "objective_value": _parse_float(meta.get("total_cost")),
                    "execution_time": _parse_float(meta.get("elapsed_seconds")),
                    "validation_status": meta.get("validation_status", ""),
                }
            )
        for lb in lbs:
            meta = lb["metadata"]
            results.append(
                {
                    "folder": folder.name,
                    "algo_key": lb["key"],
                    "filename": f"{lb['key']}_lower_bound.json",
                    "datetime": dt,
                    "algorithm_name": lb["displayName"],
                    "instance_name": meta.get("input_dir_name") or "",
                    "status": "LB",
                    "objective_value": lb.get("lowerBound"),
                    "execution_time": _parse_float(meta.get("elapsed_seconds")),
                    "validation_status": "",
                }
            )

    return results


def get_run_data_for_visualizer(folder: str) -> list[dict]:
    """
    특정 출력 폴더의 모든 결과를 OCAM 비주얼라이저 호환 형태로 반환.
    Returns: [{ name, algorithms: [...], lowerBounds: [...] }]
    """
    root = get_outputs_dir()
    folder_path = root / folder
    if not folder_path.exists() or not folder_path.is_dir():
        return []

    algos, lbs = _scan_run_dir(folder_path)
    if not algos and not lbs:
        return []

    return [{"name": folder, "algorithms": algos, "lowerBounds": lbs}]


def get_result_detail(folder: str, filename: str) -> dict | None:
    """특정 결과 파일의 상세 내용을 반환 (legacy 호환)."""
    root = get_outputs_dir()
    file_path = root / folder / filename
    if not file_path.exists():
        return None
    try:
        return json.loads(file_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
