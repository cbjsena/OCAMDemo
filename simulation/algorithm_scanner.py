# simulation/algorithm_scanner.py
"""
algorithms 폴더를 스캔하여 사용 가능한 알고리즘 목록을 반환.

구조: algorithms/<researcher_name>/<algorithm_name>/solver.py
  - solver.py 안에 callable한 algorithm() 함수가 있어야 함
"""

import importlib.util
import inspect
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

from django.conf import settings

from common.constants import SOLVER_FILENAME, SOLVER_FUNCTION_NAME


def get_algorithms_dir() -> Path:
    return Path(settings.ALGORITHMS_DIR)


def discover_algorithms() -> list[dict]:
    """
    사용 가능한 알고리즘 목록을 반환.

    Returns:
        [{"researcher": "kim", "name": "greedy", "full_name": "kim/greedy",
          "path": Path(...), "valid": True}, ...]
    """
    root = get_algorithms_dir()
    if not root.exists():
        return []

    algorithms = []
    for researcher_dir in sorted(root.iterdir()):
        if not researcher_dir.is_dir() or researcher_dir.name.startswith("_"):
            continue

        for algo_dir in sorted(researcher_dir.iterdir()):
            if not algo_dir.is_dir() or algo_dir.name.startswith("_"):
                continue

            solver_path = algo_dir / SOLVER_FILENAME
            valid = _validate_solver(solver_path)
            algorithms.append(
                {
                    "researcher": researcher_dir.name,
                    "name": algo_dir.name,
                    "full_name": f"{researcher_dir.name}/{algo_dir.name}",
                    "path": solver_path,
                    "valid": valid,
                }
            )

    return algorithms


def _validate_solver(solver_path: Path) -> bool:
    """solver.py에 callable algorithm() 함수가 있는지 검증."""
    if not solver_path.exists():
        return False

    try:
        spec = importlib.util.spec_from_file_location("solver_check", solver_path)
        if spec is None or spec.loader is None:
            return False

        module = importlib.util.module_from_spec(spec)

        # sys.modules에 등록하여 dataclass decorator가 __module__을 찾을 수 있도록 함
        import sys
        sys.modules[spec.name] = module

        try:
            spec.loader.exec_module(module)
            func = getattr(module, SOLVER_FUNCTION_NAME, None)
            return callable(func)
        finally:
            # 임시 모듈 제거
            sys.modules.pop(spec.name, None)
    except Exception:
        return False


def validate_solver_with_reason(solver_path: Path) -> tuple[bool, str]:
    """solver.py 검증 결과와 사유를 함께 반환."""
    if not solver_path.exists():
        return False, f"{SOLVER_FILENAME} not found"

    try:
        import sys
        spec = importlib.util.spec_from_file_location("solver_check", solver_path)
        if spec is None or spec.loader is None:
            return False, "failed to load module spec"

        module = importlib.util.module_from_spec(spec)

        # sys.modules에 등록하여 dataclass decorator가 __module__을 찾을 수 있도록 함
        sys.modules[spec.name] = module
        try:
            spec.loader.exec_module(module)
            func = getattr(module, SOLVER_FUNCTION_NAME, None)
            if not callable(func):
                return False, f"No callable '{SOLVER_FUNCTION_NAME}()'"

            sig = inspect.signature(func)
            if len(sig.parameters) != 2:
                return False, "algorithm() must accept exactly 2 parameters"
            return True, "ok"
        finally:
            # 임시 모듈 제거
            sys.modules.pop(spec.name, None)
    except Exception as e:
        return False, str(e)


def install_algorithm_zip(uploaded_file) -> dict:
    """
    ZIP 업로드 파일에서 알고리즘 1개를 설치한다.

    허용 구조:
      - <researcher>/<algorithm>/__init__.py
      - <researcher>/<algorithm>/solver.py
      - algorithms/<researcher>/<algorithm>/... (prefix 포함도 허용)
    """
    root = get_algorithms_dir()
    root.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(uploaded_file) as zf:
        file_names = [n for n in zf.namelist() if not n.endswith("/")]
        if not file_names:
            raise ValueError("ZIP is empty")

        normalized_entries = []
        for name in file_names:
            safe_name = name.replace("\\", "/").lstrip("/")
            parts = [p for p in safe_name.split("/") if p and p != "."]
            if not parts or any(p == ".." for p in parts):
                raise ValueError("Invalid ZIP path")
            if parts[0] == "algorithms":
                parts = parts[1:]
            if len(parts) < 3:
                continue
            normalized_entries.append((parts, safe_name))

        candidates = {}
        for parts, _safe_name in normalized_entries:
            researcher, algorithm = parts[0], parts[1]
            filename = "/".join(parts[2:])
            key = (researcher, algorithm)
            candidates.setdefault(key, set()).add(filename)

        valid_candidates = [
            key
            for key, files in candidates.items()
            if "__init__.py" in files and SOLVER_FILENAME in files
        ]
        if len(valid_candidates) != 1:
            raise ValueError("ZIP must contain exactly one algorithm package")

        researcher, algorithm = valid_candidates[0]
        name_pattern = re.compile(r"^[A-Za-z0-9_]+$")
        if not name_pattern.match(researcher) or not name_pattern.match(algorithm):
            raise ValueError("researcher/algorithm name allows only letters, digits, underscore")

        target_dir = root / researcher / algorithm
        if target_dir.exists():
            raise FileExistsError(f"Algorithm '{researcher}/{algorithm}' already exists")

        tmp_root = Path(tempfile.mkdtemp(prefix="algo_upload_"))
        try:
            tmp_algo_dir = tmp_root / researcher / algorithm
            tmp_algo_dir.mkdir(parents=True, exist_ok=True)

            for parts, safe_name in normalized_entries:
                if parts[0] != researcher or parts[1] != algorithm:
                    continue
                rel_path = Path(*parts[2:])
                dst = tmp_algo_dir / rel_path
                dst.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(safe_name) as src:
                    dst.write_bytes(src.read())

            solver_path = tmp_algo_dir / SOLVER_FILENAME
            is_valid, reason = validate_solver_with_reason(solver_path)
            if not is_valid:
                raise ValueError(f"Invalid solver.py: {reason}")

            target_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(tmp_algo_dir), str(target_dir))
        finally:
            shutil.rmtree(tmp_root, ignore_errors=True)

    return {
        "researcher": researcher,
        "name": algorithm,
        "full_name": f"{researcher}/{algorithm}",
        "path": target_dir,
    }


def load_algorithm_function(full_name: str):
    """
    알고리즘 함수를 동적으로 로드하여 반환.

    Args:
        full_name: "researcher_name/algorithm_name"

    Returns:
        callable algorithm() 함수
    """
    import sys
    root = get_algorithms_dir()
    parts = full_name.split("/")
    if len(parts) != 2:
        raise ValueError(f"Invalid algorithm name: {full_name}")

    solver_path = root / parts[0] / parts[1] / SOLVER_FILENAME
    if not solver_path.exists():
        raise FileNotFoundError(f"solver.py not found: {solver_path}")

    module_name = f"algo_{full_name.replace('/', '_')}"
    spec = importlib.util.spec_from_file_location(module_name, solver_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load spec for {solver_path}")

    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)

    # sys.modules에 등록하여 dataclass decorator가 __module__을 찾을 수 있도록 함
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
        func = getattr(module, SOLVER_FUNCTION_NAME, None)
        if not callable(func):
            raise AttributeError(f"No callable '{SOLVER_FUNCTION_NAME}()' in {solver_path}")
        return func
    finally:
        # 사용 후 제거
        sys.modules.pop(module_name, None)
