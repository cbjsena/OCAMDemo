from __future__ import annotations

import time
from collections import Counter
from typing import Iterable

from algorithms.wsgoh.heuristic_yongs.solver import VariantResult, generate_variant_results
from algorithms.wsgoh.postprocess_support import finalize_v10_solution
from algorithms.wsgoh.utils_mip import Pattern, _clone_solution, _evaluate_total_cost, _format_counts
from ocam.models import CascadingSolution, InstanceData
from ocam.validation import validate_solution

from .config import (
    ACTUAL_COST_TIME_FRACTION,
    ACTUAL_FEASIBILITY_TIME_FRACTION,
    FALLBACK_TIME_RESERVE,
    FINAL_SOLVE_MODE,
    MAX_CASCADE_DEPTH,
    MAX_CASCADE_SEED_VIRTUAL_PORTSTAYS,
    MAX_TOTAL_ACTUAL_PATTERNS,
    MAX_TOTAL_FALLBACK_PATTERNS,
    PHASE_I_FEASIBILITY_TOLERANCE,
    clear_pattern_cost_cache,
)
from .master import _solve_actual_master, _solve_fallback_master, _solve_phase_i_actual_master
from .numpy_screening import format_numpy_screening_stats, reset_numpy_screening_stats
from .patterns import (
    _actual_only_patterns,
    _build_empty_actual_patterns,
    _build_seed_patterns,
    _build_virtual_full_fallback_patterns,
    _complete_actual_warm_start,
    _generate_chain_patterns_from_seeds,
    _prune_pattern_pool,
    _seed_actual_pattern_ids,
    _seed_all_pattern_ids,
    format_pattern_timing_stats,
    reset_pattern_timing_stats,
)
from .types import MasterResult
from .utils import (
    _build_coverage_context,
    _coverage_impossibility_summary,
    _family_counts,
    _format_seed_result_lines,
    _format_selected_virtual_coverage,
    _format_slack_entries,
    _load_gurobi,
    _log_prune_stats,
    _remaining_seconds,
)

def _prepare_seed_results(
    instance_data: InstanceData,
    timelimit: int,
) -> tuple[list[VariantResult], dict[str, float], VariantResult]:
    raw_results = generate_variant_results(instance_data, timelimit)
    valid_results: list[VariantResult] = []
    costs: dict[str, float] = {}

    for result in raw_results:
        try:
            validate_solution(result.solution, instance_data)
            costs[result.variant.name] = _evaluate_total_cost(result.solution, instance_data)
        except Exception as exc:
            print(
                "wsgoh/mip_twostage_v10_numpy skipped invalid seed "
                f"{result.variant.name}: {type(exc).__name__}: {exc}",
                flush=True,
            )
            continue
        valid_results.append(result)

    if not valid_results:
        raise RuntimeError("wsgoh/mip_twostage_v10_numpy: no valid heuristic_yongs seed variant was produced.")

    canonical_result = min(
        valid_results,
        key=lambda result: (
            result.metrics.score(),
            costs[result.variant.name],
            result.variant.name,
        ),
    )
    return valid_results, costs, canonical_result

def _zero_virtual_seed_master_result(
    canonical_solution: CascadingSolution,
    actual_patterns: list[Pattern],
    baseline_actual_ids: set[str],
) -> MasterResult:
    selected_patterns = [pattern for pattern in actual_patterns if pattern.pattern_id in baseline_actual_ids]
    return MasterResult(
        solution=_clone_solution(canonical_solution),
        objective=float(sum(pattern.cost for pattern in selected_patterns)),
        virtual_portstay_objective=0,
        status="SEED_ZERO_VIRTUAL",
        policy_status="FEASIBLE_BY_ZERO_VIRTUAL_SEED",
        selected_patterns=selected_patterns,
        selected_pattern_ids=set(baseline_actual_ids),
        selected_family_counts=_family_counts(selected_patterns),
        solve_seconds=0.0,
    )

def _finalize_solution(instance_data: InstanceData, solution: CascadingSolution, label: str) -> CascadingSolution:
    started = time.monotonic()
    processed, stats = finalize_v10_solution(instance_data, solution, label=label)
    postprocess_seconds = time.monotonic() - started
    print(
        "wsgoh/mip_twostage_v10_numpy validation-filtered local search postprocess\n"
        f"- label: {label}\n"
        f"- wall time: {postprocess_seconds:.3f}s\n"
        f"- original evaluated cost: {stats.get('original_cost'):.6f}\n"
        f"- cleanup initial: {stats.get('cleanup_initial_status')}\n"
        f"- ts chains seen: {stats.get('ts_chains_seen')}, candidates tested: {stats.get('ts_candidates_tested')}, "
        f"accepted: {stats.get('ts_accepted')}, rejected validation: {stats.get('ts_rejected_validation')}, "
        f"rejected no improvement: {stats.get('ts_rejected_no_improvement')}, "
        f"best delta: {stats.get('ts_best_delta'):.6f}\n"
        f"- position shift enabled: {stats.get('position_shift_enabled')}, "
        f"skip reason: {stats.get('position_shift_skipped_reason') or '-'}\n"
        f"- position TS target vessels: {stats.get('position_shift_target_vessels')}, "
        f"position targets: {stats.get('position_shift_targets')}, "
        f"candidates tested: {stats.get('position_shift_candidates_tested')}, "
        f"accepted: {stats.get('position_shift_accepted')}, "
        f"rejected validation: {stats.get('position_shift_rejected_validation')}, "
        f"rejected no improvement: {stats.get('position_shift_rejected_no_improvement')}, "
        f"best delta: {stats.get('position_shift_best_delta'):.6f}\n"
        f"- cleanup final: {stats.get('cleanup_final_status')}\n"
        f"- final evaluated cost: {stats.get('final_cost'):.6f}\n"
        f"- total delta: {stats.get('total_delta'):.6f}\n"
        f"- final status: {stats.get('final_status')}",
        flush=True,
    )
    return _clone_solution(processed)

def algorithm(instance_data: InstanceData, timelimit: int) -> CascadingSolution:
    return _algorithm(instance_data, timelimit)

def _algorithm(instance_data: InstanceData, timelimit: int) -> CascadingSolution:
    clear_pattern_cost_cache()
    gp, GRB = _load_gurobi()
    start_time = time.monotonic()
    phase_times: Counter[str] = Counter()

    phase_started = time.monotonic()
    seed_results, seed_costs, canonical_seed = _prepare_seed_results(instance_data, timelimit)
    phase_times["seed_generation_eval_seconds"] += time.monotonic() - phase_started
    canonical_seed_name = canonical_seed.variant.name
    zero_seed_results = [
        result
        for result in seed_results
        if result.metrics.virtual_portstay_count == 0 and result.metrics.virtual_vessel_count == 0
    ]
    best_zero_seed = (
        min(zero_seed_results, key=lambda result: (seed_costs[result.variant.name], result.variant.name))
        if zero_seed_results
        else None
    )
    warm_seed_name = best_zero_seed.variant.name if best_zero_seed is not None else canonical_seed_name

    phase_started = time.monotonic()
    context = _build_coverage_context(instance_data)
    phase_times["coverage_context_seconds"] += time.monotonic() - phase_started
    seed_solutions = {result.variant.name: result.solution for result in seed_results}
    print(
        "wsgoh/mip_twostage_v10_numpy heuristic_yongs seed variants ready\n"
        f"- seed variants: {len(seed_results)}\n"
        f"- canonical warm-start seed: {canonical_seed_name}\n"
        f"- active warm-start seed: {warm_seed_name}\n"
        f"- zero-virtual seeds: {len(zero_seed_results)}\n"
        f"{_format_seed_result_lines(seed_results, seed_costs, canonical_seed_name)}",
        flush=True,
    )

    reset_numpy_screening_stats()
    reset_pattern_timing_stats()
    phase_started = time.monotonic()
    seed_patterns: list[Pattern] = []
    for result in seed_results:
        seed_patterns.extend(
            _build_seed_patterns(
                instance_data,
                result.solution,
                context,
                result.variant.name,
                canonical=result.variant.name == canonical_seed_name,
            )
        )
    empty_actual_patterns = _build_empty_actual_patterns(instance_data, context)
    virtual_full_patterns = _build_virtual_full_fallback_patterns(instance_data, context)
    phase_times["base_pattern_generation_seconds"] += time.monotonic() - phase_started
    print(
        "wsgoh/mip_twostage_v10_numpy seed/base pattern pool ready\n"
        f"- seed patterns: {len(seed_patterns)} ({_format_counts(_family_counts(seed_patterns))})\n"
        f"- empty actual patterns: {len(empty_actual_patterns)}\n"
        f"- virtual fallback patterns: {len(virtual_full_patterns)}\n"
        f"- numpy screening: {format_numpy_screening_stats()}\n"
        f"- pattern timing: {format_pattern_timing_stats()}",
        flush=True,
    )

    phase_started = time.monotonic()
    cascade_seed_solutions = {
        result.variant.name: result.solution
        for result in seed_results
        if result.metrics.virtual_portstay_count <= MAX_CASCADE_SEED_VIRTUAL_PORTSTAYS
    }
    cascade_skipped_seed_names = [
        result.variant.name
        for result in seed_results
        if result.variant.name not in cascade_seed_solutions
    ]
    chain_actual, chain_virtual, chain_diagnostics = _generate_chain_patterns_from_seeds(
        instance_data,
        context,
        cascade_seed_solutions,
    )
    phase_times["cascade_generation_seconds"] += time.monotonic() - phase_started
    print(
        "wsgoh/mip_twostage_v10_numpy cascade-chain candidates ready\n"
        f"- max depth: {MAX_CASCADE_DEPTH}\n"
        f"- seed virtual PortStay threshold: {MAX_CASCADE_SEED_VIRTUAL_PORTSTAYS}\n"
        f"- seeds used: {len(cascade_seed_solutions)}, skipped: {len(cascade_skipped_seed_names)}"
        f" ({', '.join(cascade_skipped_seed_names) if cascade_skipped_seed_names else '-'})\n"
        f"- actual={len(chain_actual)}, virtual_residual={len(chain_virtual)}\n"
        f"- diagnostics: {_format_counts(chain_diagnostics)}",
        flush=True,
    )

    actual_raw_patterns = _actual_only_patterns(seed_patterns + empty_actual_patterns + chain_actual)
    actual_warm_start_raw = _complete_actual_warm_start(
        actual_raw_patterns,
        _seed_actual_pattern_ids(actual_raw_patterns, warm_seed_name),
        instance_data,
    )
    phase_started = time.monotonic()
    actual_patterns = _actual_only_patterns(actual_raw_patterns)
    actual_patterns, actual_prune = _prune_pattern_pool(
        actual_patterns,
        actual_warm_start_raw,
        instance_data=instance_data,
        context=context,
        max_total_patterns=MAX_TOTAL_ACTUAL_PATTERNS,
    )
    actual_patterns = _actual_only_patterns(actual_patterns)
    phase_times["actual_pool_pruning_seconds"] += time.monotonic() - phase_started
    actual_warm_start = _complete_actual_warm_start(
        actual_patterns,
        _seed_actual_pattern_ids(actual_patterns, warm_seed_name),
        instance_data,
    )

    fallback_patterns: list[Pattern] = []
    fallback_warm_start: set[str] = set()
    fallback_prune_text = "fallback prune: skipped until needed"
    if best_zero_seed is None:
        fallback_raw_patterns = seed_patterns + empty_actual_patterns + chain_actual + chain_virtual + virtual_full_patterns
        fallback_warm_start_raw = _complete_actual_warm_start(
            fallback_raw_patterns,
            _seed_all_pattern_ids(fallback_raw_patterns, warm_seed_name),
            instance_data,
        )
        phase_started = time.monotonic()
        fallback_patterns, fallback_prune = _prune_pattern_pool(
            fallback_raw_patterns,
            fallback_warm_start_raw,
            instance_data=instance_data,
            context=context,
            max_total_patterns=MAX_TOTAL_FALLBACK_PATTERNS,
        )
        fallback_warm_start = _complete_actual_warm_start(
            fallback_patterns,
            _seed_all_pattern_ids(fallback_patterns, warm_seed_name),
            instance_data,
        )
        phase_times["fallback_pool_pruning_seconds"] += time.monotonic() - phase_started
        fallback_prune_text = _log_prune_stats("fallback prune", fallback_prune)
    print(
        "wsgoh/mip_twostage_v10_numpy initial pool\n"
        f"- seed variants: {len(seed_results)}\n"
        f"- canonical warm-start seed: {canonical_seed_name}\n"
        f"- active warm-start seed: {warm_seed_name}\n"
        f"- raw actual families: {_format_counts(_family_counts(actual_raw_patterns))}\n"
        f"- fallback pool families: {_format_counts(_family_counts(fallback_patterns)) if fallback_patterns else '(deferred)'}\n"
        f"- actual-only pool families: {_format_counts(_family_counts(actual_patterns))}\n"
        f"- chain diagnostics: {_format_counts(chain_diagnostics)}\n"
        f"- {fallback_prune_text}\n"
        f"- {_log_prune_stats('actual prune', actual_prune)}\n"
        f"- Phase-I coverage precheck: {_coverage_impossibility_summary(context, actual_patterns)}\n"
        f"- phase timing: {_format_counts({key: round(value, 3) for key, value in phase_times.items()})}\n"
        f"- pattern timing: {format_pattern_timing_stats()}"
    )

    if best_zero_seed is not None:
        zero_seed_actual_ids = _complete_actual_warm_start(
            actual_patterns,
            _seed_actual_pattern_ids(actual_patterns, best_zero_seed.variant.name),
            instance_data,
        )
        print(
            "wsgoh/mip_twostage_v10_numpy Phase-I feasibility skipped\n"
            "- reason: a heuristic seed is already zero-virtual\n"
            f"- seed: {best_zero_seed.variant.name}\n"
            f"- warm-start actual patterns: {len(zero_seed_actual_ids)}"
        )
        remaining = _remaining_seconds(start_time, timelimit)
        actual_cost_time = int(max(1.0, min(remaining, max(1.0, timelimit * ACTUAL_COST_TIME_FRACTION))))
        phase_started = time.monotonic()
        actual_cost = _solve_actual_master(
            gp,
            GRB,
            instance_data,
            context,
            actual_patterns,
            actual_cost_time,
            zero_seed_actual_ids,
            "zero_seed_actual_cost",
            solve_mode=FINAL_SOLVE_MODE,
        )
        phase_times["actual_cost_mip_wall_seconds"] += time.monotonic() - phase_started
        if actual_cost.solution is None:
            print(
                "wsgoh/mip_twostage_v10_numpy zero-seed actual-only cost MIP had no incumbent; returning best zero seed",
                flush=True,
            )
            return _finalize_solution(instance_data, best_zero_seed.solution, "zero_seed_heuristic")
        validate_solution(actual_cost.solution, instance_data)
        evaluated_cost = _evaluate_total_cost(actual_cost.solution, instance_data)
        print(
            "wsgoh/mip_twostage_v10_numpy final actual-only result\n"
            f"- status: {actual_cost.policy_status} ({actual_cost.status})\n"
            f"- solve time: {actual_cost.solve_seconds:.2f}s\n"
            f"- selected families: {_format_counts(actual_cost.selected_family_counts)}\n"
            f"- objective pattern cost: {actual_cost.objective:.6f}\n"
            f"- evaluated total cost: {evaluated_cost:.6f}\n"
            "- mixed fallback skipped because zero-virtual seed shortcut succeeded\n"
            f"- phase timing: {_format_counts({key: round(value, 3) for key, value in phase_times.items()})}"
        )
        return _finalize_solution(instance_data, actual_cost.solution, "zero_seed_actual_cost")

    remaining = _remaining_seconds(start_time, timelimit)
    phase_i_time = int(max(1.0, min(remaining, max(1.0, timelimit * ACTUAL_FEASIBILITY_TIME_FRACTION))))
    print(
        "wsgoh/mip_twostage_v10_numpy coverage-relaxed actual-only Phase-I MILP\n"
        f"- patterns: {len(actual_patterns)}\n"
        f"- families: {_format_counts(_family_counts(actual_patterns))}\n"
        f"- virtual families present: {_format_counts(_family_counts(pattern for pattern in actual_patterns if pattern.is_virtual))}\n"
        f"- timelimit: {phase_i_time}"
    )
    phase_started = time.monotonic()
    phase_i = _solve_phase_i_actual_master(
        gp,
        GRB,
        instance_data,
        context,
        actual_patterns,
        phase_i_time,
        actual_warm_start,
    )
    phase_times["phase_i_mip_wall_seconds"] += time.monotonic() - phase_started
    phase_i_objective = phase_i.objective if phase_i.objective is not None else float("inf")
    actual_feasible = phase_i.solution is not None and phase_i_objective <= PHASE_I_FEASIBILITY_TOLERANCE
    print(
        "wsgoh/mip_twostage_v10_numpy coverage-relaxed actual-only Phase-I result\n"
        f"- status: {phase_i.policy_status} ({phase_i.status})\n"
        f"- solve time: {phase_i.solve_seconds:.2f}s\n"
        f"- objective: {phase_i.objective}\n"
        f"- actual feasible: {actual_feasible}\n"
        f"- missing slack total: {phase_i.missing_total:.6g}\n"
        f"- extra slack total: {phase_i.extra_total:.6g}\n"
        f"- top missing: {_format_slack_entries(phase_i.top_missing)}\n"
        f"- top extra: {_format_slack_entries(phase_i.top_extra)}\n"
        f"- selected families: {_format_counts(phase_i.selected_family_counts)}"
    )

    if actual_feasible:
        remaining = _remaining_seconds(start_time, timelimit)
        actual_cost_time = int(max(1.0, min(remaining, max(1.0, timelimit * ACTUAL_COST_TIME_FRACTION))))
        phase_started = time.monotonic()
        actual_cost = _solve_actual_master(
            gp,
            GRB,
            instance_data,
            context,
            actual_patterns,
            actual_cost_time,
            phase_i.selected_pattern_ids,
            "phase_i_actual_cost",
            solve_mode=FINAL_SOLVE_MODE,
        )
        phase_times["actual_cost_mip_wall_seconds"] += time.monotonic() - phase_started
        result = actual_cost if actual_cost.solution is not None else None
        if result is None:
            validate_solution(phase_i.solution, instance_data)
            print(
                "wsgoh/mip_twostage_v10_numpy actual-only cost MIP had no incumbent; returning Phase-I zero-slack incumbent",
                flush=True,
            )
            return _finalize_solution(instance_data, phase_i.solution, "phase_i_zero_slack")
        validate_solution(result.solution, instance_data)
        evaluated_cost = _evaluate_total_cost(result.solution, instance_data)
        print(
            "wsgoh/mip_twostage_v10_numpy final actual-only result\n"
            f"- status: {result.policy_status} ({result.status})\n"
            f"- solve time: {result.solve_seconds:.2f}s\n"
            f"- selected families: {_format_counts(result.selected_family_counts)}\n"
            f"- objective pattern cost: {result.objective:.6f}\n"
            f"- evaluated total cost: {evaluated_cost:.6f}\n"
            "- mixed fallback skipped because Phase-I found zero-slack actual-only feasibility\n"
            f"- phase timing: {_format_counts({key: round(value, 3) for key, value in phase_times.items()})}"
        )
        return _finalize_solution(instance_data, result.solution, "phase_i_actual_cost")

    print(
        "wsgoh/mip_twostage_v10_numpy actual-only Phase-I positive\n"
        "- meaning: zero-virtual actual-only solution was not shown in the current actual pattern pool\n"
        f"- infeasible summary: {_coverage_impossibility_summary(context, actual_patterns)}\n"
        "- running mixed fallback improve with virtual patterns"
    )

    remaining = _remaining_seconds(start_time, timelimit)
    fallback_time = int(max(1.0, remaining - FALLBACK_TIME_RESERVE if timelimit > 0 else 60.0))
    phase_started = time.monotonic()
    fallback_result = _solve_fallback_master(
        gp,
        GRB,
        instance_data,
        context,
        fallback_patterns,
        fallback_time,
        fallback_warm_start,
    )
    phase_times["fallback_mip_wall_seconds"] += time.monotonic() - phase_started
    if fallback_result.solution is None:
        raise RuntimeError(
            "wsgoh/mip_twostage_v10_numpy: mixed fallback improve did not return a feasible solution. "
            f"status={fallback_result.policy_status} ({fallback_result.status})."
        )
    validate_solution(fallback_result.solution, instance_data)
    fallback_cost = _evaluate_total_cost(fallback_result.solution, instance_data)
    virtual_patterns_selected = [pattern for pattern in fallback_result.selected_patterns if pattern.is_virtual]
    virtual_family_counts = _family_counts(virtual_patterns_selected)
    print(
        "wsgoh/mip_twostage_v10_numpy final mixed fallback improve result\n"
        f"- status: {fallback_result.policy_status} ({fallback_result.status})\n"
        f"- solve time: {fallback_result.solve_seconds:.2f}s\n"
        f"- selected virtual vessel count: {len(virtual_patterns_selected)}\n"
        f"- selected virtual PortStay count: {fallback_result.virtual_portstay_objective}\n"
        f"- selected virtual families: {_format_counts(virtual_family_counts)}\n"
        f"- selected actual families: {_format_counts(_family_counts(pattern for pattern in fallback_result.selected_patterns if not pattern.is_virtual))}\n"
        f"- objective pattern cost: {fallback_result.objective:.6f}\n"
        f"- evaluated total cost: {fallback_cost:.6f}\n"
        f"- selected virtual coverage: {_format_selected_virtual_coverage(virtual_patterns_selected)}\n"
        f"- phase timing: {_format_counts({key: round(value, 3) for key, value in phase_times.items()})}"
    )
    return _finalize_solution(instance_data, fallback_result.solution, "mixed_fallback_improve")
