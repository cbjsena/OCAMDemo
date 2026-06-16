from __future__ import annotations

from ocam.models import CascadingSolution, InstanceData

from algorithms.wsgoh.heuristic_yongs.solver import generate_variant_results


def run_single_variant(instance_data: InstanceData, timelimit: int, variant_name: str) -> CascadingSolution:
    results = generate_variant_results(
        instance_data,
        timelimit,
        variant_names=(variant_name,),
        fail_fast=True,
    )
    if len(results) != 1:
        raise RuntimeError(
            f"heuristic_yongs/{variant_name}: expected exactly one variant result, got {len(results)}."
        )

    result = results[0]
    print(
        f"heuristic_yongs/{variant_name} selected: "
        f"virtual_portstays={result.metrics.virtual_portstay_count}, "
        f"virtual_vessels={result.metrics.virtual_vessel_count}, "
        f"actual_vessels={result.metrics.actual_vessel_count}, "
        f"declared={len(result.metrics.declared_position_signature)}"
    )
    return result.solution


def variant_description(variant_name: str) -> str:
    return f"Standalone heuristic_yongs variant {variant_name}; imports the shared wrapper and original Yongs solver."
