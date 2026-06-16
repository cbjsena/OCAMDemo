# simulation/models.py
from django.conf import settings
from django.db import models

from common.constants import SIMULATION_STATUS_CHOICES, SIMULATION_STATUS_PENDING


class SimulationRun(models.Model):
    """시뮬레이션 실행 기록 (DB 관리)."""

    id = models.AutoField(primary_key=True, verbose_name="Simulation ID")

    # 입력 정보
    instance_name = models.CharField(max_length=200, verbose_name="Instance Name")
    algorithm_name = models.CharField(
        max_length=200,
        verbose_name="Algorithm",
        help_text="researcher_name/algorithm_name",
    )

    # 상태 & 진행률
    status = models.CharField(
        max_length=20,
        choices=SIMULATION_STATUS_CHOICES,
        default=SIMULATION_STATUS_PENDING,
    )
    progress = models.IntegerField(default=0, help_text="진행률 (%)")
    task_id = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="Celery Task ID",
    )

    # 결과
    output_folder = models.CharField(
        max_length=500,
        blank=True,
        null=True,
        help_text="outputs 폴더 내 결과 경로",
    )
    objective_value = models.FloatField(blank=True, null=True)
    execution_time = models.FloatField(blank=True, null=True, help_text="실행 시간 (초)")
    model_status = models.CharField(max_length=200, blank=True, null=True)
    description = models.TextField(blank=True, null=True)

    # Audit
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="simulations_created",
    )
    updated_at = models.DateTimeField(auto_now=True)
    started_at = models.DateTimeField(blank=True, null=True)
    finished_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = "simulation_run"
        ordering = ["-created_at"]
        verbose_name = "Simulation Run"

    def __str__(self):
        return f"Sim#{self.id} [{self.instance_name}] {self.algorithm_name} - {self.status}"

    @property
    def is_running(self):
        return self.status == "RUNNING"

    @property
    def is_pending(self):
        return self.status == "PENDING"

    @property
    def is_processing(self):
        return self.status in ("PENDING", "RUNNING")

    @property
    def is_success(self):
        return self.status == "SUCCESS"

    @property
    def can_cancel(self):
        return self.status in ("PENDING", "RUNNING")
