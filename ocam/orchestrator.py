from __future__ import annotations

import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

import yaml

from algorithms.registry import (
    AlgorithmLookupError,
    discover_algorithm_import_failures,
    discover_algorithms,
    get_algorithm_spec,
)
from ocam.console import ConsoleCapture
from ocam.io import load_inputs, write_lower_bound_outputs, write_outputs
from ocam.leaderboard import update_leaderboard, update_lower_bound_leaderboard
from ocam.models import AlgorithmResult, CascadingSolution, LowerBoundResult, NoResultError, RunConfig
from ocam.postprocessing import postprocess
from ocam.preprocessing import preprocess


def _position_declaration_summary(instance_data, solution: CascadingSolution) -> dict[str, object]:
    selected_by_key: dict[tuple[str, str], list[int]] = {}
    for position in solution.declared_positions:
        selected_by_key.setdefault((position.lane_code, position.proforma_name), []).append(
            position.declared_position_no
        )

    rows = []
    for lane in instance_data.service_lanes:
        lane_code = lane["lane_code"]
        for version in lane["versions"]:
            available_positions = sorted(version.get("available_positions") or [])
            if not available_positions:
                continue
            proforma_name = version["proforma_name"]
            rows.append(
                {
                    "lane_code": lane_code,
                    "proforma_name": proforma_name,
                    "available_positions": available_positions,
                    "selected_positions": sorted(selected_by_key.get((lane_code, proforma_name), [])),
                    "required_count": int(version.get("own_vessel_count") or 0),
                    "declared_count": int(version.get("total_vessel_count") or len(available_positions)),
                    "effective_from_date": str(version.get("effective_from") or ""),
                }
            )
    return {
        "format": "ocam_position_declaration_summary_v1",
        "rows": rows,
    }


def _make_run_output_dir(base_outputs_dir: Path) -> Path:
    prefix = datetime.now().strftime("%y%m%d_%H%M")
    candidate = base_outputs_dir / prefix
    if not candidate.exists():
        return candidate

    suffix = 2
    while True:
        next_candidate = base_outputs_dir / f"{prefix} ({suffix})"
        if not next_candidate.exists():
            return next_candidate
        suffix += 1


def _sanitize_path_component(value: str) -> str:
    stripped = value.strip()
    sanitized = "".join(char if char.isalnum() or char in {"-", "_", "."} else "_" for char in stripped)
    return sanitized or "unnamed"


def _resolve_instance_dirs(payload: dict[str, object], config_dir: Path) -> tuple[Path, ...]:
    instances = payload.get("instances")
    input_dir = payload.get("input_dir")

    if instances is None:
        instances = [input_dir or "instances/toy_v1"]

    if not isinstance(instances, list) or not instances:
        raise ValueError("config.instances는 비어 있지 않은 문자열 배열이어야 합니다.")
    if not all(isinstance(value, str) and value for value in instances):
        raise ValueError("config.instances의 각 원소는 비어 있지 않은 문자열이어야 합니다.")

    return tuple((config_dir / value).resolve() for value in instances)


def _build_instance_output_dirs(run_output_dir: Path, instance_dirs: tuple[Path, ...]) -> dict[Path, Path]:
    counts: dict[str, int] = {}
    output_dirs: dict[Path, Path] = {}
    use_subdirs = len(instance_dirs) > 1

    for instance_dir in instance_dirs:
        base_name = _sanitize_path_component(instance_dir.name)
        counts[base_name] = counts.get(base_name, 0) + 1
        unique_name = base_name if counts[base_name] == 1 else f"{base_name}_{counts[base_name]}"
        output_dirs[instance_dir] = run_output_dir / unique_name if use_subdirs else run_output_dir
    return output_dirs


def _base_metadata(instance_data, instance_dir: Path) -> dict[str, object]:
    metadata: dict[str, object] = {}
    planning_horizon = instance_data.planning_horizon or {}
    if planning_horizon.get("start") is not None:
        metadata["planning_horizon_start"] = planning_horizon["start"]
    if planning_horizon.get("end") is not None:
        metadata["planning_horizon_end"] = planning_horizon["end"]
    metadata["input_dir"] = str(instance_dir)
    metadata["input_dir_name"] = instance_dir.name
    metadata["scenario_name"] = instance_data.scenario_name
    return metadata


def load_run_config(config_path: Path) -> RunConfig:
    with config_path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}

    if not isinstance(payload, dict):
        raise ValueError("config 파일의 최상위 YAML 값은 mapping(dict)이어야 합니다.")

    legacy_messages: list[str] = []
    if "algorithm" in payload or "compare_with" in payload:
        legacy_messages.append(
            "구버전 알고리즘 지정 방식(`algorithm`, `compare_with`)을 사용하고 있습니다.\n"
            "다음처럼 `algorithms` 배열 하나로 바꿔주세요.\n\n"
            "기존 예시:\n"
            "  algorithm: yongs/only_virtual2\n"
            "  compare_with: [yongs/only_virtual]\n\n"
            "새 예시:\n"
            "  algorithms:\n"
            "    - yongs/only_virtual2\n"
            "    - yongs/only_virtual"
        )
    if "planning_horizon" in payload:
        legacy_messages.append(
            "구버전 planning horizon 지정 방식(`planning_horizon`)을 사용하고 있습니다.\n"
            "planning horizon은 이제 config가 아니라 인스턴스의 `metadata.csv`에 작성해야 합니다.\n\n"
            "config.yaml에서는 `planning_horizon` 블록을 제거하고,\n"
            "`input_dir/metadata.csv`에 다음 형식으로 넣어주세요.\n\n"
            "  key,value\n"
            "  scenario_name,toy_scenario_v1\n"
            "  planning_horizon_start,2026-03-10T19:30:00Z\n"
            "  planning_horizon_end,2027-01-01T00:00:00Z"
        )
    if legacy_messages:
        raise ValueError("구버전 config 형식입니다. 아래 안내에 따라 수정해주세요.\n\n" + "\n\n".join(legacy_messages))

    algorithms = payload.get("algorithms", ["yongs/only_virtual"])
    outputs_dir = payload.get("outputs", payload.get("output_dir", "outputs"))
    leaderboard_dir = payload.get("leaderboard", "outputs/leaderboard")
    timelimit = payload.get("timelimit", 60)

    if (
        not isinstance(algorithms, list)
        or not algorithms
        or not all(isinstance(value, str) and value for value in algorithms)
    ):
        raise ValueError("config.algorithms는 비어 있지 않은 문자열 배열이어야 합니다.")
    if not isinstance(outputs_dir, str) or not outputs_dir:
        raise ValueError("config.outputs는 비어 있지 않은 문자열이어야 합니다.")
    if not isinstance(leaderboard_dir, str) or not leaderboard_dir:
        raise ValueError("config.leaderboard는 비어 있지 않은 문자열이어야 합니다.")
    if not isinstance(timelimit, int) or isinstance(timelimit, bool):
        raise ValueError("config.timelimit는 정수여야 합니다.")

    config_dir = config_path.parent
    return RunConfig(
        algorithms=tuple(algorithms),
        instances=_resolve_instance_dirs(payload, config_dir),
        outputs_dir=(config_dir / outputs_dir).resolve(),
        leaderboard_dir=(config_dir / leaderboard_dir).resolve(),
        timelimit=timelimit,
    )


def run_single(
    config: RunConfig,
    algorithm_name: str,
    instance_dir: Path,
    run_output_dir: Path,
    output_dir: Path,
) -> AlgorithmResult | LowerBoundResult | None:
    spec = get_algorithm_spec(algorithm_name)
    raw_inputs = load_inputs(instance_dir)
    instance_data = preprocess(raw_inputs)

    start = time.perf_counter()
    status = "ok"
    no_result = False
    raw_result: object | None = None
    solution = CascadingSolution(
        declared_positions=[],
        vessel_schedules={},
        virtual_vessel_schedules={},
        num_virtual_vessels_used=0,
    )
    metadata = _base_metadata(instance_data, instance_dir)
    checkpoint = start

    def log_stage(message: str) -> None:
        nonlocal checkpoint
        now = time.perf_counter()
        print(
            f"[orchestrator] {algorithm_name} {message} | step={now - checkpoint:.2f}s total={now - start:.2f}s",
            flush=True,
        )
        checkpoint = now

    with ConsoleCapture() as console:
        try:
            log_stage("algorithm call begin")
            raw_result = spec.entrypoint(instance_data, config.timelimit)
            log_stage(f"algorithm call returned type={type(raw_result).__name__}")
            if isinstance(raw_result, CascadingSolution):
                solution = raw_result
            elif isinstance(raw_result, LowerBoundResult):
                pass
            else:
                raise TypeError(
                    "ocam.orchestrator.run_single: "
                    f"algorithm '{algorithm_name}' must return CascadingSolution or LowerBoundResult, "
                    f"got {type(raw_result)!r}."
                )
        except NoResultError as exc:
            no_result = True
            metadata["no_result_reason"] = str(exc)
            print(f"Algorithm '{algorithm_name}' produced no result: {exc}")
        except SystemExit as exc:
            status = "stopped"
            metadata["exit_code"] = exc.code
            metadata["stop_reason"] = "SystemExit"
            print(f"Algorithm '{algorithm_name}' stopped via exit().")
        except BaseException as exc:
            if isinstance(exc, KeyboardInterrupt):
                raise
            status = "error"
            metadata["error_type"] = type(exc).__name__
            metadata["error_message"] = str(exc)
            metadata["error_context"] = f"ocam.orchestrator.run_single[{algorithm_name}]"
            traceback.print_exc()
    log_stage("console capture exited")

    elapsed = time.perf_counter() - start
    if no_result:
        return None

    if isinstance(raw_result, LowerBoundResult):
        raw_result.algorithm = algorithm_name
        raw_result.logs = console.text
        raw_result.metadata = {**metadata, **raw_result.metadata}
        raw_result.metadata.setdefault("elapsed_seconds", elapsed)
        raw_result.metadata.setdefault("outputs_run_dir", run_output_dir.name)
        raw_result.metadata.setdefault("validation_status", "skipped")
        if output_dir != run_output_dir:
            raw_result.metadata.setdefault("outputs_instance_dir", output_dir.name)
        if raw_result.lower_bound is not None and raw_result.objective is None:
            raw_result.objective = {"lower_bound": raw_result.lower_bound}
        elif isinstance(raw_result.objective, dict) and raw_result.lower_bound is not None:
            raw_result.objective.setdefault("lower_bound", raw_result.lower_bound)
        log_stage("write lower-bound outputs begin")
        write_lower_bound_outputs(output_dir, raw_result)
        log_stage("write lower-bound outputs finished")
        log_stage("update lower-bound leaderboard begin")
        update_lower_bound_leaderboard(config.leaderboard_dir.parent / "lower_bounds", run_output_dir, raw_result)
        log_stage("update lower-bound leaderboard finished")
        return raw_result

    result = AlgorithmResult(
        algorithm=algorithm_name,
        status=status,
        solution=solution,
        logs=console.text,
        metadata=metadata,
    )
    result.metadata.setdefault("elapsed_seconds", elapsed)
    result.metadata.setdefault("outputs_run_dir", run_output_dir.name)
    result.metadata["position_declaration_summary"] = _position_declaration_summary(instance_data, solution)
    if output_dir != run_output_dir:
        result.metadata.setdefault("outputs_instance_dir", output_dir.name)
    if result.status == "ok":
        try:
            log_stage("postprocess begin")
            result = postprocess(result, instance_data)
            log_stage("postprocess finished")
            result.metadata.setdefault("validation_status", "passed")
        except BaseException as exc:
            if isinstance(exc, KeyboardInterrupt):
                raise
            result.status = "error"
            result.objective = None
            result.metadata["error_type"] = type(exc).__name__
            result.metadata["error_message"] = str(exc)
            result.metadata["error_context"] = f"ocam.orchestrator.postprocess[{algorithm_name}]"
            result.metadata["validation_status"] = "failed"
            traceback.print_exc()
    else:
        result.metadata.setdefault("validation_status", "skipped")

    log_stage("write outputs begin")
    write_outputs(output_dir, result)
    log_stage("write outputs finished")
    log_stage("update leaderboard begin")
    update_leaderboard(config.leaderboard_dir, run_output_dir, result)
    log_stage("update leaderboard finished")
    return result


def run_many(config: RunConfig) -> list[AlgorithmResult | LowerBoundResult]:
    run_output_dir = _make_run_output_dir(config.outputs_dir)
    instance_output_dirs = _build_instance_output_dirs(run_output_dir, config.instances)
    results: list[AlgorithmResult | LowerBoundResult] = []
    next_config = RunConfig(
        algorithms=config.algorithms,
        instances=config.instances,
        outputs_dir=run_output_dir,
        leaderboard_dir=config.leaderboard_dir,
        timelimit=config.timelimit,
    )
    for instance_dir in config.instances:
        output_dir = instance_output_dirs[instance_dir]
        for name in config.algorithms:
            result = run_single(next_config, name, instance_dir, run_output_dir, output_dir)
            if result is not None:
                results.append(result)
    return results


def _print_algorithm_list() -> None:
    discovered = discover_algorithms()
    import_failures = discover_algorithm_import_failures()
    if not discovered and not import_failures:
        print("No algorithms discovered under algorithms/.")
        return

    if discovered:
        print("Available algorithms:")
        for spec in discovered:
            print(f"  - {spec.name}: {spec.description}")

    if import_failures:
        print("Algorithms found but failed to import:")
        for failure in import_failures:
            print(f"  - {failure.name}: {failure.error_message}")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--list-algorithms"]:
        _print_algorithm_list()
        return 0

    if len(args) > 1:
        print("Usage: python3 main.py [--list-algorithms | <config.yaml>]", file=sys.stderr)
        return 2

    config_path = "default_config.yaml" if len(args) == 0 else args[0]
    config_path = Path(config_path).expanduser().resolve()
    if not config_path.is_file():
        print(f"Config file not found: {config_path}", file=sys.stderr)
        return 2

    try:
        config = load_run_config(config_path)
    except ValueError as exc:
        print(f"Invalid config file {config_path}: {exc}", file=sys.stderr)
        return 2

    try:
        results = run_many(config)
    except AlgorithmLookupError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    for result in results:
        file_prefix = result.algorithm.replace("/", "_")
        run_dir = result.metadata.get("outputs_run_dir", "")
        output_base_dir = config.outputs_dir / run_dir if run_dir else config.outputs_dir
        instance_dir = result.metadata.get("outputs_instance_dir", "")
        if isinstance(instance_dir, str) and instance_dir:
            output_base_dir = output_base_dir / instance_dir
        if isinstance(result, LowerBoundResult):
            lower_bound = "n/a" if result.lower_bound is None else result.lower_bound
            print(
                f"[{result.metadata.get('input_dir_name', '?')} | {result.algorithm}] "
                f"status={result.status} lower_bound={lower_bound} "
                f"lower_bound_file={output_base_dir / (file_prefix + '_lower_bound.json')} "
                f"metadata={output_base_dir / (file_prefix + '_lower_bound_metadata.csv')}"
            )
            continue
        objective = "n/a" if result.objective is None else result.objective["total_cost"]
        print(
            f"[{result.metadata.get('input_dir_name', '?')} | {result.algorithm}] "
            f"status={result.status} objective={objective} "
            f"solution={output_base_dir / (file_prefix + '_solution.json')} "
            f"metadata={output_base_dir / (file_prefix + '_metadata.csv')}"
        )
    return 0
