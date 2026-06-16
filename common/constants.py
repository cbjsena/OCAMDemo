# common/constants.py

# ==========================================
# Simulation Status
# ==========================================
SIMULATION_STATUS_PENDING = "PENDING"
SIMULATION_STATUS_RUNNING = "RUNNING"
SIMULATION_STATUS_SUCCESS = "SUCCESS"
SIMULATION_STATUS_FAILED = "FAILED"
SIMULATION_STATUS_CANCELED = "CANCELED"

SIMULATION_STATUS_CHOICES = [
    (SIMULATION_STATUS_PENDING, "Pending"),
    (SIMULATION_STATUS_RUNNING, "Running"),
    (SIMULATION_STATUS_SUCCESS, "Success"),
    (SIMULATION_STATUS_FAILED, "Failed"),
    (SIMULATION_STATUS_CANCELED, "Canceled"),
]

# ==========================================
# Mock Engine Configuration
# ==========================================
MOCK_ENGINE_STEP_INTERVAL_SEC = 6  # 각 단계 간격 (초)
MOCK_ENGINE_STEP_INCREMENT = 10  # 단계당 진행률 증가분 (%)
MOCK_ENGINE_TOTAL_STEPS = 10  # 총 단계 수 (10 × 10% = 100%)

# ==========================================
# Instance / Algorithm / Output Paths
# ==========================================
METADATA_FILENAME = "metadata.csv"
SOLVER_FILENAME = "solver.py"
SOLVER_FUNCTION_NAME = "algorithm"
INSTANCE_SEPARATOR = "~"  # 하위 폴더 구분자 (toy_v2~000_base)
