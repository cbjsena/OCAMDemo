# simulation/tasks.py
"""
Celery Task: 시뮬레이션 비동기 실행 (항목 D).
로컬에서는 EAGER 모드로 동기 실행.
"""

import logging

from celery import shared_task
from django.utils import timezone

from common.constants import (
    SIMULATION_STATUS_CANCELED,
    SIMULATION_STATUS_FAILED,
    SIMULATION_STATUS_RUNNING,
    SIMULATION_STATUS_SUCCESS,
)
from simulation.engine import (
    EngineCanceledError,
    _save_result,
    run_mock_engine,
    run_real_engine,
)
from simulation.models import SimulationRun

logger = logging.getLogger(__name__)


def _use_real_engine(simulation: SimulationRun) -> bool:
    """실제 알고리즘 solver.py가 유효하면 True."""
    from simulation.algorithm_scanner import discover_algorithms

    algorithms = discover_algorithms()
    for algo in algorithms:
        if algo["full_name"] == simulation.algorithm_name and algo["valid"]:
            return True
    return False


@shared_task(bind=True)
def run_simulation_task(self, simulation_id: int) -> None:
    try:
        simulation = SimulationRun.objects.get(pk=simulation_id)
    except SimulationRun.DoesNotExist:
        logger.warning("SimulationRun %s not found", simulation_id)
        return

    if simulation.status == SIMULATION_STATUS_CANCELED:
        logger.info("Simulation %s already canceled", simulation_id)
        return

    # RUNNING 전환
    simulation.status = SIMULATION_STATUS_RUNNING
    simulation.progress = 0
    simulation.started_at = timezone.now()
    simulation.model_status = "RUNNING"
    simulation.save(update_fields=[
        "status", "progress", "started_at", "model_status", "updated_at",
    ])

    try:
        if _use_real_engine(simulation):
            logger.info("Using REAL engine for Sim#%d", simulation_id)
            model_result = run_real_engine(simulation)
        else:
            logger.info("Using MOCK engine for Sim#%d", simulation_id)
            model_result = run_mock_engine(simulation)

        # 완료 전 취소 확인
        simulation.refresh_from_db(fields=["status"])
        if simulation.status == SIMULATION_STATUS_CANCELED:
            return

        # 결과 저장
        result = _save_result(simulation, model_result)

        simulation.status = SIMULATION_STATUS_SUCCESS
        simulation.progress = 100
        simulation.finished_at = timezone.now()
        # result는 dict 형식 (_save_result에서 반환)
        simulation.objective_value = result.get("objective_value")
        simulation.execution_time = result.get("execution_time")
        simulation.model_status = result.get("status")  # 항상 존재 (기본값: "ok")
        simulation.output_folder = result.get("output_dir")
        simulation.save(update_fields=[
            "status", "progress", "finished_at", "objective_value",
            "execution_time", "model_status", "output_folder", "updated_at",
        ])

    except EngineCanceledError:
        logger.info("Simulation %s canceled during engine run", simulation_id)
        simulation.refresh_from_db()
        simulation.status = SIMULATION_STATUS_CANCELED
        simulation.finished_at = timezone.now()
        simulation.model_status = "Canceled by user"
        simulation.save(update_fields=["status", "finished_at", "model_status", "updated_at"])

    except Exception as exc:
        logger.exception("Simulation %s failed", simulation_id)
        simulation.refresh_from_db(fields=["status"])
        if simulation.status == SIMULATION_STATUS_CANCELED:
            return

        simulation.status = SIMULATION_STATUS_FAILED
        simulation.finished_at = timezone.now()
        simulation.model_status = str(exc)[:200]
        simulation.progress = 0
        simulation.save(update_fields=[
            "status", "finished_at", "model_status", "progress", "updated_at",
        ])
        raise

