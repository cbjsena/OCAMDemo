# simulation/engine.py
"""
시뮬레이션 엔진 (항목 F — Mock 엔진 패턴 포함).

1. 실제 알고리즘이 있으면 algorithm() 함수 호출
2. 없으면 Mock 엔진: 6초 간격 10% 증가, 60초 완료
"""

import logging
import time
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from common.constants import (
    MOCK_ENGINE_STEP_INCREMENT,
    MOCK_ENGINE_STEP_INTERVAL_SEC,
    MOCK_ENGINE_TOTAL_STEPS,
    SIMULATION_STATUS_CANCELED,
)
from ocam.models import AlgorithmResult, LowerBoundResult, RunConfig
from ocam.models.solution import CascadingSolution, VesselSchedules
from ocam.orchestrator import run_single

logger = logging.getLogger(__name__)


class EngineCanceledError(Exception):
    """모니터링에서 중단 요청 시 발생."""


def _ensure_not_canceled(simulation) -> None:
    """DB에서 최신 상태를 확인하여 CANCELED면 예외 발생."""
    from simulation.models import SimulationRun

    current_status = (
        SimulationRun.objects.filter(pk=simulation.pk).values_list("status", flat=True).first()
    )
    if current_status == SIMULATION_STATUS_CANCELED:
        raise EngineCanceledError("Canceled by user")


def _save_result(simulation, result: AlgorithmResult | LowerBoundResult) -> dict:
    """AlgorithmResult 또는 LowerBoundResult에서 simulation result dict 생성 (안전한 필드 접근)"""
    # objective 필드 안전 접근
    objective_value = None
    if result.objective is not None and isinstance(result.objective, dict):
        objective_value = result.objective.get("total_cost")

    # metadata 필드 안전 접근
    execution_time = None
    output_dir = None
    if result.metadata and isinstance(result.metadata, dict):
        # OCAM outputs may store elapsed time under different keys depending on
        # legacy vs current exporters. Support both 'elapsed_time' and
        # 'elapsed_seconds'. Cast numeric-like strings to float when possible.
        if "elapsed_time" in result.metadata:
            execution_time = result.metadata.get("elapsed_time")
        else:
            # legacy key used by some result producers
            execution_time = result.metadata.get("elapsed_seconds")
        output_dir = result.metadata.get("outputs_run_dir") or result.metadata.get("outputs_run_folder")
        # normalize to float if possible
        try:
            if execution_time is not None:
                execution_time = float(execution_time)
        except (TypeError, ValueError):
            execution_time = None

    result_data = {
        "simulation_id": simulation.id,
        "instance_name": simulation.instance_name,
        "algorithm_name": simulation.algorithm_name,
        "objective_value": objective_value,
        "execution_time": execution_time,
        "status": result.status if hasattr(result, "status") else "unknown",
        "output_dir": output_dir,
        "created_at": timezone.now().isoformat(),
    }

    return result_data


def _make_run_output_dir(outputs_dir: Path) -> Path:
    """OCAM 원소스와 동일한 YYMMDD_HHMM 규칙으로 실행 폴더를 생성."""
    prefix = datetime.now().strftime("%y%m%d_%H%M")
    candidate = outputs_dir / prefix
    if not candidate.exists():
        return candidate

    suffix = 2
    while True:
        next_candidate = outputs_dir / f"{prefix} ({suffix})"
        if not next_candidate.exists():
            return next_candidate
        suffix += 1


def run_real_engine(simulation) -> AlgorithmResult | LowerBoundResult:
    """OCAM 오케스트레이션 규격으로 실제 알고리즘을 실행."""
    from instance.services.instance_service import get_instance_by_name

    inst = get_instance_by_name(simulation.instance_name)
    if not inst:
        raise FileNotFoundError(f"Instance '{simulation.instance_name}' not found")

    outputs_dir = Path(settings.OUTPUTS_DIR)
    run_output_dir = _make_run_output_dir(outputs_dir)
    run_output_dir.mkdir(parents=True, exist_ok=True)

    config = RunConfig(
        algorithms=(simulation.algorithm_name,),
        instances=(inst["path"],),
        outputs_dir=run_output_dir,
        leaderboard_dir=outputs_dir / "leaderboard",
        timelimit=60,
    )

    result = run_single(
        config=config,
        algorithm_name=simulation.algorithm_name,
        instance_dir=inst["path"],
        run_output_dir=run_output_dir,
        output_dir=run_output_dir,
    )
    if result is None:
        raise RuntimeError(f"Algorithm '{simulation.algorithm_name}' produced no result")
    return result


def run_mock_engine(simulation) -> AlgorithmResult:
    """
    가짜 엔진 (항목 F): 6초×10단계 = 60초 완료.
    AlgorithmResult 객체를 반환하여 run_real_engine과 형식 일치.
    """
    start = time.time()
    _ensure_not_canceled(simulation)

    # CSV 파일 수량 집계
    from instance.services.instance_service import get_instance_by_name

    inst = get_instance_by_name(simulation.instance_name)
    file_count = len(inst["files"]) if inst else 0

    simulation.model_status = f"데이터 로드 완료 ({file_count} files)"
    simulation.save(update_fields=["model_status", "updated_at"])

    logger.info("[MockEngine] Sim#%d – instance '%s' (%d files)",
                simulation.id, simulation.instance_name, file_count)

    # 6초 간격으로 10% 증가
    for step in range(1, MOCK_ENGINE_TOTAL_STEPS + 1):
        _ensure_not_canceled(simulation)
        time.sleep(MOCK_ENGINE_STEP_INTERVAL_SEC)
        _ensure_not_canceled(simulation)

        progress = step * MOCK_ENGINE_STEP_INCREMENT
        simulation.progress = progress
        simulation.model_status = (
            "최적화 완료" if step == MOCK_ENGINE_TOTAL_STEPS
            else f"최적화 진행 중… ({progress}%)"
        )
        simulation.save(update_fields=["progress", "model_status", "updated_at"])

        logger.info("[MockEngine] Sim#%d – progress %d%% (step %d/%d)",
                    simulation.id, progress, step, MOCK_ENGINE_TOTAL_STEPS)

    elapsed = time.time() - start
    # AlgorithmResult 객체 반환 (run_real_engine과 동일한 형식)
    return AlgorithmResult(
        algorithm=simulation.algorithm_name,
        status="ok",
        objective={"total_cost": 12345.67},
        solution=CascadingSolution(
            declared_positions=[],
            vessel_schedules=VesselSchedules(),
            virtual_vessel_schedules=VesselSchedules(),
        ),
        logs="",
        metadata={
            "elapsed_time": round(elapsed, 2),
            "outputs_run_dir": str(settings.OUTPUTS_DIR),
            "mock_engine": True,
            "file_count": file_count,
            "instance": simulation.instance_name,
        },
    )

