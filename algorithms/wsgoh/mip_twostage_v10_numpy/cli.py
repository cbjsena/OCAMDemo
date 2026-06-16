from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import yaml

from ocam.io import load_inputs
from ocam.models import CascadingSolution
from ocam.preprocessing import preprocess

from .config import DEFAULT_INPUT_DIR, DEFAULT_OUTPUT_DIR, DEFAULT_TIMELIMIT
from .types import DirectRunArguments

def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run wsgoh/mip_twostage_v10_numpy directly. OCAM's normal entrypoint is "
            "`python main.py <config.yaml>`."
        )
    )
    parser.add_argument("config", nargs="?", type=Path, default=None)
    parser.add_argument("--config", dest="config_option", type=Path, default=None)
    parser.add_argument("--instance-index", type=int, default=0)
    parser.add_argument("--input-dir", type=Path, default=None)
    parser.add_argument("--timelimit", type=int, default=None)
    parser.add_argument("--output-solution", type=Path, default=None)
    return parser

def _resolve_path(value: str | Path, base_dir: Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (base_dir / path).resolve()

def _make_timestamped_output_dir(base_output_dir: Path) -> Path:
    prefix = datetime.now().strftime("%y%m%d_%H%M")
    candidate = base_output_dir / prefix
    if not candidate.exists():
        return candidate
    suffix = 2
    while True:
        next_candidate = base_output_dir / f"{prefix} ({suffix})"
        if not next_candidate.exists():
            return next_candidate
        suffix += 1

def _load_config_payload(config_path: Path) -> dict[str, Any]:
    with config_path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise ValueError("config file must contain a YAML mapping at the top level.")
    return payload

def _config_path_from_namespace(namespace: argparse.Namespace) -> Path | None:
    config_path = namespace.config
    config_option = namespace.config_option
    if config_path is not None and config_option is not None and config_path != config_option:
        raise ValueError("Provide config either positionally or with --config, not both.")
    chosen = config_option if config_option is not None else config_path
    if chosen is None:
        return None
    chosen = chosen.expanduser().resolve()
    if not chosen.is_file():
        raise ValueError(f"config file not found: {chosen}")
    return chosen

def _input_dir_from_config(payload: dict[str, Any], config_dir: Path, instance_index: int) -> Path | None:
    instances = payload.get("instances")
    if instances is not None:
        if not isinstance(instances, list) or not instances:
            raise ValueError("config.instances must be a non-empty list when provided.")
        if instance_index < 0 or instance_index >= len(instances):
            raise ValueError(
                f"instance_index {instance_index} is out of range for config.instances "
                f"with length {len(instances)}."
            )
        selected = instances[instance_index]
        if not isinstance(selected, str) or not selected:
            raise ValueError("selected config.instances entry must be a non-empty string.")
        return _resolve_path(selected, config_dir)

    input_dir = payload.get("input_dir")
    if input_dir is None:
        return None
    if not isinstance(input_dir, str) or not input_dir:
        raise ValueError("config.input_dir must be a non-empty string when provided.")
    return _resolve_path(input_dir, config_dir)

def _resolve_direct_run_arguments(namespace: argparse.Namespace) -> DirectRunArguments:
    config_path = _config_path_from_namespace(namespace)
    payload: dict[str, Any] = {}
    config_dir = Path.cwd()
    if config_path is not None:
        payload = _load_config_payload(config_path)
        config_dir = config_path.parent

    input_dir = namespace.input_dir
    if input_dir is None:
        input_dir = _input_dir_from_config(payload, config_dir, namespace.instance_index)
    input_dir = _resolve_path(input_dir or DEFAULT_INPUT_DIR, Path.cwd())

    timelimit = namespace.timelimit
    if timelimit is None:
        config_timelimit = payload.get("timelimit")
        if config_timelimit is not None and not isinstance(config_timelimit, int):
            raise ValueError("config.timelimit must be an integer when provided.")
        timelimit = config_timelimit if config_timelimit is not None else DEFAULT_TIMELIMIT
    if timelimit < 1:
        raise ValueError("timelimit must be positive.")

    output_solution = namespace.output_solution
    if output_solution is None:
        base_output_dir_value = payload.get("output_dir", payload.get("outputs"))
        if isinstance(base_output_dir_value, str) and base_output_dir_value:
            base_output_dir = _resolve_path(base_output_dir_value, config_dir)
        else:
            base_output_dir = _resolve_path(DEFAULT_OUTPUT_DIR, Path.cwd())
        output_solution = _make_timestamped_output_dir(base_output_dir) / "wsgoh_mip_twostage_v10_numpy_solution.json"
    else:
        output_solution = _resolve_path(output_solution, Path.cwd())

    return DirectRunArguments(input_dir=input_dir, timelimit=timelimit, output_solution=output_solution)


def main(algorithm_func: Callable[[Any, int], CascadingSolution]) -> None:
    parser = build_argument_parser()
    args = _resolve_direct_run_arguments(parser.parse_args())
    raw_inputs = load_inputs(args.input_dir)
    instance_data = preprocess(raw_inputs)
    solution = algorithm_func(instance_data, args.timelimit)
    if args.output_solution is not None:
        args.output_solution.parent.mkdir(parents=True, exist_ok=True)
        with args.output_solution.open("w", encoding="utf-8") as handle:
            json.dump(solution.to_dict(), handle, indent=2, default=str)
        print(f"wsgoh/mip_twostage_v10_numpy wrote solution to {args.output_solution}")
