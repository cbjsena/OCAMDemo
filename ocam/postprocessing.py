from __future__ import annotations

from ocam.models.schedule_events import InLaneSail, OutLaneSail
from ocam.models import AlgorithmResult, InstanceData
from ocam.validation import validate_solution, evaluate_solution


_EVALUATION_DETAIL_KEYS = {
    "bunker_cost_by_inlane_sail",
    "bunker_cost_by_port_stay",
    "bunker_cost_by_outlane_sail",
    "transshipment_count",
}


def _annotate_sail_metrics(result: AlgorithmResult, instance_data: InstanceData) -> None:
    distance_by_leg = {
        (row["from_port_code"], row["to_port_code"]): float(row["distance"]) for row in instance_data.distances
    }

    for schedules in (result.solution.vessel_schedules.values(), result.solution.virtual_vessel_schedules.values()):
        for vessel_schedule in schedules:
            for event in vessel_schedule:
                if not isinstance(event, (InLaneSail, OutLaneSail)):
                    continue
                distance = distance_by_leg.get((event.from_port_code, event.to_port_code))
                if distance is None:
                    continue
                duration_hours = (event.sea_sail_end - event.sea_sail_start).total_seconds() / 3600.0
                if duration_hours <= 0:
                    continue
                event.distance = distance
                event.avg_speed = distance / duration_hours


def postprocess(result: AlgorithmResult, instance_data: InstanceData) -> AlgorithmResult:
    """
    TODO: Post-process the algorithm result.
    """

    result.metadata.setdefault("scenario_name", instance_data.scenario_name)
    result.metadata.setdefault("postprocessed", True)
    _annotate_sail_metrics(result, instance_data)
    objective_missing_details = not isinstance(result.objective, dict) or any(
        key not in result.objective for key in _EVALUATION_DETAIL_KEYS
    )
    if result.objective is None or objective_missing_details:
        validate_solution(result.solution, instance_data)
        evaluated_objective = evaluate_solution(result.solution, instance_data)
        if result.objective is None:
            result.objective = evaluated_objective
        elif isinstance(result.objective, dict) and isinstance(evaluated_objective, dict):
            for key in _EVALUATION_DETAIL_KEYS:
                if key in evaluated_objective:
                    result.objective.setdefault(key, evaluated_objective[key])
    return result
