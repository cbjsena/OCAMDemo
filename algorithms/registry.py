from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from types import ModuleType
from typing import Callable

PACKAGE_ROOT = Path(__file__).resolve().parent
NESTED_ALGORITHM_ROOTS = {
    ("wsgoh", "heuristic_yongs"),
}


class AlgorithmLookupError(LookupError):
    """Raised when an algorithm cannot be resolved."""


@dataclass(frozen=True)
class AlgorithmSpec:
    name: str
    module: ModuleType
    entrypoint: Callable
    description: str


@dataclass(frozen=True)
class AlgorithmImportFailure:
    name: str
    error_message: str


def _iter_algorithm_packages() -> list[tuple[str, str]]:
    names: list[tuple[str, str]] = []
    # 저장소 규칙:
    # algorithms/<researcher_name>/<algorithm_name>/solver.py
    # algorithms/<researcher_name>/<algorithm_group>/<algorithm_name>/solver.py
    #   - 현재는 명시적으로 허용한 nested root만 공개 알고리즘으로 노출한다.
    #
    # researcher 폴더 이름도 공개 알고리즘 이름의 일부로 사용하므로,
    # 여러 사람이 같은 저장소에 알고리즘을 추가해도 이름 충돌을 줄일 수 있다.
    for researcher_path in sorted(PACKAGE_ROOT.iterdir()):
        if not researcher_path.is_dir():
            continue
        if researcher_path.name.startswith("_") or researcher_path.name == "__pycache__":
            continue
        for algorithm_path in sorted(researcher_path.iterdir()):
            if not algorithm_path.is_dir():
                continue
            if algorithm_path.name.startswith("_") or algorithm_path.name == "__pycache__":
                continue
            if not (algorithm_path / "solver.py").exists():
                continue
            names.append((researcher_path.name, algorithm_path.name))

        for nested_researcher_name, group_name in sorted(NESTED_ALGORITHM_ROOTS):
            if researcher_path.name != nested_researcher_name:
                continue
            group_path = researcher_path / group_name
            if not group_path.is_dir():
                continue
            for algorithm_path in sorted(group_path.iterdir()):
                if not algorithm_path.is_dir():
                    continue
                if algorithm_path.name.startswith("_") or algorithm_path.name == "__pycache__":
                    continue
                if not (algorithm_path / "solver.py").exists():
                    continue
                names.append((researcher_path.name, f"{group_name}/{algorithm_path.name}"))
    return names


def _exported_name(researcher_name: str, algorithm_name: str) -> str:
    return f"{researcher_name}/{algorithm_name}"


def _build_spec(researcher_name: str, algorithm_name: str) -> AlgorithmSpec:
    # 현재 레지스트리는 __init__.py가 아니라 solver.py를 직접 import한다.
    module_name = f"algorithms.{researcher_name}.{algorithm_name.replace('/', '.')}.solver"
    try:
        module = import_module(module_name)
    except Exception as exc:
        raise AlgorithmLookupError(
            "algorithms.registry._build_spec: failed to import " f"'{module_name}': {type(exc).__name__}: {exc}"
        ) from exc

    entrypoint = getattr(module, "algorithm", None)
    if not callable(entrypoint):
        raise AlgorithmLookupError(
            "algorithms.registry._build_spec: "
            f"algorithms/{researcher_name}/{algorithm_name}/solver.py must define callable algorithm()."
        )

    exported_name = _exported_name(researcher_name, algorithm_name)
    description = getattr(module, "DESCRIPTION", "No description provided.")
    return AlgorithmSpec(
        name=exported_name,
        module=module,
        entrypoint=entrypoint,
        description=description,
    )


def discover_algorithm_import_failures() -> list[AlgorithmImportFailure]:
    failures: list[AlgorithmImportFailure] = []
    for researcher_name, algorithm_name in _iter_algorithm_packages():
        try:
            _build_spec(researcher_name, algorithm_name)
        except Exception as exc:
            failures.append(
                AlgorithmImportFailure(
                    name=_exported_name(researcher_name, algorithm_name),
                    error_message=str(exc),
                )
            )
    return sorted(failures, key=lambda failure: failure.name)


def discover_algorithms() -> list[AlgorithmSpec]:
    discovered: list[AlgorithmSpec] = []
    for researcher_name, algorithm_name in _iter_algorithm_packages():
        try:
            discovered.append(_build_spec(researcher_name, algorithm_name))
        except Exception:
            # TODO: Expose skipped-package diagnostics in a user-visible report.
            continue
    return sorted(discovered, key=lambda spec: spec.name)


def get_algorithm_spec(name: str) -> AlgorithmSpec:
    known_names: list[str] = []
    import_failures: dict[str, str] = {}

    for researcher_name, algorithm_name in _iter_algorithm_packages():
        exported_name = _exported_name(researcher_name, algorithm_name)
        known_names.append(exported_name)
        if exported_name != name:
            continue
        try:
            return _build_spec(researcher_name, algorithm_name)
        except Exception as exc:
            import_failures[exported_name] = str(exc)
            break

    if name in import_failures:
        raise AlgorithmLookupError(
            "algorithms.registry.get_algorithm_spec: "
            f"algorithm '{name}' exists, but failed to import.\n"
            f"{import_failures[name]}"
        )

    raise AlgorithmLookupError(
        "algorithms.registry.get_algorithm_spec: "
        f"unknown algorithm '{name}'. Available package names: {sorted(known_names)}. "
        "Run `python main.py --list-algorithms` to inspect available packages."
    )
