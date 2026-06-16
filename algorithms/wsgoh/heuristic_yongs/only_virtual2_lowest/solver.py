from __future__ import annotations

from ocam.models import CascadingSolution, InstanceData

from algorithms.wsgoh.heuristic_yongs.variant_algorithm import run_single_variant, variant_description

VARIANT_NAME = "only_virtual2_lowest"
DESCRIPTION = variant_description(VARIANT_NAME)


def algorithm(instance_data: InstanceData, timelimit: int) -> CascadingSolution:
    return run_single_variant(instance_data, timelimit, VARIANT_NAME)
