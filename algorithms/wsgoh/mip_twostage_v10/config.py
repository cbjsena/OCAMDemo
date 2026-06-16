from __future__ import annotations

from pathlib import Path
from typing import Any

DESCRIPTION = (
    "mip_twostage_v10: D-first full-vessel pattern MIP seeded by all "
    "wsgoh/heuristic_yongs variants, with actual-candidate, actual-primary-virtual, "
    "depth-1 cascade candidates, empty actual patterns, coverage-relaxed "
    "actual-only Phase-I MILP, and lexicographic virtual-PortStay fallback."
)

DEFAULT_INPUT_DIR = Path("instances/toy_v1")
DEFAULT_OUTPUT_DIR = Path("output_dir")
DEFAULT_TIMELIMIT = 300

# 한 실제 선박당 남길 수 있는 최대 actual pattern 수입니다. 너무 많은 후보가 한 선박에 몰리는 것을 막습니다.
MAX_PATTERNS_PER_VESSEL = 160
# Phase-I / actual-only cost MIP에 들어가는 actual pattern 전체 상한입니다.
MAX_TOTAL_ACTUAL_PATTERNS = 10000
# mixed fallback MIP에 들어가는 actual + virtual pattern 전체 상한입니다.
MAX_TOTAL_FALLBACK_PATTERNS = 12000
# virtual hole 하나를 actual 선박이 takeover하는 actual-primary-virtual 후보의 target별 채택 상한입니다.
MAX_BASE_ACTUAL_PRIMARY_PER_TARGET = 6
# selectable position 하나를 actual 선박 schedule에 삽입하는 actual-candidate 후보의 position별 채택 상한입니다.
MAX_BASE_ACTUAL_CANDIDATE_PER_POSITION = 6
# target 하나에 대해 feasibility를 검사해볼 source actual vessel schedule 수의 상한입니다.
MAX_BASE_SCREENED_PER_TARGET = 16
# heuristic seed 하나에서 만들 actual-primary-virtual pattern의 총 상한입니다.
MAX_BASE_ACTUAL_PRIMARY_TOTAL_PER_SEED = 100
# heuristic seed 하나에서 만들 actual-candidate pattern의 총 상한입니다.
MAX_BASE_ACTUAL_CANDIDATE_TOTAL_PER_SEED = 100
# cascade-chain actual repair pattern의 총 생성 상한입니다.
MAX_CHAIN_PATTERNS = 200
# cascade-chain displacement 깊이입니다. v10은 lean하게 depth 1만 사용합니다.
MAX_CASCADE_DEPTH = 1
# cascade-chain 생성에서 seed별로 repair target으로 삼을 virtual hole 수의 상한입니다.
MAX_TARGET_HOLES_PER_ROUND = 10
# virtual hole 하나를 메우려고 검사할 candidate actual vessel 수의 상한입니다.
MAX_CANDIDATE_VESSELS_PER_HOLE = 10
# vessel-hole pair 하나에서 시도할 handover split / 연결 variant 수의 상한입니다.
MAX_HANDOVER_VARIANTS_PER_PAIR = 5
# 전체 timelimit 중 actual-only Phase-I feasibility MILP에 배정할 최대 비율입니다.
ACTUAL_FEASIBILITY_TIME_FRACTION = 0.25
# 전체 timelimit 중 final actual-only cost MIP에 배정할 최대 비율입니다.
ACTUAL_COST_TIME_FRACTION = 0.35
# fallback solve를 위해 남겨두는 최소 시간 여유입니다. 남은 시간이 이보다 작으면 fallback 시간을 보수적으로 잡습니다.
FALLBACK_TIME_RESERVE = 1.0
# 최종 MIP solve 방식입니다. "full"은 Gurobi 정규 branch-and-bound를 사용한다는 뜻입니다.
FINAL_SOLVE_MODE = "full"
# Phase-I slack objective가 이 값 이하이면 coverage relaxed objective를 0으로 보고 actual-only feasible로 판정합니다.
PHASE_I_FEASIBILITY_TOLERANCE = 1e-6

_PATTERN_COST_CACHE: dict[tuple[Any, ...], float] = {}

def clear_pattern_cost_cache() -> None:
    _PATTERN_COST_CACHE.clear()
