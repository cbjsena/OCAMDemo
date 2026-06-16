from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from algorithms.wsgoh.utils_mip import CoverageKey, Pattern, PositionKey
from ocam.models import CascadingSolution

@dataclass(frozen=True)
class DirectRunArguments:
    input_dir: Path
    timelimit: int
    output_solution: Path | None

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
class PoolPruneStats:
    input_count: int
    retained_count: int
    schedule_duplicate_pruned: int = 0
    coverage_duplicate_pruned: int = 0
    vessel_cap_pruned: int = 0
    total_cap_pruned: int = 0
    orphan_pruned: int = 0

@dataclass
class MasterResult:
    solution: CascadingSolution | None
    objective: float | None
    virtual_portstay_objective: int | None
    status: str
    policy_status: str
    selected_patterns: list[Pattern]
    selected_pattern_ids: set[str]
    selected_family_counts: Counter[str]
    solve_seconds: float

@dataclass
class PhaseIResult:
    solution: CascadingSolution | None
    objective: float | None
    status: str
    policy_status: str
    selected_patterns: list[Pattern]
    selected_pattern_ids: set[str]
    selected_family_counts: Counter[str]
    solve_seconds: float
    missing_total: float
    extra_total: float
    top_missing: list[tuple[CoverageKey, float]]
    top_extra: list[tuple[CoverageKey, float]]
