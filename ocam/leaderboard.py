from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from ocam.io import build_lower_bound_output_texts, build_output_texts
from ocam.models import AlgorithmResult, LowerBoundResult


def _extract_total_cost(result: AlgorithmResult) -> float | None:
    objective = result.objective
    if not isinstance(objective, dict):
        return None
    total_cost = objective.get("total_cost")
    if isinstance(total_cost, bool) or not isinstance(total_cost, (int, float)):
        return None
    return float(total_cost)


def _objective_rank(objective: object) -> tuple[int, float] | None:
    if not isinstance(objective, dict):
        return None

    total_cost = objective.get("total_cost")
    if isinstance(total_cost, bool) or not isinstance(total_cost, (int, float)):
        return None

    num_virtual = objective.get("num_virtual_vessels", 0)
    if isinstance(num_virtual, bool) or not isinstance(num_virtual, (int, float)):
        num_virtual = 0

    return (int(num_virtual), float(total_cost))


def _scenario_dir_name(result: AlgorithmResult | LowerBoundResult) -> str:
    scenario_name = str(result.metadata.get("scenario_name") or "unknown_scenario").strip()
    return (
        "".join(char if char.isalnum() or char in {"-", "_", "."} else "_" for char in scenario_name)
        or "unknown_scenario"
    )


def _existing_objective_rank(entry_path: Path) -> tuple[int, float] | None:
    if not entry_path.is_file():
        return None

    try:
        payload = json.loads(entry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    return _objective_rank(payload.get("objective"))


def _read_payload(entry_path: Path) -> dict[str, object] | None:
    if not entry_path.is_file():
        return None
    try:
        payload = json.loads(entry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _runtime_sample(result: AlgorithmResult, run_outputs_dir: Path) -> dict[str, object]:
    elapsed_seconds = result.metadata.get("elapsed_seconds")
    sample = {
        "run": run_outputs_dir.name,
        "recorded_at": datetime.now().isoformat(timespec="seconds"),
    }
    if isinstance(elapsed_seconds, (int, float)) and not isinstance(elapsed_seconds, bool):
        sample["elapsed_seconds"] = float(elapsed_seconds)
    if result.metadata.get("outputs_instance_dir"):
        sample["outputs_instance_dir"] = result.metadata["outputs_instance_dir"]
    return sample


def _append_runtime_history(entry_path: Path, run_outputs_dir: Path, result: AlgorithmResult) -> Path:
    payload = _read_payload(entry_path)
    if payload is None:
        return entry_path

    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
        payload["metadata"] = metadata

    history = metadata.get("leaderboard_runtime_history")
    if not isinstance(history, list):
        history = []
        elapsed_seconds = metadata.get("elapsed_seconds")
        source_run = metadata.get("leaderboard_source_run") or metadata.get("leaderboard_source_dir")
        if isinstance(source_run, str) and isinstance(elapsed_seconds, (int, float)) and not isinstance(
            elapsed_seconds, bool
        ):
            history.append(
                {
                    "run": source_run,
                    "elapsed_seconds": float(elapsed_seconds),
                    "recorded_at": str(metadata.get("leaderboard_updated_at") or ""),
                }
            )
    sample = _runtime_sample(result, run_outputs_dir)
    if not any(
        isinstance(item, dict)
        and item.get("run") == sample.get("run")
        and item.get("elapsed_seconds") == sample.get("elapsed_seconds")
        for item in history
    ):
        history.append(sample)

    metadata["leaderboard_runtime_history"] = history
    metadata["leaderboard_updated_at"] = datetime.now().isoformat(timespec="seconds")
    metadata["leaderboard_last_equal_source_run"] = run_outputs_dir.name
    entry_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True, default=str), encoding="utf-8")
    return entry_path


def update_leaderboard(leaderboard_dir: Path, run_outputs_dir: Path, result: AlgorithmResult) -> Path | None:
    objective_rank = _objective_rank(result.objective)
    if result.status != "ok" or objective_rank is None:
        return None

    scenario_dir = leaderboard_dir / _scenario_dir_name(result)
    entry_path = scenario_dir / f"{result.algorithm.replace('/', '_')}.json"
    existing_objective_rank = _existing_objective_rank(entry_path)
    if existing_objective_rank is not None and existing_objective_rank < objective_rank:
        return None
    if existing_objective_rank is not None and existing_objective_rank == objective_rank:
        return _append_runtime_history(entry_path, run_outputs_dir, result)

    leaderboard_metadata = dict(result.metadata)
    leaderboard_metadata["leaderboard_entry_type"] = "best"
    leaderboard_metadata["leaderboard_updated_at"] = datetime.now().isoformat(timespec="seconds")
    leaderboard_metadata["leaderboard_source_run"] = run_outputs_dir.name
    leaderboard_metadata["leaderboard_source_dir"] = run_outputs_dir.name
    leaderboard_metadata["leaderboard_runtime_history"] = [_runtime_sample(result, run_outputs_dir)]

    leaderboard_result = AlgorithmResult(
        algorithm=result.algorithm,
        status=result.status,
        objective=result.objective,
        solution=result.solution,
        logs=result.logs,
        metadata=leaderboard_metadata,
    )
    scenario_dir.mkdir(parents=True, exist_ok=True)

    texts = build_output_texts(leaderboard_result)
    payload = {
        "format": "ocam_leaderboard_entry_v1",
        "algorithm": leaderboard_result.algorithm,
        "status": leaderboard_result.status,
        "objective": leaderboard_result.objective,
        "metadata": leaderboard_result.metadata,
        "solution": leaderboard_result.solution.to_dict(),
        "logs": leaderboard_result.logs,
        "artifacts": texts,
    }
    entry_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True, default=str), encoding="utf-8")

    legacy_prefix = result.algorithm.replace("/", "_")
    for suffix in ("_solution.json", "_metadata.csv", "_logs.txt"):
        legacy_path = scenario_dir / f"{legacy_prefix}{suffix}"
        if legacy_path.exists():
            legacy_path.unlink()

    return entry_path


def _existing_lower_bound(entry_path: Path) -> float | None:
    payload = _read_payload(entry_path)
    if payload is None:
        return None
    value = payload.get("lower_bound")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def update_lower_bound_leaderboard(
    lower_bounds_dir: Path,
    run_outputs_dir: Path,
    result: LowerBoundResult,
) -> Path | None:
    if result.status != "ok" or result.lower_bound is None:
        return None

    scenario_dir = lower_bounds_dir / _scenario_dir_name(result)
    entry_path = scenario_dir / f"{result.algorithm.replace('/', '_')}.json"
    existing_lower_bound = _existing_lower_bound(entry_path)
    if existing_lower_bound is not None and existing_lower_bound >= result.lower_bound:
        return None

    lower_bound_metadata = dict(result.metadata)
    lower_bound_metadata["leaderboard_entry_type"] = "lower_bound_best"
    lower_bound_metadata["leaderboard_updated_at"] = datetime.now().isoformat(timespec="seconds")
    lower_bound_metadata["leaderboard_source_run"] = run_outputs_dir.name
    lower_bound_metadata["leaderboard_source_dir"] = run_outputs_dir.name

    lower_bound_result = LowerBoundResult(
        algorithm=result.algorithm,
        status=result.status,
        lower_bound=result.lower_bound,
        objective=result.objective,
        logs=result.logs,
        metadata=lower_bound_metadata,
    )
    scenario_dir.mkdir(parents=True, exist_ok=True)
    texts = build_lower_bound_output_texts(lower_bound_result)
    payload = {
        "format": "ocam_lower_bound_entry_v1",
        "algorithm": lower_bound_result.algorithm,
        "status": lower_bound_result.status,
        "lower_bound": lower_bound_result.lower_bound,
        "objective": lower_bound_result.objective,
        "metadata": lower_bound_result.metadata,
        "logs": lower_bound_result.logs,
        "artifacts": texts,
    }
    entry_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True, default=str), encoding="utf-8")
    return entry_path
