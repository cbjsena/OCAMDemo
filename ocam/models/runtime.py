from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .common import repr_fields
from .solution import CascadingSolution, VesselSchedules


class NoResultError(RuntimeError):
    """Raised when an algorithm intentionally produces no result artifact."""


@dataclass
class RunConfig:
    algorithms: tuple[str, ...]
    instances: tuple[Path, ...]
    outputs_dir: Path
    leaderboard_dir: Path
    timelimit: int = 60

    def __repr__(self) -> str:
        return repr_fields(
            "RunConfig",
            algorithms=self.algorithms,
            instances=self.instances,
            outputs_dir=self.outputs_dir,
            leaderboard_dir=self.leaderboard_dir,
            timelimit=self.timelimit,
        )


@dataclass
class AlgorithmResult:
    """Execution record created by the orchestration layer."""

    algorithm: str
    status: str = "ok"
    objective: dict[str, Any] | None = None
    solution: CascadingSolution = field(
        default_factory=lambda: CascadingSolution(
            declared_positions=[],
            vessel_schedules=VesselSchedules(),
            virtual_vessel_schedules=VesselSchedules(),
        )
    )
    logs: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:
        return repr_fields(
            "AlgorithmResult",
            algorithm=self.algorithm,
            status=self.status,
            objective=self.objective,
            solution=self.solution,
            logs=f"<{len(self.logs)} chars>",
            metadata=self.metadata,
        )


@dataclass
class LowerBoundResult:
    """Execution record for algorithms that produce a bound, not a solution."""

    algorithm: str
    status: str = "ok"
    lower_bound: float | None = None
    objective: dict[str, Any] | None = None
    logs: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:
        return repr_fields(
            "LowerBoundResult",
            algorithm=self.algorithm,
            status=self.status,
            lower_bound=self.lower_bound,
            objective=self.objective,
            logs=f"<{len(self.logs)} chars>",
            metadata=self.metadata,
        )
