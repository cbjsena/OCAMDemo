# common/messages.py
"""
애플리케이션 전역 메시지 상수 관리 모듈 (항목 A)

사용 예시:
    from common import messages as msg
    messages.success(request, msg.UPLOAD_SUCCESS)
    messages.error(request, msg.LOAD_ERROR.format(target="instance", error=str(e)))
"""

# ==========================================
# 1. 공통 및 인증 (General & Auth)
# ==========================================
PERMISSION_DENIED = "You do not have permission to perform this action."
LOGIN_REQUIRED = "Please login to access this page."
FUNC_NOT_IMPLEMENTED = "{func_name} function not implemented yet."
INVALID_PARAMETERS = "Invalid parameters."

# 통합된 공통 에러 메시지
SAVE_ERROR = "Failed to save {target}: {error}"
LOAD_ERROR = "Failed to load {target}: {error}"
PROCESS_ERROR = "Failed to process {target}: {error}"

# Not Found
DATA_NOT_FOUND = "Data not found."
ITEM_NOT_FOUND = "{item} not found."

# ==========================================
# 2. Instance 관리
# ==========================================
INSTANCE_NOT_FOUND = "Instance '{name}' not found."
INSTANCE_NO_METADATA = "No metadata.csv found in instance folder."
INSTANCE_LOAD_SUCCESS = "Instance data loaded successfully."

# ==========================================
# 3. CSV 업로드/다운로드
# ==========================================
UPLOAD_SUCCESS = "File uploaded successfully."
FILE_NOT_SELECTED = "Please select a file to upload."
INVALID_FILE_EXT = "Please upload a valid .{ext} file."
CSV_FILE_EMPTY = "The uploaded CSV file is empty."
CSV_UPLOAD_SUCCESS = "CSV file '{filename}' uploaded and saved successfully."
CSV_DOWNLOAD_ERROR = "Failed to download CSV: {error}"

# ==========================================
# 4. Algorithm
# ==========================================
ALGORITHM_NOT_FOUND = "Algorithm '{name}' not found."
ALGORITHM_NO_FUNCTION = "No callable 'algorithm()' function found in solver.py."
ALGORITHM_LOAD_ERROR = "Failed to load algorithm: {error}"
ALGORITHM_UPLOAD_SUCCESS = "Algorithm '{name}' uploaded successfully."
ALGORITHM_UPLOAD_EXISTS = "Algorithm '{name}' already exists."
ALGORITHM_UPLOAD_INVALID = "Invalid algorithm package: {reason}"

# ==========================================
# 5. Simulation
# ==========================================
SIMULATION_STARTED = "Simulation started (ID: {sim_id})."
SIMULATION_CANCELED = "Simulation '{sim_id}' has been canceled."
SIMULATION_CANCEL_FAILED = "Cannot cancel simulation '{sim_id}': not in running state."
SIMULATION_NOT_FOUND = "Simulation not found."
SIMULATION_FAILED = "Simulation failed: {error}"
SIMULATION_DELETED = "Simulation '{sim_id}' has been deleted."
SIMULATION_DELETE_FAILED = "Cannot delete simulation '{sim_id}': it may be running or you lack permissions."

# ==========================================
# 6. Result
# ==========================================
RESULT_NOT_FOUND = "Result file not found."
RESULT_INVALID_JSON = "Invalid result JSON: {error}"
RESULT_LOAD_ERROR = "Failed to load result: {error}"

# ==========================================
# 7. CLI / Log
# ==========================================
DIR_NOT_FOUND = "Directory not found: {path}"
FILE_NOT_FOUND = "[SKIP] File not found: {file}"
