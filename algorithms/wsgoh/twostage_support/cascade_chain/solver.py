from __future__ import annotations

import argparse
import math
import random
import json
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import blake2b
from pathlib import Path
from typing import Any, Iterable

import yaml

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from algorithms.yongs.only_virtual import solver as only_virtual1_solver
from algorithms.yongs.only_virtual2 import solver as only_virtual2_solver
from algorithms.wsgoh.utils_mip import (
    CoverageKey,
    Pattern,
    PositionKey,
    ServiceFragment,
    _clone_solution,
    _declared_position_payload,
    _drop_orphan_patterns_with_count,
    _estimate_pattern_cost,
    _evaluate_total_cost,
    _fixed_position_keys,
    _format_counts,
    _format_coverage_positions,
    _initial_selectable_position_values,
    _make_fragment,
    _pattern_family,
    _position_key_from_schedule,
    _position_schedules,
    _required_coverage_keys,
    _safe_name,
    _schedule_coverage_keys,
    _schedule_payload_signature,
    _selectable_position_keys,
    _status_name,
    _switch_candidates_for_fragment,
    _try_assign_service_as_primary,
    _try_insert_service_schedule,
    _enumerate_virtual_handover_splits,
)
from ocam.models import (
    CascadingSolution,
    Delivery,
    DryDock,
    Idle,
    InLaneEvent,
    InstanceData,
    PortStay,
    Redelivery,
    VesselScheduleEvent,
)
from ocam.io import load_inputs
from ocam.preprocessing import preprocess
from ocam.utils import event_end_port_code, event_end_time, event_start_port_code, event_start_time, lookup_distance
from ocam.validation import validate_solution

DESCRIPTION = (
    "Two-stage support: virtual-hole-driven cascade-chain pattern generation "
    "with a lexicographic restricted set-partitioning MIP."
)

INITIAL_HEURISTIC = "only_virtual2"
ROUNDS: int | None = None
DEFAULT_INPUT_DIR = Path("instances/toy_v1")
DEFAULT_OUTPUT_DIR = Path("output_dir")
DEFAULT_TIMELIMIT = 300
MAX_ITERATIONS = 6
NO_IMPROVEMENT_LIMIT = 2
MAX_POOL_PATTERNS = 2000
MAX_PATTERNS_PER_VESSEL = 60
MAX_ITERATION_CANDIDATES = 180
INTERMEDIATE_NOREL_TIME_LIMIT = 8
FINAL_BRANCH_AND_BOUND_TIME_RESERVE = 30
FINAL_SOLVE_MODE = "full"  # Change to "norel" for faster experimental runs.
RANDOM_SEED = 42


@dataclass(frozen=True)
class SolverOptions:
    initial_heuristic: str = INITIAL_HEURISTIC
    rounds: int | None = ROUNDS
    max_iterations: int = MAX_ITERATIONS
    no_improvement_limit: int = NO_IMPROVEMENT_LIMIT
    max_pool_patterns: int = MAX_POOL_PATTERNS
    max_patterns_per_vessel: int = MAX_PATTERNS_PER_VESSEL
    max_iteration_candidates: int = MAX_ITERATION_CANDIDATES
    intermediate_norel_time_limit: int = INTERMEDIATE_NOREL_TIME_LIMIT
    final_branch_and_bound_time_reserve: int = FINAL_BRANCH_AND_BOUND_TIME_RESERVE
    final_solve_mode: str = FINAL_SOLVE_MODE
    random_seed: int = RANDOM_SEED


@dataclass(frozen=True)
class DirectRunArguments:
    input_dir: Path
    timelimit: int
    output_solution: Path | None


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run wsgoh/twostage_support/cascade_chain directly. OCAM's normal entrypoint is still "
            "`python main.py <config.yaml>`; this parser is for solver-level experiments."
        )
    )
    parser.add_argument(
        "config",
        nargs="?",
        type=Path,
        default=None,
        help="Optional OCAM YAML config. Direct solver mode uses one instance from this config.",
    )
    parser.add_argument(
        "--config",
        dest="config_option",
        type=Path,
        default=None,
        help="Optional OCAM YAML config. Same as the positional config argument.",
    )
    parser.add_argument(
        "--instance-index",
        type=int,
        default=0,
        help="Instance index to use when the config has an instances list. Default: 0.",
    )
    parser.add_argument("--input-dir", type=Path, default=None)
    parser.add_argument("--timelimit", type=int, default=None)
    parser.add_argument("--output-solution", type=Path, default=None)
    parser.add_argument(
        "--initial-heuristic",
        choices=("only_virtual1", "only_virtual2", "only_virtual", "yongs/only_virtual", "yongs/only_virtual2"),
        default=INITIAL_HEURISTIC,
        help=(
            "Seed heuristic for the initial feasible solution. "
            "only_virtual1 is an alias for yongs/only_virtual; only_virtual2 is the default."
        ),
    )
    parser.add_argument(
        "--rounds",
        type=int,
        default=ROUNDS,
        help=(
            "Target total number of master solves, including the final master. "
            "Use --rounds 1 --final-solve-mode full for a single branch-and-bound solve."
        ),
    )
    parser.add_argument("--max-iterations", type=int, default=MAX_ITERATIONS)
    parser.add_argument("--no-improvement-limit", type=int, default=NO_IMPROVEMENT_LIMIT)
    parser.add_argument("--max-pool-patterns", type=int, default=MAX_POOL_PATTERNS)
    parser.add_argument("--max-patterns-per-vessel", type=int, default=MAX_PATTERNS_PER_VESSEL)
    parser.add_argument("--max-iteration-candidates", type=int, default=MAX_ITERATION_CANDIDATES)
    parser.add_argument("--intermediate-norel-time-limit", type=int, default=INTERMEDIATE_NOREL_TIME_LIMIT)
    parser.add_argument("--final-time-reserve", type=int, default=FINAL_BRANCH_AND_BOUND_TIME_RESERVE)
    parser.add_argument("--final-solve-mode", choices=("full", "norel"), default=FINAL_SOLVE_MODE)
    parser.add_argument("--random-seed", type=int, default=RANDOM_SEED)
    return parser


def _options_from_namespace(namespace: argparse.Namespace) -> SolverOptions:
    return SolverOptions(
        initial_heuristic=namespace.initial_heuristic,
        rounds=namespace.rounds,
        max_iterations=namespace.max_iterations,
        no_improvement_limit=namespace.no_improvement_limit,
        max_pool_patterns=namespace.max_pool_patterns,
        max_patterns_per_vessel=namespace.max_patterns_per_vessel,
        max_iteration_candidates=namespace.max_iteration_candidates,
        intermediate_norel_time_limit=namespace.intermediate_norel_time_limit,
        final_branch_and_bound_time_reserve=namespace.final_time_reserve,
        final_solve_mode=namespace.final_solve_mode,
        random_seed=namespace.random_seed,
    )


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
    if input_dir is None:
        input_dir = DEFAULT_INPUT_DIR
    else:
        input_dir = _resolve_path(input_dir, Path.cwd())

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
        output_solution = (
            _make_timestamped_output_dir(base_output_dir)
            / "wsgoh_twostage_cascade_chain_solution.json"
        )
    else:
        output_solution = _resolve_path(output_solution, Path.cwd())

    return DirectRunArguments(
        input_dir=input_dir,
        timelimit=timelimit,
        output_solution=output_solution,
    )


def _canonical_initial_heuristic(value: str) -> str:
    aliases = {
        "only_virtual1": "only_virtual1",
        "only_virtual": "only_virtual1",
        "yongs/only_virtual": "only_virtual1",
        "only_virtual2": "only_virtual2",
        "yongs/only_virtual2": "only_virtual2",
    }
    try:
        return aliases[value]
    except KeyError as exc:
        raise ValueError(
            "initial_heuristic must be one of only_virtual1, only_virtual2, "
            "yongs/only_virtual, or yongs/only_virtual2."
        ) from exc


def _apply_solver_options(options: SolverOptions) -> None:
    global INITIAL_HEURISTIC
    global ROUNDS
    global MAX_ITERATIONS
    global NO_IMPROVEMENT_LIMIT
    global MAX_POOL_PATTERNS
    global MAX_PATTERNS_PER_VESSEL
    global MAX_ITERATION_CANDIDATES
    global INTERMEDIATE_NOREL_TIME_LIMIT
    global FINAL_BRANCH_AND_BOUND_TIME_RESERVE
    global FINAL_SOLVE_MODE
    global RANDOM_SEED

    initial_heuristic = _canonical_initial_heuristic(options.initial_heuristic)
    if options.rounds is not None:
        if options.rounds < 1:
            raise ValueError("rounds must be positive.")
    if options.max_iterations < 0:
        raise ValueError("max_iterations must be non-negative.")
    if options.no_improvement_limit < 1:
        raise ValueError("no_improvement_limit must be positive.")
    if options.max_pool_patterns < 1:
        raise ValueError("max_pool_patterns must be positive.")
    if options.max_patterns_per_vessel < 1:
        raise ValueError("max_patterns_per_vessel must be positive.")
    if options.max_iteration_candidates < 1:
        raise ValueError("max_iteration_candidates must be positive.")
    if options.intermediate_norel_time_limit < 1:
        raise ValueError("intermediate_norel_time_limit must be positive.")
    if options.final_branch_and_bound_time_reserve < 0:
        raise ValueError("final_branch_and_bound_time_reserve must be non-negative.")
    if options.final_solve_mode not in {"full", "norel"}:
        raise ValueError("final_solve_mode must be 'full' or 'norel'.")

    INITIAL_HEURISTIC = initial_heuristic
    ROUNDS = options.rounds
    MAX_ITERATIONS = options.max_iterations
    NO_IMPROVEMENT_LIMIT = options.no_improvement_limit
    MAX_POOL_PATTERNS = options.max_pool_patterns
    MAX_PATTERNS_PER_VESSEL = options.max_patterns_per_vessel
    MAX_ITERATION_CANDIDATES = options.max_iteration_candidates
    INTERMEDIATE_NOREL_TIME_LIMIT = options.intermediate_norel_time_limit
    FINAL_BRANCH_AND_BOUND_TIME_RESERVE = options.final_branch_and_bound_time_reserve
    FINAL_SOLVE_MODE = options.final_solve_mode
    RANDOM_SEED = options.random_seed


@dataclass
class CoverageContext:
    fixed_positions: set[PositionKey]
    selectable_positions: set[PositionKey]
    coverage_positions: set[PositionKey]
    initial_selectable_values: dict[PositionKey, int]
    coverage_schedules: dict[PositionKey, list[Any]]
    selectable_schedules: dict[PositionKey, list[Any]]
    fixed_coverage: set[CoverageKey]
    selectable_coverage: dict[CoverageKey, PositionKey]
    required_coverage: set[CoverageKey]
    declared_positions_payload: list[dict[str, Any]]


@dataclass
class MasterResult:
    solution: CascadingSolution
    objective: float
    virtual_portstay_objective: int
    status: str
    selected_patterns: list[Pattern]
    selected_pattern_ids: set[str]
    selected_family_counts: Counter[str]


@dataclass
class PoolPruneStats:
    input_count: int
    retained_count: int
    schedule_duplicate_pruned: int = 0
    coverage_duplicate_pruned: int = 0
    vessel_cap_pruned: int = 0
    total_cap_pruned: int = 0
    orphan_pruned: int = 0


@dataclass(frozen=True)
class VirtualHole:
    hole_id: str
    lane_code: str
    version_code: str
    position_no: int
    schedule: tuple[VesselScheduleEvent, ...]
    prefix_schedule: tuple[VesselScheduleEvent, ...]
    start_time: datetime
    end_time: datetime
    start_port_code: str
    end_port_code: str
    coverage_keys: frozenset[CoverageKey]
    portstay_count: int
    source_virtual_code: str
    source_virtual_pattern_id: str
    split_mode: str
    opportunity_cost_estimate: float


@dataclass(frozen=True)
class SourceBlock:
    block_id: str
    vessel_code: str
    lane_code: str | None
    version_code: str | None
    position_no: int | None
    start_time: datetime
    end_time: datetime
    start_port_code: str
    end_port_code: str
    coverage_keys: frozenset[CoverageKey]
    portstay_count: int
    selected_pattern_id: str
    event_slice: tuple[int, int]
    is_locked: bool
    can_be_displaced: bool


@dataclass(frozen=True)
class HandoverOption:
    ts_out_port_code: str
    ts_out_time: datetime
    ts_in_port_code: str
    ts_in_time: datetime
    required_speed: float
    sailing_hours: float
    distance_nm: float
    slack_hours: float


@dataclass
class DisplacementMove:
    move_id: str
    vessel_code: str
    target_hole: VirtualHole
    source_block: SourceBlock | None
    source_hole_created: VirtualHole | None
    handover: HandoverOption | None
    target_taken_portstay_count: int
    source_created_portstay_count: int
    delta_virtual_portstay: int
    estimated_cost_delta: float
    required_speed: float
    actual_pattern_candidate: Pattern
    residual_virtual_patterns: list[Pattern]


@dataclass
class CascadeChain:
    chain_id: str
    initial_hole_id: str
    moves: list[DisplacementMove]
    terminal_hole: VirtualHole | None
    terminal_closed_by_free_vessel: bool
    terminal_closed_by_virtual: bool
    net_virtual_reduction: int
    estimated_cost_delta: float
    pattern_candidates: list[Pattern]


def _load_gurobi():
    try:
        import gurobipy as gp
        from gurobipy import GRB
    except ImportError as exc:
        raise RuntimeError(
            "wsgoh/twostage_support/cascade_chain requires gurobipy at runtime. "
            "Install Gurobi's Python package or activate an environment with gurobipy available."
        ) from exc
    return gp, GRB


def _make_initial_solution(instance_data: InstanceData, timelimit: int) -> CascadingSolution:
    initial_solver = only_virtual1_solver if INITIAL_HEURISTIC == "only_virtual1" else only_virtual2_solver
    initial_solver.NUM_VIRTUAL_VESSELS_USED = 0
    initial_solver.USED_VIRTUAL_VESSEL_CODES.clear()
    solution = initial_solver.algorithm(instance_data, timelimit)
    if initial_solver is not only_virtual2_solver:
        # Shared MIP helpers still use only_virtual2's schedule factory.
        only_virtual2_solver.init_solver_globals(instance_data)
    return solution


def _build_coverage_context(instance_data: InstanceData, initial_solution: CascadingSolution) -> CoverageContext:
    fixed_positions = _fixed_position_keys(instance_data)
    selectable_positions = _selectable_position_keys(instance_data)
    coverage_positions = fixed_positions | selectable_positions
    coverage_schedules = _position_schedules(coverage_positions, instance_data)
    selectable_schedules = {
        position_key: coverage_schedules[position_key]
        for position_key in selectable_positions
    }

    fixed_coverage = _required_coverage_keys(initial_solution, fixed_positions)
    selectable_coverage: dict[CoverageKey, PositionKey] = {}
    for position_key, schedule in selectable_schedules.items():
        for coverage_key in _schedule_coverage_keys(schedule, {position_key}):
            selectable_coverage[coverage_key] = position_key

    return CoverageContext(
        fixed_positions=fixed_positions,
        selectable_positions=selectable_positions,
        coverage_positions=coverage_positions,
        initial_selectable_values=_initial_selectable_position_values(instance_data, initial_solution),
        coverage_schedules=coverage_schedules,
        selectable_schedules=selectable_schedules,
        fixed_coverage=fixed_coverage,
        selectable_coverage=selectable_coverage,
        required_coverage=fixed_coverage | set(selectable_coverage),
        declared_positions_payload=_declared_position_payload(selectable_positions),
    )


def _gurobi_name(prefix: str, value: object, max_length: int = 240) -> str:
    safe_value = _safe_name(value)
    name = f"{prefix}_{safe_value}"
    if len(name) <= max_length:
        return name
    digest = blake2b(name.encode("utf-8"), digest_size=8).hexdigest()
    head_length = max_length - len(prefix) - len(digest) - 2
    return f"{prefix}_{safe_value[:head_length]}_{digest}"


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
    schedule_payload = {"events": [event.to_dict() for event in schedule]}
    return Pattern(
        pattern_id=pattern_id,
        vessel_code=vessel_code,
        is_virtual=is_virtual,
        schedule_payload=schedule_payload,
        coverage_keys=_schedule_coverage_keys(schedule, context.coverage_positions),
        cost=_estimate_pattern_cost(
            instance_data,
            context.declared_positions_payload,
            vessel_code,
            schedule_payload,
            is_virtual=is_virtual,
        ),
        requires_pattern_ids=requires_pattern_ids,
        depth=depth,
        priority=priority,
        source_fragment_id=source_fragment_id,
        target_position_key=target_position_key,
        split_mode=split_mode,
    )


def _build_baseline_patterns(
    instance_data: InstanceData,
    initial_solution: CascadingSolution,
    context: CoverageContext,
) -> list[Pattern]:
    patterns: list[Pattern] = []

    for vessel_code, schedule in initial_solution.vessel_schedules.items():
        patterns.append(
            _make_pattern(
                instance_data=instance_data,
                context=context,
                pattern_id=f"actual:{vessel_code}",
                vessel_code=vessel_code,
                is_virtual=False,
                schedule=list(schedule),
                priority=1_000_000.0,
            )
        )

    for vessel_code, schedule in initial_solution.virtual_vessel_schedules.items():
        patterns.append(
            _make_pattern(
                instance_data=instance_data,
                context=context,
                pattern_id=f"virtual:{vessel_code}",
                vessel_code=vessel_code,
                is_virtual=True,
                schedule=list(schedule),
                priority=1_000_000.0,
            )
        )

    for position_key, schedule in context.coverage_schedules.items():
        patterns.append(
            _make_pattern(
                instance_data=instance_data,
                context=context,
                pattern_id=f"virtual-fallback:{position_key[0]}:{position_key[1]}:{position_key[2]}",
                vessel_code=f"MIPV6VIRTUAL_{position_key[0]}_{position_key[1]}_{position_key[2]}",
                is_virtual=True,
                schedule=schedule,
                priority=900_000.0,
                target_position_key=position_key,
            )
        )

    fixed_positions = context.coverage_positions - context.selectable_positions
    replacement_count = 0
    for virtual_code, virtual_schedule in initial_solution.virtual_vessel_schedules.items():
        position_key = _position_key_from_schedule(virtual_schedule)
        if position_key is None:
            continue
        for vessel_code, vessel_schedule in initial_solution.vessel_schedules.items():
            if _schedule_coverage_keys(vessel_schedule, fixed_positions):
                continue
            primary_schedule = _try_assign_service_as_primary(
                instance_data,
                vessel_code,
                list(vessel_schedule),
                position_key,
                list(virtual_schedule),
            )
            if primary_schedule is None:
                continue
            replacement_count += 1
            patterns.append(
                _make_pattern(
                    instance_data=instance_data,
                    context=context,
                    pattern_id=(
                        "actual-primary-virtual:"
                        f"{vessel_code}:{virtual_code}:{position_key[0]}:{position_key[1]}:"
                        f"{position_key[2]}:{replacement_count}"
                    ),
                    vessel_code=vessel_code,
                    is_virtual=False,
                    schedule=primary_schedule,
                    priority=800_000.0,
                    source_fragment_id=f"initial-virtual:{virtual_code}",
                    target_position_key=position_key,
                )
            )

    insertion_count = 0
    for vessel_code, schedule in initial_solution.vessel_schedules.items():
        for position_key, service_schedule in context.selectable_schedules.items():
            candidate = _try_insert_service_schedule(
                instance_data,
                vessel_code,
                list(schedule),
                position_key,
                service_schedule,
            )
            if candidate is None:
                continue
            insertion_count += 1
            patterns.append(
                _make_pattern(
                    instance_data=instance_data,
                    context=context,
                    pattern_id=(
                        "actual-candidate:"
                        f"{vessel_code}:{position_key[0]}:{position_key[1]}:{position_key[2]}:{insertion_count}"
                    ),
                    vessel_code=vessel_code,
                    is_virtual=False,
                    schedule=candidate,
                    priority=100_000.0,
                    source_fragment_id=f"selectable:{position_key[0]}:{position_key[1]}:{position_key[2]}",
                    target_position_key=position_key,
                )
            )

    return patterns


def _baseline_family_enabled(pattern: Pattern) -> bool:
    family = _pattern_family(pattern)
    if family == "actual":
        return ENABLE_BASELINE_ACTUAL
    if family == "virtual":
        return ENABLE_BASELINE_VIRTUAL
    if family == "virtual-fallback":
        return ENABLE_VIRTUAL_FALLBACK
    if family == "actual-candidate":
        return ENABLE_ACTUAL_CANDIDATE
    if family == "actual-primary-virtual":
        return ENABLE_ACTUAL_PRIMARY_VIRTUAL
    return True


def _filter_baseline_patterns_for_experiment(patterns: list[Pattern]) -> tuple[list[Pattern], Counter[str]]:
    removed: Counter[str] = Counter()
    retained: list[Pattern] = []
    for pattern in patterns:
        if _baseline_family_enabled(pattern):
            retained.append(pattern)
        else:
            removed[_pattern_family(pattern)] += 1
    return retained, removed


def _protected_pattern_ids(patterns: list[Pattern], selected_pattern_ids: set[str]) -> set[str]:
    protected = set(selected_pattern_ids)
    for pattern in patterns:
        if pattern.pattern_id.startswith(("actual:", "virtual:", "virtual-fallback:")):
            protected.add(pattern.pattern_id)
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


def _prune_pattern_pool(
    patterns: list[Pattern],
    selected_pattern_ids: set[str],
) -> tuple[list[Pattern], PoolPruneStats]:
    protected_ids = _protected_pattern_ids(patterns, selected_pattern_ids)
    stats = PoolPruneStats(input_count=len(patterns), retained_count=0)

    by_schedule: dict[tuple[Any, ...], Pattern] = {}
    for pattern in patterns:
        signature = (
            pattern.vessel_code,
            pattern.is_virtual,
            pattern.requires_pattern_ids,
            _schedule_payload_signature(pattern.schedule_payload),
        )
        incumbent = by_schedule.get(signature)
        by_schedule[signature] = pattern if incumbent is None else _prefer_pattern(pattern, incumbent, protected_ids)
    stats.schedule_duplicate_pruned = len(patterns) - len(by_schedule)

    by_coverage: dict[tuple[Any, ...], Pattern] = {}
    for pattern in by_schedule.values():
        signature = (
            pattern.vessel_code,
            pattern.is_virtual,
            pattern.requires_pattern_ids,
            pattern.coverage_keys,
            _pattern_family(pattern),
        )
        incumbent = by_coverage.get(signature)
        by_coverage[signature] = pattern if incumbent is None else _prefer_pattern(pattern, incumbent, protected_ids)
    stats.coverage_duplicate_pruned = len(by_schedule) - len(by_coverage)

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

    capped, orphan_count = _drop_orphan_patterns_with_count(capped)
    stats.orphan_pruned += orphan_count
    if len(capped) > MAX_POOL_PATTERNS:
        protected = [pattern for pattern in capped if pattern.pattern_id in protected_ids]
        candidates = [pattern for pattern in capped if pattern.pattern_id not in protected_ids]
        candidates.sort(key=lambda pattern: (-pattern.priority, pattern.cost, pattern.pattern_id))
        capped = protected + candidates[: max(0, MAX_POOL_PATTERNS - len(protected))]
        stats.total_cap_pruned = len(retained) - len(capped)
        capped, orphan_count = _drop_orphan_patterns_with_count(capped)
        stats.orphan_pruned += orphan_count

    stats.retained_count = len(capped)
    return sorted(capped, key=lambda pattern: pattern.pattern_id), stats


def _make_virtual_suffix_fallback(
    instance_data: InstanceData,
    context: CoverageContext,
    *,
    pattern_id: str,
    parent_pattern_id: str,
    vessel_code: str,
    suffix_schedule: list[Any],
    priority: float,
    source_fragment_id: str,
    split_mode: str | None = None,
) -> Pattern | None:
    suffix_position_key = _position_key_from_schedule(suffix_schedule)
    suffix_coverage = _schedule_coverage_keys(suffix_schedule, context.coverage_positions)
    if suffix_position_key is None or not suffix_coverage:
        return None
    return _make_pattern(
        instance_data=instance_data,
        context=context,
        pattern_id=pattern_id,
        vessel_code=f"MIPV6_SUFFIX_{_safe_name(vessel_code)}_{_safe_name(pattern_id)}",
        is_virtual=True,
        schedule=list(suffix_schedule),
        requires_pattern_ids=frozenset({parent_pattern_id}),
        depth=2,
        priority=priority,
        source_fragment_id=source_fragment_id,
        target_position_key=suffix_position_key,
        split_mode=split_mode,
    )


def _virtual_fragments(solution: CascadingSolution) -> list[tuple[str, PositionKey, list[Any]]]:
    fragments: list[tuple[str, PositionKey, list[Any]]] = []
    for virtual_code, schedule in solution.virtual_vessel_schedules.items():
        position_key = _position_key_from_schedule(schedule)
        if position_key is not None:
            fragments.append((virtual_code, position_key, list(schedule)))
    return fragments


def _generate_selectable_switch_patterns(
    instance_data: InstanceData,
    context: CoverageContext,
    incumbent_solution: CascadingSolution,
    iteration: int,
) -> tuple[list[Pattern], Counter[str]]:
    patterns: list[Pattern] = []
    diagnostics: Counter[str] = Counter()
    serial = 0

    fragments = [
        _make_fragment(
            fragment_id=f"selectable:{position_key[0]}:{position_key[1]}:{position_key[2]}",
            position_key=position_key,
            schedule=schedule,
            source="selectable",
            depth=0,
        )
        for position_key, schedule in sorted(context.selectable_schedules.items())
    ]

    for fragment in fragments:
        candidates = []
        for vessel_code, vessel_schedule in incumbent_solution.vessel_schedules.items():
            result = _switch_candidates_for_fragment(
                instance_data,
                vessel_code,
                list(vessel_schedule),
                fragment,
                context.coverage_positions,
            )
            candidates.extend(result.candidates)
            diagnostics.update(result.rejections)
        candidates.sort(key=lambda candidate: candidate.score, reverse=True)

        for candidate in candidates[:3]:
            serial += 1
            safe_fragment_id = _safe_name(fragment.fragment_id)
            actual_pattern_id = (
                f"actual-alns-selectable-switch-i{iteration}:"
                f"{candidate.vessel_code}:{safe_fragment_id}:{serial}"
            )
            suffix_pattern_id = (
                f"virtual-suffix-fallback-dalns-selectable-switch-i{iteration}:"
                f"{candidate.vessel_code}:{safe_fragment_id}:{serial}"
            )
            suffix_pattern = _make_virtual_suffix_fallback(
                instance_data,
                context,
                pattern_id=suffix_pattern_id,
                parent_pattern_id=actual_pattern_id,
                vessel_code=candidate.vessel_code,
                suffix_schedule=candidate.dropped_suffix,
                priority=250_000.0 + candidate.score,
                source_fragment_id=f"dropped-suffix:{candidate.vessel_code}:{safe_fragment_id}",
                split_mode=candidate.split_mode,
            )
            actual_requires = (
                frozenset({suffix_pattern.pattern_id})
                if suffix_pattern is not None
                else frozenset()
            )
            actual_pattern = _make_pattern(
                instance_data=instance_data,
                context=context,
                pattern_id=actual_pattern_id,
                vessel_code=candidate.vessel_code,
                is_virtual=False,
                schedule=candidate.schedule,
                requires_pattern_ids=actual_requires,
                depth=1,
                priority=500_000.0 + candidate.score,
                source_fragment_id=fragment.fragment_id,
                target_position_key=fragment.position_key,
                split_mode=candidate.split_mode,
            )
            bundle = [actual_pattern]
            if suffix_pattern is not None:
                diagnostics["virtual_suffix_fallbacks"] += 1
                bundle.append(suffix_pattern)
            if len(patterns) + len(bundle) > MAX_ITERATION_CANDIDATES:
                return patterns, diagnostics
            patterns.extend(bundle)
            if len(patterns) >= MAX_ITERATION_CANDIDATES:
                return patterns, diagnostics

    return patterns, diagnostics


def _generate_partial_virtual_primary_patterns(
    instance_data: InstanceData,
    context: CoverageContext,
    incumbent_solution: CascadingSolution,
    iteration: int,
) -> tuple[list[Pattern], Counter[str]]:
    patterns: list[Pattern] = []
    diagnostics: Counter[str] = Counter()
    serial = 0

    for virtual_code, position_key, virtual_schedule in _virtual_fragments(incumbent_solution):
        handover_splits = _enumerate_virtual_handover_splits(virtual_schedule)
        diagnostics["handover_splits"] += len(handover_splits)
        for split_index, split in enumerate(handover_splits, start=1):
            prefix_pattern_id = (
                f"virtual-alns-prefix-i{iteration}:"
                f"{virtual_code}:{split_index}:{_safe_name(split.mode)}"
            )
            primary_patterns: list[Pattern] = []
            for vessel_code, vessel_schedule in incumbent_solution.vessel_schedules.items():
                if _schedule_coverage_keys(vessel_schedule, context.fixed_positions):
                    diagnostics["primary_has_fixed_service"] += 1
                    continue
                primary_schedule = _try_assign_service_as_primary(
                    instance_data,
                    vessel_code,
                    list(vessel_schedule),
                    position_key,
                    split.suffix,
                )
                if primary_schedule is None:
                    diagnostics["primary_infeasible"] += 1
                    continue
                serial += 1
                primary_patterns.append(
                    _make_pattern(
                        instance_data=instance_data,
                        context=context,
                        pattern_id=(
                            f"actual-alns-partial-primary-i{iteration}:"
                            f"{vessel_code}:{virtual_code}:{split_index}:{_safe_name(split.mode)}:{serial}"
                        ),
                        vessel_code=vessel_code,
                        is_virtual=False,
                        schedule=primary_schedule,
                        requires_pattern_ids=frozenset({prefix_pattern_id}),
                        depth=1,
                        priority=650_000.0 + len(_schedule_coverage_keys(split.suffix, {position_key})) * 100.0,
                        source_fragment_id=f"virtual-suffix:{virtual_code}:{split_index}",
                        target_position_key=position_key,
                        split_mode=split.mode,
                    )
                )

            primary_patterns.sort(key=lambda pattern: (pattern.cost, -len(pattern.coverage_keys), pattern.pattern_id))
            primary_patterns = primary_patterns[:3]
            if not primary_patterns:
                continue

            patterns.append(
                _make_pattern(
                    instance_data=instance_data,
                    context=context,
                    pattern_id=prefix_pattern_id,
                    vessel_code=f"MIPV6_PARTIAL_PREFIX_{_safe_name(virtual_code)}_{iteration}_{split_index}",
                    is_virtual=True,
                    schedule=split.prefix,
                    depth=1,
                    priority=450_000.0,
                    source_fragment_id=f"virtual-prefix:{virtual_code}:{split_index}",
                    target_position_key=position_key,
                    split_mode=split.mode,
                )
            )
            patterns.extend(primary_patterns)
            if len(patterns) >= MAX_ITERATION_CANDIDATES:
                return patterns[:MAX_ITERATION_CANDIDATES], diagnostics

    return patterns[:MAX_ITERATION_CANDIDATES], diagnostics


def _generate_actual_prefix_virtual_suffix_patterns(
    instance_data: InstanceData,
    context: CoverageContext,
    incumbent_solution: CascadingSolution,
    iteration: int,
) -> tuple[list[Pattern], Counter[str]]:
    patterns: list[Pattern] = []
    diagnostics: Counter[str] = Counter()
    serial = 0

    for virtual_code, position_key, virtual_schedule in _virtual_fragments(incumbent_solution):
        handover_splits = _enumerate_virtual_handover_splits(virtual_schedule)
        diagnostics["actual_prefix_splits"] += len(handover_splits)
        for split_index, split in enumerate(handover_splits, start=1):
            suffix_coverage_count = len(_schedule_coverage_keys(split.suffix, {position_key}))
            if suffix_coverage_count <= 0:
                diagnostics["actual_prefix_empty_suffix"] += 1
                continue

            prefix_candidates: list[tuple[float, Pattern, Pattern]] = []
            for vessel_code, vessel_schedule in incumbent_solution.vessel_schedules.items():
                if _schedule_coverage_keys(vessel_schedule, context.fixed_positions):
                    diagnostics["actual_prefix_has_fixed_service"] += 1
                    continue
                prefix_schedule = _try_assign_service_as_primary(
                    instance_data,
                    vessel_code,
                    list(vessel_schedule),
                    position_key,
                    split.prefix,
                )
                if prefix_schedule is None:
                    diagnostics["actual_prefix_infeasible"] += 1
                    continue

                serial += 1
                safe_virtual_code = _safe_name(virtual_code)
                safe_mode = _safe_name(split.mode)
                actual_pattern_id = (
                    f"actual-alns-prefix-primary-i{iteration}:"
                    f"{vessel_code}:{safe_virtual_code}:{split_index}:{safe_mode}:{serial}"
                )
                suffix_pattern_id = (
                    f"virtual-suffix-fallback-dalns-prefix-i{iteration}:"
                    f"{vessel_code}:{safe_virtual_code}:{split_index}:{safe_mode}:{serial}"
                )
                suffix_pattern = _make_virtual_suffix_fallback(
                    instance_data,
                    context,
                    pattern_id=suffix_pattern_id,
                    parent_pattern_id=actual_pattern_id,
                    vessel_code=vessel_code,
                    suffix_schedule=split.suffix,
                    priority=450_000.0 + suffix_coverage_count * 100.0,
                    source_fragment_id=f"actual-prefix-virtual-suffix:{virtual_code}:{split_index}",
                    split_mode=split.mode,
                )
                if suffix_pattern is None:
                    diagnostics["actual_prefix_missing_suffix_pattern"] += 1
                    continue

                prefix_coverage_count = len(_schedule_coverage_keys(split.prefix, {position_key}))
                actual_pattern = _make_pattern(
                    instance_data=instance_data,
                    context=context,
                    pattern_id=actual_pattern_id,
                    vessel_code=vessel_code,
                    is_virtual=False,
                    schedule=prefix_schedule,
                    requires_pattern_ids=frozenset({suffix_pattern_id}),
                    depth=1,
                    priority=620_000.0 + prefix_coverage_count * 100.0,
                    source_fragment_id=f"virtual-prefix:{virtual_code}:{split_index}",
                    target_position_key=position_key,
                    split_mode=split.mode,
                )
                combined_cost = actual_pattern.cost + suffix_pattern.cost
                prefix_candidates.append((combined_cost, actual_pattern, suffix_pattern))

            prefix_candidates.sort(
                key=lambda item: (
                    item[0],
                    -len(item[1].coverage_keys),
                    item[1].pattern_id,
                )
            )
            for _, actual_pattern, suffix_pattern in prefix_candidates[:3]:
                if len(patterns) + 2 > MAX_ITERATION_CANDIDATES:
                    return patterns, diagnostics
                patterns.extend([actual_pattern, suffix_pattern])
                diagnostics["actual_prefix_virtual_suffix_pairs"] += 1

    return patterns, diagnostics


def _generate_alns_patterns(
    instance_data: InstanceData,
    context: CoverageContext,
    incumbent_solution: CascadingSolution,
    operator_name: str,
    iteration: int,
) -> tuple[list[Pattern], Counter[str]]:
    if operator_name == "selectable_switch":
        return _generate_selectable_switch_patterns(instance_data, context, incumbent_solution, iteration)
    if operator_name == "partial_virtual_primary":
        return _generate_partial_virtual_primary_patterns(instance_data, context, incumbent_solution, iteration)
    if operator_name == "actual_prefix_virtual_suffix":
        return _generate_actual_prefix_virtual_suffix_patterns(instance_data, context, incumbent_solution, iteration)
    raise ValueError(f"wsgoh/twostage_support/cascade_chain: unknown legacy ALNS operator {operator_name!r}.")


def _choose_operator(iteration: int, forced_operator: str | None) -> str:
    if forced_operator is not None:
        return forced_operator
    return "selectable_switch" if iteration % 2 == 1 else "partial_virtual_primary"


def _solve_master_lexicographic(
    gp,
    GRB,
    instance_data: InstanceData,
    context: CoverageContext,
    patterns: list[Pattern],
    mip_timelimit: int,
    warm_start_ids: set[str],
    iteration_label: str,
    solve_mode: str,
) -> MasterResult:
    if not patterns:
        raise ValueError("wsgoh/twostage_support/cascade_chain: pattern pool is empty.")
    if not context.required_coverage:
        raise ValueError("wsgoh/twostage_support/cascade_chain: no required coverage keys were generated.")

    model = gp.Model(f"wsgoh_twostage_cascade_chain_{_safe_name(iteration_label)}")
    model.Params.OutputFlag = 0
    if mip_timelimit > 0:
        model.Params.TimeLimit = mip_timelimit
    if solve_mode == "norel":
        model.Params.NoRelHeurTime = max(1, mip_timelimit)
        model.Params.NodeLimit = 0
        model.Params.MIPFocus = 1
        model.Params.Heuristics = 1.0
    elif solve_mode != "full":
        raise ValueError(f"wsgoh/twostage_support/cascade_chain: unknown solve mode {solve_mode!r}.")

    y = {
        pattern.pattern_id: model.addVar(vtype=GRB.BINARY, name=_gurobi_name("y", pattern.pattern_id))
        for pattern in patterns
    }
    z = {
        key: model.addVar(
            vtype=GRB.BINARY,
            lb=0,
            ub=1,
            name=_gurobi_name("z", f"{key[0]}_{key[1]}_{key[2]}"),
        )
        for key in sorted(context.selectable_positions)
    }

    actual_patterns_by_vessel: dict[str, list[str]] = {}
    patterns_by_coverage: dict[CoverageKey, list[str]] = {}
    pattern_ids = {pattern.pattern_id for pattern in patterns}
    for pattern in patterns:
        if not pattern.is_virtual:
            actual_patterns_by_vessel.setdefault(pattern.vessel_code, []).append(pattern.pattern_id)
        for coverage_key in pattern.coverage_keys:
            patterns_by_coverage.setdefault(coverage_key, []).append(pattern.pattern_id)
        missing_requirements = pattern.requires_pattern_ids - pattern_ids
        if missing_requirements:
            raise ValueError(
                f"wsgoh/twostage_support/cascade_chain: pattern {pattern.pattern_id!r} has missing parent patterns "
                f"{sorted(missing_requirements)!r}."
            )

    for vessel in instance_data.vessels:
        vessel_code = vessel["vessel_code"]
        vessel_pattern_ids = actual_patterns_by_vessel.get(vessel_code, [])
        if not vessel_pattern_ids:
            raise ValueError(
                f"wsgoh/twostage_support/cascade_chain: missing actual-vessel pattern for {vessel_code!r}."
            )
        model.addConstr(
            gp.quicksum(y[pattern_id] for pattern_id in vessel_pattern_ids) == 1,
            name=_gurobi_name("one_pattern", vessel_code),
        )

    for pattern in patterns:
        for required_pattern_id in sorted(pattern.requires_pattern_ids):
            model.addConstr(
                y[pattern.pattern_id] <= y[required_pattern_id],
                name=_gurobi_name("requires", f"{pattern.pattern_id}_{required_pattern_id}"),
            )

    for lane in instance_data.service_lanes:
        lane_code = lane["lane_code"]
        for version in lane["versions"]:
            available_positions = version["available_positions"] or []
            if not available_positions:
                continue
            proforma_name = version["proforma_name"]
            required_selectable_count = int(version["own_vessel_count"]) - len(version["declared_positions"])
            model.addConstr(
                gp.quicksum(z[(lane_code, proforma_name, int(position_no))] for position_no in available_positions)
                == required_selectable_count,
                name=_gurobi_name("declare", f"{lane_code}_{proforma_name}"),
            )

    for coverage_key in sorted(context.required_coverage):
        position_key = (coverage_key[0], coverage_key[1], coverage_key[2])
        rhs = z[position_key] if coverage_key in context.selectable_coverage else 1.0
        model.addConstr(
            gp.quicksum(y[pattern_id] for pattern_id in patterns_by_coverage.get(coverage_key, [])) == rhs,
            name=_gurobi_name(
                "cover",
                (
                    f"{coverage_key[0]}_{coverage_key[1]}_{coverage_key[2]}_"
                    f"{coverage_key[3]}_{int(coverage_key[5].timestamp())}"
                ),
            ),
        )

    virtual_portstay_expr = gp.quicksum(
        (len(pattern.coverage_keys) if pattern.is_virtual else 0) * y[pattern.pattern_id]
        for pattern in patterns
    )
    total_cost_expr = gp.quicksum(pattern.cost * y[pattern.pattern_id] for pattern in patterns)
    model.ModelSense = GRB.MINIMIZE
    model.setObjectiveN(
        virtual_portstay_expr,
        index=0,
        priority=2,
        weight=1.0,
        abstol=0.0,
        reltol=0.0,
        name="MinVirtualPortStay",
    )
    model.setObjectiveN(
        total_cost_expr,
        index=1,
        priority=1,
        weight=1.0,
        abstol=0.0,
        reltol=0.0,
        name="MinTotalCost",
    )

    for pattern in patterns:
        y[pattern.pattern_id].Start = 1.0 if pattern.pattern_id in warm_start_ids else 0.0
    for key, variable in z.items():
        variable.Start = float(context.initial_selectable_values.get(key, 0))

    model.optimize()
    status = _status_name(GRB, model.Status)
    if model.SolCount < 1:
        raise RuntimeError(
            f"wsgoh/twostage_support/cascade_chain: Gurobi did not return a feasible solution. status={status}."
        )

    selected_pattern_ids = {
        pattern.pattern_id for pattern in patterns if y[pattern.pattern_id].X > 0.5
    }
    selected_patterns = [
        pattern for pattern in patterns if pattern.pattern_id in selected_pattern_ids
    ]
    selected_actual_schedules: dict[str, dict[str, Any]] = {}
    selected_virtual_schedules: dict[str, dict[str, Any]] = {}
    for pattern in selected_patterns:
        if pattern.is_virtual:
            selected_virtual_schedules[pattern.vessel_code] = pattern.schedule_payload
        else:
            selected_actual_schedules[pattern.vessel_code] = pattern.schedule_payload

    missing_actual_vessels = sorted(
        vessel["vessel_code"]
        for vessel in instance_data.vessels
        if vessel["vessel_code"] not in selected_actual_schedules
    )
    if missing_actual_vessels:
        raise RuntimeError(
            "wsgoh/twostage_support/cascade_chain: selected solution is missing actual vessels: "
            f"{missing_actual_vessels!r}."
        )

    selected_declared_positions = [
        {
            "lane_code": lane_code,
            "proforma_name": proforma_name,
            "declared_position_no": position_no,
        }
        for (lane_code, proforma_name, position_no), variable in sorted(z.items())
        if variable.X > 0.5
    ]

    selected_cost = sum(pattern.cost for pattern in selected_patterns)
    selected_virtual_portstays = sum(
        len(pattern.coverage_keys) for pattern in selected_patterns if pattern.is_virtual
    )

    return MasterResult(
        solution=CascadingSolution(
            declared_positions=selected_declared_positions,
            vessel_schedules=selected_actual_schedules,
            virtual_vessel_schedules=selected_virtual_schedules,
            num_virtual_vessels_used=len(selected_virtual_schedules),
        ),
        objective=float(selected_cost),
        virtual_portstay_objective=int(selected_virtual_portstays),
        status=status,
        selected_patterns=selected_patterns,
        selected_pattern_ids=selected_pattern_ids,
        selected_family_counts=Counter(_pattern_family(pattern) for pattern in selected_patterns),
    )


def _solve_master(*args, **kwargs) -> MasterResult:
    return _solve_master_lexicographic(*args, **kwargs)


def _virtual_portstay_count(solution: CascadingSolution) -> int:
    return sum(
        1
        for schedule in solution.virtual_vessel_schedules.values()
        for event in schedule
        if isinstance(event, PortStay)
    )


def _format_selected_virtual_coverage(patterns: list[Pattern]) -> str:
    virtual_patterns = [pattern for pattern in patterns if pattern.is_virtual]
    if not virtual_patterns:
        return "(none)"
    return "; ".join(
        f"{pattern.pattern_id} -> {_format_coverage_positions(pattern)}"
        for pattern in sorted(virtual_patterns, key=lambda item: item.pattern_id)
    )

def _clone_schedule_lite(schedule: Iterable[VesselScheduleEvent]) -> list[VesselScheduleEvent]:
    return [VesselScheduleEvent.coerce(event.to_dict()) for event in schedule]


def _position_key_from_coverage(coverage_keys: frozenset[CoverageKey]) -> PositionKey | None:
    positions = {(key[0], key[1], key[2]) for key in coverage_keys}
    return next(iter(positions)) if len(positions) == 1 else None


def _schedule_max_speed(schedule: Iterable[VesselScheduleEvent]) -> float:
    max_speed = 0.0
    for event in schedule:
        try:
            from_port = event_start_port_code(event)
            to_port = event_end_port_code(event)
            start_time = event_start_time(event)
            end_time = event_end_time(event)
        except (AttributeError, TypeError):
            continue
        if from_port == to_port:
            continue
        hours = (end_time - start_time).total_seconds() / 3600.0
        if hours <= 0.0:
            return float("inf")
        try:
            distance = float(lookup_distance(from_port, to_port))
        except Exception:
            return float("inf")
        max_speed = max(max_speed, distance / (hours + 1e-5))
    return max_speed


def _schedule_speed_ok(schedule: Iterable[VesselScheduleEvent]) -> bool:
    return _schedule_max_speed(schedule) <= MAX_REPOSITION_SPEED_KNOTS + 1e-6


def _virtual_fragment_cost_estimate(
    instance_data: InstanceData,
    context: CoverageContext,
    vessel_code: str,
    schedule: list[VesselScheduleEvent],
) -> float:
    if not schedule:
        return 0.0
    try:
        return float(
            _estimate_pattern_cost(
                instance_data,
                context.declared_positions_payload,
                vessel_code,
                {"events": [event.to_dict() for event in schedule]},
                is_virtual=True,
            )
        )
    except Exception:
        return float(sum(1 for event in schedule if isinstance(event, PortStay)))


def _make_hole(
    instance_data: InstanceData,
    context: CoverageContext,
    *,
    hole_id: str,
    source_virtual_code: str,
    source_virtual_pattern_id: str,
    schedule: list[VesselScheduleEvent],
    prefix_schedule: list[VesselScheduleEvent] | None = None,
    split_mode: str = "full",
) -> VirtualHole | None:
    if not schedule:
        return None
    coverage_keys = _schedule_coverage_keys(schedule, context.coverage_positions)
    if not coverage_keys:
        return None
    position_key = _position_key_from_schedule(schedule) or _position_key_from_coverage(coverage_keys)
    if position_key is None:
        return None
    return VirtualHole(
        hole_id=hole_id,
        lane_code=position_key[0],
        version_code=position_key[1],
        position_no=position_key[2],
        schedule=tuple(_clone_schedule_lite(schedule)),
        prefix_schedule=tuple(_clone_schedule_lite(prefix_schedule or [])),
        start_time=event_start_time(schedule[0]),
        end_time=event_end_time(schedule[-1]),
        start_port_code=event_start_port_code(schedule[0]),
        end_port_code=event_end_port_code(schedule[-1]),
        coverage_keys=coverage_keys,
        portstay_count=len(coverage_keys),
        source_virtual_code=source_virtual_code,
        source_virtual_pattern_id=source_virtual_pattern_id,
        split_mode=split_mode,
        opportunity_cost_estimate=_virtual_fragment_cost_estimate(
            instance_data,
            context,
            source_virtual_code,
            schedule,
        ),
    )


def _extract_virtual_holes(
    instance_data: InstanceData,
    context: CoverageContext,
    solution: CascadingSolution,
) -> list[VirtualHole]:
    holes: list[VirtualHole] = []
    seen_coverage: set[frozenset[CoverageKey]] = set()
    for virtual_code, raw_schedule in sorted(solution.virtual_vessel_schedules.items()):
        schedule = _clone_schedule_lite(raw_schedule)
        full_hole = _make_hole(
            instance_data,
            context,
            hole_id=f"hole:{virtual_code}:full",
            source_virtual_code=virtual_code,
            source_virtual_pattern_id=f"solution-virtual:{virtual_code}",
            schedule=schedule,
            split_mode="full",
        )
        if full_hole is not None:
            holes.append(full_hole)
            seen_coverage.add(full_hole.coverage_keys)

        split_count = 0
        for split in _enumerate_virtual_handover_splits(schedule):
            if split_count >= MAX_HANDOVER_OPTIONS_PER_VESSEL_HOLE:
                break
            suffix = _clone_schedule_lite(split.suffix)
            prefix = _clone_schedule_lite(split.prefix)
            split_hole = _make_hole(
                instance_data,
                context,
                hole_id=f"hole:{virtual_code}:split:{_safe_name(split.mode)}",
                source_virtual_code=virtual_code,
                source_virtual_pattern_id=f"solution-virtual:{virtual_code}",
                schedule=suffix,
                prefix_schedule=prefix,
                split_mode=split.mode,
            )
            if split_hole is None or split_hole.coverage_keys in seen_coverage:
                continue
            holes.append(split_hole)
            seen_coverage.add(split_hole.coverage_keys)
            split_count += 1

    holes.sort(
        key=lambda hole: (
            hole.portstay_count,
            hole.opportunity_cost_estimate,
            -hole.start_time.timestamp(),
            hole.hole_id,
        ),
        reverse=True,
    )
    return holes[:MAX_TARGET_HOLES_PER_ROUND]


def _inlane_blocks_lite(schedule: list[VesselScheduleEvent]) -> list[tuple[int, int, list[VesselScheduleEvent]]]:
    blocks: list[tuple[int, int, list[VesselScheduleEvent]]] = []
    start_index: int | None = None
    for index, event in enumerate(schedule):
        if isinstance(event, InLaneEvent):
            if start_index is None:
                start_index = index
            continue
        if start_index is not None:
            blocks.append((start_index, index - 1, schedule[start_index:index]))
            start_index = None
    if start_index is not None:
        blocks.append((start_index, len(schedule) - 1, schedule[start_index:]))
    return blocks


def _extract_actual_source_blocks(
    context: CoverageContext,
    solution: CascadingSolution,
) -> list[SourceBlock]:
    blocks: list[SourceBlock] = []
    for vessel_code, raw_schedule in sorted(solution.vessel_schedules.items()):
        schedule = _clone_schedule_lite(raw_schedule)
        for first_index, last_index, block_schedule in _inlane_blocks_lite(schedule):
            coverage_keys = _schedule_coverage_keys(block_schedule, context.coverage_positions)
            if not coverage_keys:
                continue
            position_key = _position_key_from_schedule(block_schedule) or _position_key_from_coverage(coverage_keys)
            locked = bool(_schedule_coverage_keys(block_schedule, context.fixed_positions))
            blocks.append(
                SourceBlock(
                    block_id=f"source:{vessel_code}:{first_index}:{last_index}:{_safe_name(position_key)}",
                    vessel_code=vessel_code,
                    lane_code=position_key[0] if position_key else None,
                    version_code=position_key[1] if position_key else None,
                    position_no=position_key[2] if position_key else None,
                    start_time=event_start_time(block_schedule[0]),
                    end_time=event_end_time(block_schedule[-1]),
                    start_port_code=event_start_port_code(block_schedule[0]),
                    end_port_code=event_end_port_code(block_schedule[-1]),
                    coverage_keys=coverage_keys,
                    portstay_count=len(coverage_keys),
                    selected_pattern_id=f"solution-actual:{vessel_code}",
                    event_slice=(first_index, last_index),
                    is_locked=locked,
                    can_be_displaced=not locked,
                )
            )
    return blocks


def _extract_free_vessels(context: CoverageContext, solution: CascadingSolution) -> list[str]:
    free_vessels: list[str] = []
    for vessel_code, schedule in sorted(solution.vessel_schedules.items()):
        if not _schedule_coverage_keys(schedule, context.fixed_positions):
            free_vessels.append(vessel_code)
    return free_vessels


def _make_virtual_fragment_pattern(
    instance_data: InstanceData,
    context: CoverageContext,
    *,
    pattern_id: str,
    vessel_code: str,
    schedule: list[VesselScheduleEvent],
    priority: float,
    source_fragment_id: str,
    requires_pattern_ids: frozenset[str] = frozenset(),
    split_mode: str | None = None,
) -> Pattern | None:
    if not schedule:
        return None
    coverage_keys = _schedule_coverage_keys(schedule, context.coverage_positions)
    if not coverage_keys:
        return None
    position_key = _position_key_from_schedule(schedule) or _position_key_from_coverage(coverage_keys)
    if position_key is None:
        return None
    return _make_pattern(
        instance_data=instance_data,
        context=context,
        pattern_id=pattern_id,
        vessel_code=vessel_code,
        is_virtual=True,
        schedule=schedule,
        requires_pattern_ids=requires_pattern_ids,
        depth=2,
        priority=priority,
        source_fragment_id=source_fragment_id,
        target_position_key=position_key,
        split_mode=split_mode,
    )


def _target_prefix_pattern(
    instance_data: InstanceData,
    context: CoverageContext,
    hole: VirtualHole,
    round_index: int,
    serial: int,
) -> Pattern | None:
    if not hole.prefix_schedule:
        return None
    return _make_virtual_fragment_pattern(
        instance_data,
        context,
        pattern_id=f"cascade_chain_virtual_target_prefix:{round_index}:{serial}:{_safe_name(hole.hole_id)}",
        vessel_code=f"MIPLITE_TARGET_PREFIX_{round_index}_{serial}_{_safe_name(hole.hole_id)}",
        schedule=_clone_schedule_lite(hole.prefix_schedule),
        priority=720_000.0 + hole.portstay_count,
        source_fragment_id=hole.hole_id,
        split_mode=f"target_prefix:{hole.split_mode}",
    )


def _source_suffix_pattern_and_hole(
    instance_data: InstanceData,
    context: CoverageContext,
    *,
    parent_pattern_id: str,
    source_schedule: list[VesselScheduleEvent],
    round_index: int,
    serial: int,
    source_fragment_id: str,
) -> tuple[Pattern | None, VirtualHole | None]:
    if not source_schedule:
        return None, None
    virtual_pattern = _make_virtual_fragment_pattern(
        instance_data,
        context,
        pattern_id=f"cascade_chain_virtual_source:{round_index}:{serial}:{_safe_name(source_fragment_id)}",
        vessel_code=f"MIPLITE_SOURCE_{round_index}_{serial}_{_safe_name(source_fragment_id)}",
        schedule=_clone_schedule_lite(source_schedule),
        priority=710_000.0,
        source_fragment_id=source_fragment_id,
        requires_pattern_ids=frozenset({parent_pattern_id}),
        split_mode="source_suffix",
    )
    if virtual_pattern is None:
        return None, None
    source_hole = _make_hole(
        instance_data,
        context,
        hole_id=f"source-hole:{round_index}:{serial}:{_safe_name(source_fragment_id)}",
        source_virtual_code=virtual_pattern.vessel_code,
        source_virtual_pattern_id=virtual_pattern.pattern_id,
        schedule=_clone_schedule_lite(source_schedule),
        split_mode="source_suffix",
    )
    return virtual_pattern, source_hole


def _rank_chain_candidates(
    hole: VirtualHole,
    source_blocks: list[SourceBlock],
    free_vessels: list[str],
) -> list[tuple[str, str, SourceBlock | None, tuple[float, ...]]]:
    candidates: list[tuple[str, str, SourceBlock | None, tuple[float, ...]]] = []
    for vessel_code in free_vessels:
        candidates.append(("free", vessel_code, None, (1.0, float(hole.portstay_count), 0.0)))
    for block in source_blocks:
        if not block.can_be_displaced or block.start_time > hole.start_time:
            continue
        candidates.append(
            (
                "displace",
                block.vessel_code,
                block,
                (0.0, -float(block.portstay_count), float(hole.portstay_count)),
            )
        )
    candidates.sort(key=lambda item: (item[3], item[1]), reverse=True)
    return candidates[:MAX_CANDIDATE_VESSELS_PER_HOLE]


def _make_handover_summary(schedule: list[VesselScheduleEvent], target_hole: VirtualHole) -> HandoverOption | None:
    best: HandoverOption | None = None
    for event in schedule:
        try:
            out_time = event_end_time(event)
            out_port = event_end_port_code(event)
        except (AttributeError, TypeError):
            continue
        if out_time > target_hole.start_time:
            continue
        hours = (target_hole.start_time - out_time).total_seconds() / 3600.0
        if out_port == target_hole.start_port_code:
            speed = 0.0
            distance = 0.0
        elif hours > 0.0:
            try:
                distance = float(lookup_distance(out_port, target_hole.start_port_code))
            except Exception:
                continue
            speed = distance / (hours + 1e-5)
        else:
            continue
        if speed > MAX_REPOSITION_SPEED_KNOTS + 1e-6:
            continue
        option = HandoverOption(
            ts_out_port_code=out_port,
            ts_out_time=out_time,
            ts_in_port_code=target_hole.start_port_code,
            ts_in_time=target_hole.start_time,
            required_speed=speed,
            sailing_hours=hours,
            distance_nm=distance,
            slack_hours=hours,
        )
        if best is None or option.ts_out_time > best.ts_out_time:
            best = option
    return best


def _build_free_vessel_move(
    instance_data: InstanceData,
    context: CoverageContext,
    current_solution: CascadingSolution,
    hole: VirtualHole,
    vessel_code: str,
    round_index: int,
    serial: int,
    diagnostics: Counter[str],
) -> DisplacementMove | None:
    position_key: PositionKey = (hole.lane_code, hole.version_code, hole.position_no)
    diagnostics["candidate_vessels_screened"] += 1
    candidate_schedule = _try_assign_service_as_primary(
        instance_data,
        vessel_code,
        _clone_schedule_lite(current_solution.vessel_schedules[vessel_code]),
        position_key,
        _clone_schedule_lite(hole.schedule),
    )
    if candidate_schedule is None:
        diagnostics["free_primary_infeasible"] += 1
        return None
    if not _schedule_speed_ok(candidate_schedule):
        diagnostics["required_speed_too_high"] += 1
        return None
    pattern_id = f"cascade_chain_actual:{round_index}:{serial}:free:{_safe_name(vessel_code)}:{_safe_name(hole.hole_id)}"
    actual_pattern = _make_pattern(
        instance_data=instance_data,
        context=context,
        pattern_id=pattern_id,
        vessel_code=vessel_code,
        is_virtual=False,
        schedule=candidate_schedule,
        depth=1,
        priority=850_000.0 + hole.portstay_count * 100.0,
        source_fragment_id=hole.hole_id,
        target_position_key=position_key,
        split_mode=f"free:{hole.split_mode}",
    )
    residuals: list[Pattern] = []
    prefix_pattern = _target_prefix_pattern(instance_data, context, hole, round_index, serial)
    if prefix_pattern is not None:
        residuals.append(prefix_pattern)
    diagnostics["displacement_moves_feasible"] += 1
    return DisplacementMove(
        move_id=pattern_id,
        vessel_code=vessel_code,
        target_hole=hole,
        source_block=None,
        source_hole_created=None,
        handover=None,
        target_taken_portstay_count=hole.portstay_count,
        source_created_portstay_count=0,
        delta_virtual_portstay=hole.portstay_count,
        estimated_cost_delta=actual_pattern.cost - hole.opportunity_cost_estimate,
        required_speed=_schedule_max_speed(candidate_schedule),
        actual_pattern_candidate=actual_pattern,
        residual_virtual_patterns=residuals,
    )


def _build_displacement_moves(
    instance_data: InstanceData,
    context: CoverageContext,
    current_solution: CascadingSolution,
    hole: VirtualHole,
    source_block: SourceBlock,
    round_index: int,
    serial_start: int,
    diagnostics: Counter[str],
) -> list[DisplacementMove]:
    position_key: PositionKey = (hole.lane_code, hole.version_code, hole.position_no)
    fragment = ServiceFragment(
        fragment_id=hole.hole_id,
        position_key=position_key,
        schedule=_clone_schedule_lite(hole.schedule),
        source="virtual_hole",
        depth=0,
        priority=hole.opportunity_cost_estimate + hole.portstay_count * 100.0,
    )
    diagnostics["candidate_vessels_screened"] += 1
    result = _switch_candidates_for_fragment(
        instance_data,
        source_block.vessel_code,
        _clone_schedule_lite(current_solution.vessel_schedules[source_block.vessel_code]),
        fragment,
        context.coverage_positions,
    )
    diagnostics.update(result.rejections)
    moves: list[DisplacementMove] = []
    sorted_candidates = sorted(
        result.candidates,
        key=lambda item: (item.score, -len(_schedule_coverage_keys(item.dropped_suffix, context.coverage_positions))),
        reverse=True,
    )[:MAX_HANDOVER_OPTIONS_PER_VESSEL_HOLE]
    for offset, candidate in enumerate(sorted_candidates):
        serial = serial_start + offset
        if not _schedule_speed_ok(candidate.schedule):
            diagnostics["required_speed_too_high"] += 1
            continue
        dropped_coverage = _schedule_coverage_keys(candidate.dropped_suffix, context.coverage_positions)
        if _schedule_coverage_keys(candidate.dropped_suffix, context.fixed_positions):
            diagnostics["source_block_locked"] += 1
            continue
        if candidate.dropped_suffix and not dropped_coverage:
            diagnostics["source_suffix_invalid"] += 1
            continue
        pattern_id = (
            f"cascade_chain_actual:{round_index}:{serial}:displace:"
            f"{_safe_name(source_block.vessel_code)}:{_safe_name(hole.hole_id)}"
        )
        actual_pattern = _make_pattern(
            instance_data=instance_data,
            context=context,
            pattern_id=pattern_id,
            vessel_code=source_block.vessel_code,
            is_virtual=False,
            schedule=candidate.schedule,
            depth=1,
            priority=820_000.0 + hole.portstay_count * 100.0 - len(dropped_coverage),
            source_fragment_id=hole.hole_id,
            target_position_key=position_key,
            split_mode=f"displace:{candidate.split_mode}",
        )
        residuals: list[Pattern] = []
        prefix_pattern = _target_prefix_pattern(instance_data, context, hole, round_index, serial)
        if prefix_pattern is not None:
            residuals.append(prefix_pattern)
        source_pattern, source_hole = _source_suffix_pattern_and_hole(
            instance_data,
            context,
            parent_pattern_id=actual_pattern.pattern_id,
            source_schedule=_clone_schedule_lite(candidate.dropped_suffix),
            round_index=round_index,
            serial=serial,
            source_fragment_id=f"{source_block.block_id}:{candidate.split_mode}",
        )
        if source_pattern is not None:
            residuals.append(source_pattern)
        diagnostics["handover_options_feasible"] += 1
        diagnostics["displacement_moves_feasible"] += 1
        moves.append(
            DisplacementMove(
                move_id=pattern_id,
                vessel_code=source_block.vessel_code,
                target_hole=hole,
                source_block=source_block,
                source_hole_created=source_hole,
                handover=_make_handover_summary(candidate.schedule, hole),
                target_taken_portstay_count=hole.portstay_count,
                source_created_portstay_count=len(dropped_coverage),
                delta_virtual_portstay=hole.portstay_count - len(dropped_coverage),
                estimated_cost_delta=actual_pattern.cost - hole.opportunity_cost_estimate,
                required_speed=_schedule_max_speed(candidate.schedule),
                actual_pattern_candidate=actual_pattern,
                residual_virtual_patterns=residuals,
            )
        )
    return moves


def _dominance_filter_moves(moves: list[DisplacementMove], diagnostics: Counter[str]) -> list[DisplacementMove]:
    kept: list[DisplacementMove] = []
    for move in moves:
        dominated = False
        for incumbent in kept:
            if (
                incumbent.delta_virtual_portstay >= move.delta_virtual_portstay
                and incumbent.estimated_cost_delta <= move.estimated_cost_delta + 1e-6
                and incumbent.required_speed <= move.required_speed + 1e-6
                and incumbent.source_created_portstay_count <= move.source_created_portstay_count
            ):
                dominated = True
                diagnostics["dominated_move"] += 1
                break
        if dominated:
            continue
        kept = [
            incumbent
            for incumbent in kept
            if not (
                move.delta_virtual_portstay >= incumbent.delta_virtual_portstay
                and move.estimated_cost_delta <= incumbent.estimated_cost_delta + 1e-6
                and move.required_speed <= incumbent.required_speed + 1e-6
                and move.source_created_portstay_count <= incumbent.source_created_portstay_count
            )
        ]
        kept.append(move)
    return kept


def _generate_moves_for_hole(
    instance_data: InstanceData,
    context: CoverageContext,
    current_solution: CascadingSolution,
    hole: VirtualHole,
    source_blocks: list[SourceBlock],
    free_vessels: list[str],
    used_vessels: set[str],
    round_index: int,
    serial_counter: list[int],
    diagnostics: Counter[str],
) -> list[DisplacementMove]:
    moves: list[DisplacementMove] = []
    for candidate_type, vessel_code, source_block, _score in _rank_chain_candidates(hole, source_blocks, free_vessels):
        if vessel_code in used_vessels:
            continue
        serial_counter[0] += 1
        serial = serial_counter[0]
        if candidate_type == "free":
            move = _build_free_vessel_move(
                instance_data,
                context,
                current_solution,
                hole,
                vessel_code,
                round_index,
                serial,
                diagnostics,
            )
            if move is not None:
                moves.append(move)
            continue
        if source_block is None:
            continue
        if source_block.is_locked:
            diagnostics["source_block_locked"] += 1
            continue
        moves.extend(
            _build_displacement_moves(
                instance_data,
                context,
                current_solution,
                hole,
                source_block,
                round_index,
                serial * 100,
                diagnostics,
            )
        )
    diagnostics["handover_options_generated"] += len(moves)
    moves = _dominance_filter_moves(moves, diagnostics)
    moves.sort(
        key=lambda move: (
            move.delta_virtual_portstay,
            -move.source_created_portstay_count,
            -move.estimated_cost_delta,
            -move.required_speed,
            move.move_id,
        ),
        reverse=True,
    )
    return moves[:MAX_MOVE_VARIANTS_PER_HOLE]


def _materialize_chain(chain_id: str, initial_hole_id: str, moves: list[DisplacementMove]) -> CascadeChain | None:
    pattern_by_id: dict[str, Pattern] = {}
    net_virtual_reduction = 0
    estimated_cost_delta = 0.0
    terminal_hole: VirtualHole | None = None
    terminal_closed_by_free_vessel = False
    terminal_closed_by_virtual = False
    for move in moves:
        net_virtual_reduction += move.delta_virtual_portstay
        estimated_cost_delta += move.estimated_cost_delta
        pattern_by_id[move.actual_pattern_candidate.pattern_id] = move.actual_pattern_candidate
        for residual in move.residual_virtual_patterns:
            pattern_by_id[residual.pattern_id] = residual
        terminal_hole = move.source_hole_created
        terminal_closed_by_free_vessel = move.source_hole_created is None
        terminal_closed_by_virtual = move.source_hole_created is not None
    if net_virtual_reduction <= 0:
        return None
    return CascadeChain(
        chain_id=chain_id,
        initial_hole_id=initial_hole_id,
        moves=list(moves),
        terminal_hole=terminal_hole,
        terminal_closed_by_free_vessel=terminal_closed_by_free_vessel,
        terminal_closed_by_virtual=terminal_closed_by_virtual,
        net_virtual_reduction=net_virtual_reduction,
        estimated_cost_delta=estimated_cost_delta,
        pattern_candidates=list(pattern_by_id.values()),
    )


def _search_chains_for_hole(
    instance_data: InstanceData,
    context: CoverageContext,
    current_solution: CascadingSolution,
    initial_hole: VirtualHole,
    source_blocks: list[SourceBlock],
    free_vessels: list[str],
    round_index: int,
    serial_counter: list[int],
    diagnostics: Counter[str],
) -> list[CascadeChain]:
    chains: list[CascadeChain] = []

    def dfs(hole: VirtualHole, depth: int, used_vessels: set[str], moves: list[DisplacementMove]) -> None:
        if len(chains) >= MAX_CHAINS_PER_HOLE:
            return
        if depth >= MAX_CASCADE_DEPTH:
            chain = _materialize_chain(
                f"chain:{round_index}:{len(chains) + 1}:{_safe_name(initial_hole.hole_id)}",
                initial_hole.hole_id,
                moves,
            )
            if chain is not None:
                chains.append(chain)
            return
        variants = _generate_moves_for_hole(
            instance_data,
            context,
            current_solution,
            hole,
            source_blocks,
            free_vessels,
            used_vessels,
            round_index,
            serial_counter,
            diagnostics,
        )
        for move in variants:
            next_moves = moves + [move]
            chain = _materialize_chain(
                f"chain:{round_index}:{len(chains) + 1}:{_safe_name(initial_hole.hole_id)}",
                initial_hole.hole_id,
                next_moves,
            )
            if chain is not None:
                chains.append(chain)
                if len(chains) >= MAX_CHAINS_PER_HOLE:
                    return
            if move.source_hole_created is not None:
                dfs(move.source_hole_created, depth + 1, used_vessels | {move.vessel_code}, next_moves)
                if len(chains) >= MAX_CHAINS_PER_HOLE:
                    return

    dfs(initial_hole, depth=0, used_vessels=set(), moves=[])
    chains.sort(
        key=lambda chain: (
            chain.net_virtual_reduction,
            -chain.estimated_cost_delta,
            -len(chain.pattern_candidates),
            chain.chain_id,
        ),
        reverse=True,
    )
    return chains[:MAX_CHAINS_PER_HOLE]


def generate_chain_patterns(
    instance_data: InstanceData,
    context: CoverageContext,
    current_solution: CascadingSolution,
    round_index: int,
) -> tuple[list[Pattern], Counter[str]]:
    diagnostics: Counter[str] = Counter()
    holes = _extract_virtual_holes(instance_data, context, current_solution)
    source_blocks = _extract_actual_source_blocks(context, current_solution)
    free_vessels = _extract_free_vessels(context, current_solution)
    diagnostics["virtual_hole_count"] = len(holes)
    diagnostics["source_block_count"] = len(source_blocks)
    diagnostics["free_vessel_count"] = len(free_vessels)
    serial_counter = [0]
    chains: list[CascadeChain] = []
    for hole in holes:
        diagnostics["target_holes_considered"] += 1
        chains.extend(
            _search_chains_for_hole(
                instance_data,
                context,
                current_solution,
                hole,
                source_blocks,
                free_vessels,
                round_index,
                serial_counter,
                diagnostics,
            )
        )
    chains.sort(
        key=lambda chain: (
            chain.net_virtual_reduction,
            -chain.estimated_cost_delta,
            -len(chain.pattern_candidates),
            chain.chain_id,
        ),
        reverse=True,
    )
    diagnostics["chains_generated"] = len(chains)
    diagnostics["chains_positive_reduction"] = sum(1 for chain in chains if chain.net_virtual_reduction > 0)

    pattern_by_id: dict[str, Pattern] = {}
    for chain in chains:
        for pattern in chain.pattern_candidates:
            pattern_by_id[pattern.pattern_id] = pattern
            if len(pattern_by_id) >= MAX_CHAIN_PATTERNS_PER_ROUND:
                break
        if len(pattern_by_id) >= MAX_CHAIN_PATTERNS_PER_ROUND:
            break
    patterns = list(pattern_by_id.values())
    diagnostics["chain_patterns_added"] = len(patterns)
    diagnostics["cascade_actual_patterns"] = sum(1 for pattern in patterns if not pattern.is_virtual)
    diagnostics["cascade_virtual_patterns"] = sum(1 for pattern in patterns if pattern.is_virtual)
    return patterns, diagnostics


def _lex_key(solution: CascadingSolution, cost: float) -> tuple[int, float]:
    return (_virtual_portstay_count(solution), cost)


# ============================================================================
# mip-lite configuration override
# ============================================================================

DESCRIPTION = (
    "Two-stage support: virtual-hole-driven chain generation with "
    "a lexicographic accumulated set-partitioning MIP seeded by yongs/only_virtual2."
)

INITIAL_HEURISTIC = "only_virtual2"
MAX_CHAIN_ROUNDS = 5
MAX_CASCADE_DEPTH = 2
MAX_TARGET_HOLES_PER_ROUND = 20
MAX_CANDIDATE_VESSELS_PER_HOLE = 6
MAX_HANDOVER_OPTIONS_PER_VESSEL_HOLE = 4
MAX_MOVE_VARIANTS_PER_HOLE = 12
MAX_CHAINS_PER_HOLE = 20
MAX_CHAIN_PATTERNS_PER_ROUND = 400
MAX_REPOSITION_SPEED_KNOTS = 20
MAX_ROUNDS = MAX_CHAIN_ROUNDS
NO_IMPROVEMENT_LIMIT = 5
INTERMEDIATE_NOREL_TIME_LIMIT = 8
FINAL_TIME_RESERVE = 30
RANDOM_SEED = 42
COOLING_RATE = 0.82
ADAPTIVE_REACTION = 0.35
MAX_REPAIR_PATTERNS = 180
REMOVAL_OPERATORS = ("random", "worst", "related", "lane")
MAX_POOL_PATTERNS = 5000
MAX_PATTERNS_PER_VESSEL = 120
MAX_ITERATIONS = MAX_ROUNDS
MAX_ITERATION_CANDIDATES = MAX_CHAIN_PATTERNS_PER_ROUND
FINAL_BRANCH_AND_BOUND_TIME_RESERVE = FINAL_TIME_RESERVE
FINAL_SOLVE_MODE = "full"

EXPERIMENT_MODE = "chain_ablation"
ENABLE_BASELINE_ACTUAL = True
ENABLE_BASELINE_VIRTUAL = True
ENABLE_VIRTUAL_FALLBACK = True
ENABLE_ACTUAL_CANDIDATE = False
ENABLE_ACTUAL_PRIMARY_VIRTUAL = False
ENABLE_CASCADE_CHAIN = True


# ============================================================================
# legacy v6 ALNS helpers kept unused by the lite chain controller
# ============================================================================

@dataclass(frozen=True)
class DestroyedState:
    operator_name: str
    removal_size: int
    removed_vessels: frozenset[str]
    focus_lanes: frozenset[str]
    focus_positions: frozenset[PositionKey]


def _current_options() -> SolverOptions:
    return SolverOptions(
        initial_heuristic=INITIAL_HEURISTIC,
        rounds=ROUNDS,
        max_iterations=MAX_ITERATIONS,
        no_improvement_limit=NO_IMPROVEMENT_LIMIT,
        max_pool_patterns=MAX_POOL_PATTERNS,
        max_patterns_per_vessel=MAX_PATTERNS_PER_VESSEL,
        max_iteration_candidates=MAX_ITERATION_CANDIDATES,
        intermediate_norel_time_limit=INTERMEDIATE_NOREL_TIME_LIMIT,
        final_branch_and_bound_time_reserve=FINAL_BRANCH_AND_BOUND_TIME_RESERVE,
        final_solve_mode=FINAL_SOLVE_MODE,
        random_seed=RANDOM_SEED,
    )


def _lite_base_options() -> SolverOptions:
    return SolverOptions(
        initial_heuristic="only_virtual2",
        rounds=None,
        max_iterations=MAX_ROUNDS,
        no_improvement_limit=NO_IMPROVEMENT_LIMIT,
        max_pool_patterns=MAX_POOL_PATTERNS,
        max_patterns_per_vessel=MAX_PATTERNS_PER_VESSEL,
        max_iteration_candidates=MAX_CHAIN_PATTERNS_PER_ROUND,
        intermediate_norel_time_limit=INTERMEDIATE_NOREL_TIME_LIMIT,
        final_branch_and_bound_time_reserve=FINAL_TIME_RESERVE,
        final_solve_mode="full",
        random_seed=RANDOM_SEED,
    )


def _remaining_seconds(start_time: float, timelimit: int) -> float:
    if timelimit <= 0:
        return 60.0
    return max(0.0, float(timelimit) - (time.monotonic() - start_time))


def _choose_operator(weights: dict[str, float], rng: random.Random) -> str:
    total_weight = sum(max(0.0, weight) for weight in weights.values())
    if total_weight <= 0.0:
        return rng.choice(tuple(weights))
    threshold = rng.random() * total_weight
    running = 0.0
    for operator_name, weight in weights.items():
        running += max(0.0, weight)
        if running >= threshold:
            return operator_name
    return next(reversed(weights))


def _selected_actual_patterns(result: MasterResult | None) -> list[Pattern]:
    if result is None:
        return []
    return [pattern for pattern in result.selected_patterns if not pattern.is_virtual]


def _selected_virtual_patterns(result: MasterResult | None) -> list[Pattern]:
    if result is None:
        return []
    return [pattern for pattern in result.selected_patterns if pattern.is_virtual]


def _coverage_lanes(patterns: Iterable[Pattern]) -> Counter[str]:
    lanes: Counter[str] = Counter()
    for pattern in patterns:
        for coverage_key in pattern.coverage_keys:
            lanes[coverage_key[0]] += 1
    return lanes


def _pattern_positions(pattern: Pattern) -> set[PositionKey]:
    return {(key[0], key[1], key[2]) for key in pattern.coverage_keys}


def _apply_deletion(
    operator_name: str,
    current_result: MasterResult | None,
    current_solution: CascadingSolution,
    rng: random.Random,
) -> DestroyedState:
    actual_patterns = _selected_actual_patterns(current_result)
    virtual_patterns = _selected_virtual_patterns(current_result)
    vessel_codes = sorted(current_solution.vessel_schedules)
    removal_size = max(1, min(5, len(vessel_codes) // 10 or 1))

    removed_vessels: set[str] = set()
    focus_lanes: set[str] = set()
    focus_positions: set[PositionKey] = set()

    if operator_name == "random":
        removed_vessels.update(rng.sample(vessel_codes, min(removal_size, len(vessel_codes))))

    elif operator_name == "worst":
        ranked = sorted(actual_patterns, key=lambda pattern: pattern.cost, reverse=True)
        removed_vessels.update(pattern.vessel_code for pattern in ranked[:removal_size])
        for pattern in virtual_patterns:
            focus_positions.update(_pattern_positions(pattern))
            focus_lanes.update(key[0] for key in pattern.coverage_keys)

    elif operator_name == "related":
        lane_counts = _coverage_lanes(actual_patterns + virtual_patterns)
        if lane_counts:
            selected_lane, _ = lane_counts.most_common(1)[0]
            focus_lanes.add(selected_lane)
            for pattern in actual_patterns:
                if any(key[0] == selected_lane for key in pattern.coverage_keys):
                    removed_vessels.add(pattern.vessel_code)
                    focus_positions.update(_pattern_positions(pattern))
                    if len(removed_vessels) >= removal_size:
                        break

    elif operator_name == "lane":
        lane_counts = _coverage_lanes(actual_patterns + virtual_patterns)
        if lane_counts:
            lanes = sorted(lane_counts)
            selected_lane = rng.choice(lanes)
            focus_lanes.add(selected_lane)
            for pattern in actual_patterns:
                if any(key[0] == selected_lane for key in pattern.coverage_keys):
                    removed_vessels.add(pattern.vessel_code)
                    focus_positions.update(_pattern_positions(pattern))
            for pattern in virtual_patterns:
                if any(key[0] == selected_lane for key in pattern.coverage_keys):
                    focus_positions.update(_pattern_positions(pattern))

    else:
        raise ValueError(f"unknown removal operator {operator_name!r}")

    return DestroyedState(
        operator_name=operator_name,
        removal_size=removal_size,
        removed_vessels=frozenset(removed_vessels),
        focus_lanes=frozenset(focus_lanes),
        focus_positions=frozenset(focus_positions),
    )


def _candidate_matches_focus(pattern: Pattern, destroyed: DestroyedState) -> bool:
    if not destroyed.focus_lanes and not destroyed.focus_positions and not destroyed.removed_vessels:
        return True
    if pattern.vessel_code in destroyed.removed_vessels:
        return True
    if pattern.target_position_key in destroyed.focus_positions:
        return True
    if pattern.target_position_key is not None and pattern.target_position_key[0] in destroyed.focus_lanes:
        return True
    if any(key[0] in destroyed.focus_lanes for key in pattern.coverage_keys):
        return True
    return False


def _regret_value(pattern: Pattern, group_costs: dict[str, list[float]]) -> float:
    group_key = pattern.source_fragment_id or repr(pattern.target_position_key) or pattern.vessel_code
    costs = group_costs.get(group_key, [pattern.cost])
    if len(costs) < 2:
        return max(0.0, pattern.priority) * 1e-3
    return max(0.0, costs[1] - costs[0])


def _regret_repair_patterns(
    generated_patterns: list[Pattern],
    destroyed: DestroyedState,
) -> list[Pattern]:
    if not generated_patterns:
        return []

    filtered = [pattern for pattern in generated_patterns if _candidate_matches_focus(pattern, destroyed)]
    if not filtered:
        filtered = generated_patterns

    group_costs: dict[str, list[float]] = {}
    for pattern in filtered:
        if pattern.is_virtual and pattern.pattern_id.startswith("virtual-alns-prefix"):
            continue
        group_key = pattern.source_fragment_id or repr(pattern.target_position_key) or pattern.vessel_code
        group_costs.setdefault(group_key, []).append(pattern.cost)
    for costs in group_costs.values():
        costs.sort()

    ranked_children = [
        pattern for pattern in filtered
        if not (pattern.is_virtual and pattern.pattern_id.startswith("virtual-alns-prefix"))
    ]
    ranked_children.sort(
        key=lambda pattern: (
            -_regret_value(pattern, group_costs),
            pattern.cost,
            -pattern.priority,
            pattern.pattern_id,
        )
    )
    selected_children = ranked_children[:MAX_REPAIR_PATTERNS]
    required_parent_ids = {
        parent_id
        for pattern in selected_children
        for parent_id in pattern.requires_pattern_ids
    }
    parent_patterns = [
        pattern for pattern in generated_patterns
        if pattern.pattern_id in required_parent_ids
    ]
    return parent_patterns + selected_children


def _apply_insertion(
    instance_data: InstanceData,
    context: CoverageContext,
    current_solution: CascadingSolution,
    destroyed: DestroyedState,
    round_index: int,
) -> tuple[list[Pattern], Counter[str]]:
    diagnostics: Counter[str] = Counter()
    generated_patterns: list[Pattern] = []

    if destroyed.operator_name in {"random", "lane", "related"}:
        selectable_patterns, selectable_diagnostics = _generate_selectable_switch_patterns(
            instance_data,
            context,
            current_solution,
            round_index,
        )
        generated_patterns.extend(selectable_patterns)
        diagnostics.update(selectable_diagnostics)

    if destroyed.operator_name in {"worst", "related", "random"}:
        virtual_patterns, virtual_diagnostics = _generate_partial_virtual_primary_patterns(
            instance_data,
            context,
            current_solution,
            round_index,
        )
        generated_patterns.extend(virtual_patterns)
        diagnostics.update(virtual_diagnostics)

        suffix_patterns, suffix_diagnostics = _generate_actual_prefix_virtual_suffix_patterns(
            instance_data,
            context,
            current_solution,
            round_index,
        )
        generated_patterns.extend(suffix_patterns)
        diagnostics.update(suffix_diagnostics)

    repaired_patterns = _regret_repair_patterns(generated_patterns, destroyed)
    diagnostics["raw_candidates"] += len(generated_patterns)
    diagnostics["regret_retained"] += len(repaired_patterns)
    return repaired_patterns, diagnostics


def _accept_solution(candidate_cost: float, current_cost: float, temperature: float, rng: random.Random) -> bool:
    if candidate_cost <= current_cost + 1e-6:
        return True
    if temperature <= 1e-9:
        return False
    probability = math.exp(-(candidate_cost - current_cost) / temperature)
    return rng.random() < probability


def _reward(improved_best: bool, improved_current: bool, accepted: bool) -> float:
    if improved_best:
        return 10.0
    if improved_current:
        return 5.0
    if accepted:
        return 1.5
    return 0.2


def _update_operator_weight(weights: dict[str, float], operator_name: str, reward: float) -> None:
    weights[operator_name] = (
        (1.0 - ADAPTIVE_REACTION) * weights[operator_name]
        + ADAPTIVE_REACTION * reward
    )


def _format_weights(weights: dict[str, float]) -> str:
    return ", ".join(f"{key}={value:.2f}" for key, value in sorted(weights.items()))


def algorithm(instance_data: InstanceData, timelimit: int) -> CascadingSolution:
    previous_options = _current_options()
    _apply_solver_options(_lite_base_options())
    try:
        return _algorithm(instance_data, timelimit)
    finally:
        _apply_solver_options(previous_options)


def _algorithm(instance_data: InstanceData, timelimit: int) -> CascadingSolution:
    gp, GRB = _load_gurobi()
    start_time = time.monotonic()

    initial_solution = _make_initial_solution(instance_data, timelimit)
    validate_solution(initial_solution, instance_data)
    initial_cost = _evaluate_total_cost(initial_solution, instance_data)
    context = _build_coverage_context(instance_data, initial_solution)

    raw_patterns = _build_baseline_patterns(instance_data, initial_solution, context)
    patterns, disabled_baseline_counts = _filter_baseline_patterns_for_experiment(raw_patterns)
    patterns, prune_stats = _prune_pattern_pool(patterns, set())
    baseline_ids = {
        pattern.pattern_id
        for pattern in patterns
        if pattern.pattern_id.startswith(("actual:", "virtual:"))
    }
    initial_virtual_portstays = _virtual_portstay_count(initial_solution)

    print(
        "wsgoh/twostage_support/cascade_chain initial solution and pool\n"
        f"- experiment mode: {EXPERIMENT_MODE}\n"
        f"- initial heuristic: only_virtual2\n"
        f"- seed evaluated total cost: {initial_cost:.6f}\n"
        f"- actual vessel schedules: {len(initial_solution.vessel_schedules)}\n"
        f"- virtual vessel schedules: {len(initial_solution.virtual_vessel_schedules)}\n"
        f"- initial virtual PortStay events: {initial_virtual_portstays}\n"
        f"- initial retained patterns: {len(patterns)} "
        f"({_format_counts(Counter(_pattern_family(pattern) for pattern in patterns))})\n"
        f"- disabled baseline patterns: {_format_counts(disabled_baseline_counts)}\n"
        f"- enabled families: actual={ENABLE_BASELINE_ACTUAL}, virtual={ENABLE_BASELINE_VIRTUAL}, "
        f"virtual_fallback={ENABLE_VIRTUAL_FALLBACK}, actual_candidate={ENABLE_ACTUAL_CANDIDATE}, "
        f"actual_primary_virtual={ENABLE_ACTUAL_PRIMARY_VIRTUAL}, cascade_chain={ENABLE_CASCADE_CHAIN}\n"
        f"- initial pruned schedule duplicates: {prune_stats.schedule_duplicate_pruned}\n"
        f"- chain policy: max_rounds={MAX_CHAIN_ROUNDS}, max_depth={MAX_CASCADE_DEPTH}, "
        f"speed_limit={MAX_REPOSITION_SPEED_KNOTS} knots\n"
        f"- solve policy: lexicographic intermediate=NoRel, final=full B&B"
    )

    current_solution = initial_solution
    current_cost = initial_cost
    current_key = _lex_key(current_solution, current_cost)
    best_result: MasterResult | None = None
    best_solution = initial_solution
    best_cost = initial_cost
    best_key = current_key
    warm_start_ids = set(baseline_ids)
    no_improvement = 0

    remaining = _remaining_seconds(start_time, timelimit)
    round0_time = int(max(1.0, min(float(INTERMEDIATE_NOREL_TIME_LIMIT), remaining)))
    print(
        "wsgoh/twostage_support/cascade_chain round 0 baseline lexicographic SP master\n"
        f"- mode: baseline-only\n"
        f"- pool families: {_format_counts(Counter(_pattern_family(pattern) for pattern in patterns))}\n"
        f"- pool patterns: {len(patterns)}\n"
        f"- timelimit: {round0_time}"
    )
    round0_result = _solve_master_lexicographic(
        gp,
        GRB,
        instance_data,
        context,
        patterns,
        round0_time,
        warm_start_ids,
        "round_0_baseline",
        solve_mode="norel",
    )
    validate_solution(round0_result.solution, instance_data)
    round0_cost = _evaluate_total_cost(round0_result.solution, instance_data)
    round0_key = _lex_key(round0_result.solution, round0_cost)
    print(
        "wsgoh/twostage_support/cascade_chain round 0 baseline result\n"
        f"- status: {round0_result.status}\n"
        f"- selected families: {_format_counts(round0_result.selected_family_counts)}\n"
        f"- objective level 1 virtual PortStay: {round0_result.virtual_portstay_objective}\n"
        f"- objective level 2 pattern cost: {round0_result.objective:.6f}\n"
        f"- evaluated total cost: {round0_cost:.6f}\n"
        f"- virtual pattern count: {len(round0_result.solution.virtual_vessel_schedules)}\n"
        f"- virtual PortStay events: {_virtual_portstay_count(round0_result.solution)}"
    )
    current_solution = round0_result.solution
    current_cost = round0_cost
    current_key = round0_key
    best_result = round0_result
    best_solution = round0_result.solution
    best_cost = round0_cost
    best_key = round0_key
    warm_start_ids = set(round0_result.selected_pattern_ids)

    if not ENABLE_CASCADE_CHAIN:
        print("wsgoh/twostage_support/cascade_chain skipping chain rounds because cascade_chain is disabled.")

    for round_index in range(1, MAX_CHAIN_ROUNDS + 1 if ENABLE_CASCADE_CHAIN else 1):
        remaining = _remaining_seconds(start_time, timelimit)
        if timelimit > 0 and remaining <= FINAL_TIME_RESERVE:
            print("wsgoh/twostage_support/cascade_chain stopping chain rounds to reserve final B&B time.")
            break
        if remaining <= 1.0:
            print("wsgoh/twostage_support/cascade_chain stopping because timelimit is nearly exhausted.")
            break
        virtual_before = _virtual_portstay_count(current_solution)
        if virtual_before <= 0:
            print("wsgoh/twostage_support/cascade_chain stopping because there are no virtual holes.")
            break

        candidate_patterns, diagnostics = generate_chain_patterns(
            instance_data,
            context,
            current_solution,
            round_index,
        )
        if not candidate_patterns:
            print(
                f"wsgoh/twostage_support/cascade_chain chain round {round_index}\n"
                f"- virtual PortStay before: {virtual_before}\n"
                f"- diagnostics: {_format_counts(diagnostics)}\n"
                "- no positive chain patterns generated; stopping"
            )
            break

        before_count = len(patterns)
        patterns.extend(candidate_patterns)
        protected_ids = warm_start_ids | (best_result.selected_pattern_ids if best_result is not None else set())
        patterns, prune_stats = _prune_pattern_pool(patterns, protected_ids)

        print(
            f"wsgoh/twostage_support/cascade_chain chain round {round_index}\n"
            f"- virtual PortStay before: {virtual_before}\n"
            f"- chain patterns generated/retained: {diagnostics['chain_patterns_added']}/{len(candidate_patterns)}\n"
            f"- diagnostics: {_format_counts(diagnostics)}\n"
            f"- pool before/after: {before_count}/{len(patterns)}\n"
            f"- pruned: schedule={prune_stats.schedule_duplicate_pruned}, "
            f"coverage={prune_stats.coverage_duplicate_pruned}, vessel_cap={prune_stats.vessel_cap_pruned}, "
            f"total_cap={prune_stats.total_cap_pruned}, orphan={prune_stats.orphan_pruned}"
        )

        mip_time = int(max(1.0, min(float(INTERMEDIATE_NOREL_TIME_LIMIT), remaining)))
        try:
            master_result = _solve_master_lexicographic(
                gp,
                GRB,
                instance_data,
                context,
                patterns,
                mip_time,
                warm_start_ids,
                f"round_{round_index}",
                solve_mode="norel",
            )
            validate_solution(master_result.solution, instance_data)
            evaluated_cost = _evaluate_total_cost(master_result.solution, instance_data)
        except Exception as exc:
            print(f"wsgoh/twostage_support/cascade_chain round {round_index} SP failed: {type(exc).__name__}: {exc}")
            if best_result is None:
                raise
            break

        candidate_key = _lex_key(master_result.solution, evaluated_cost)
        improved_best = candidate_key < best_key
        improved_current = candidate_key < current_key
        selected_cascade_actual = sum(
            count for family, count in master_result.selected_family_counts.items()
            if family == "cascade_chain_actual"
        )
        selected_cascade_virtual = sum(
            count for family, count in master_result.selected_family_counts.items()
            if family.startswith("cascade_chain_virtual")
        )
        selected_actual_candidate = master_result.selected_family_counts.get("actual-candidate", 0)
        selected_actual_primary_virtual = master_result.selected_family_counts.get("actual-primary-virtual", 0)

        print(
            f"wsgoh/twostage_support/cascade_chain round {round_index} lexicographic SP master\n"
            f"- status: {master_result.status}\n"
            f"- pool patterns: {len(patterns)}\n"
            f"- selected families: {_format_counts(master_result.selected_family_counts)}\n"
            f"- MIP virtual PortStay objective: {master_result.virtual_portstay_objective}\n"
            f"- MIP pattern cost objective: {master_result.objective:.6f}\n"
            f"- evaluated total cost: {evaluated_cost:.6f}\n"
            f"- lex key before/after: {current_key} -> {candidate_key}\n"
            f"- selected cascade actual/virtual patterns: {selected_cascade_actual}/{selected_cascade_virtual}\n"
            f"- selected actual-candidate / actual-primary-virtual: "
            f"{selected_actual_candidate}/{selected_actual_primary_virtual}\n"
            f"- virtual vessels: {len(master_result.solution.virtual_vessel_schedules)}\n"
            f"- virtual PortStay events: {_virtual_portstay_count(master_result.solution)}"
        )

        current_solution = master_result.solution
        current_cost = evaluated_cost
        current_key = candidate_key
        warm_start_ids = set(master_result.selected_pattern_ids)

        if improved_best:
            best_result = master_result
            best_solution = master_result.solution
            best_cost = evaluated_cost
            best_key = candidate_key
            no_improvement = 0
        else:
            no_improvement += 1

        if no_improvement >= NO_IMPROVEMENT_LIMIT:
            print(
                "wsgoh/twostage_support/cascade_chain stopping chain rounds after "
                f"{no_improvement} non-improving rounds."
            )
            break

    remaining = _remaining_seconds(start_time, timelimit)
    final_time = int(max(1.0, remaining if timelimit > 0 else 60.0))
    print(
        "wsgoh/twostage_support/cascade_chain final lexicographic SP master\n"
        f"- mode: full\n"
        f"- accumulated pool patterns: {len(patterns)}\n"
        f"- timelimit: {final_time}"
    )
    final_result = _solve_master_lexicographic(
        gp,
        GRB,
        instance_data,
        context,
        patterns,
        final_time,
        best_result.selected_pattern_ids if best_result is not None else warm_start_ids,
        "final_full",
        solve_mode="full",
    )
    validate_solution(final_result.solution, instance_data)
    final_cost = _evaluate_total_cost(final_result.solution, instance_data)
    final_key = _lex_key(final_result.solution, final_cost)

    if best_result is None or final_key <= best_key:
        best_result = final_result
        best_solution = final_result.solution
        best_cost = final_cost
        best_key = final_key

    final_solution = best_solution
    validate_solution(final_solution, instance_data)
    final_cost = _evaluate_total_cost(final_solution, instance_data)
    print(
        "wsgoh/twostage_support/cascade_chain final solution\n"
        f"- seed evaluated total cost: {initial_cost:.6f}\n"
        f"- best evaluated total cost: {final_cost:.6f}\n"
        f"- best lex key: {best_key}\n"
        f"- improvement from seed: {initial_cost - final_cost:.6f}\n"
        f"- virtual vessel schedules: {len(final_solution.virtual_vessel_schedules)} "
        f"(initial {len(initial_solution.virtual_vessel_schedules)})\n"
        f"- virtual PortStay events: {_virtual_portstay_count(final_solution)} "
        f"(initial {_virtual_portstay_count(initial_solution)})"
    )
    return _clone_solution(final_solution)
