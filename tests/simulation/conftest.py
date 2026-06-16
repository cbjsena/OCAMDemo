import csv
import io
import zipfile
from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from simulation.models import SimulationRun


@pytest.fixture
def simulation_env(tmp_path, settings):
    instances_dir = tmp_path / "instances"
    algorithms_dir = tmp_path / "algorithms"
    instances_dir.mkdir(parents=True, exist_ok=True)
    algorithms_dir.mkdir(parents=True, exist_ok=True)
    settings.INSTANCES_DIR = str(instances_dir)
    settings.ALGORITHMS_DIR = str(algorithms_dir)
    return {
        "instances_dir": instances_dir,
        "algorithms_dir": algorithms_dir,
    }


def _write_csv(path: Path, rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerows(rows)


@pytest.fixture
def sample_instances(simulation_env):
    instances_dir = simulation_env["instances_dir"]

    toy_v1 = instances_dir / "toy_v1"
    _write_csv(
        toy_v1 / "metadata.csv",
        [
            ["key", "value"],
            ["scenario_name", "Toy V1"],
            ["planning_horizon_start", "2026-01-01"],
            ["planning_horizon_end", "2026-12-31"],
        ],
    )
    _write_csv(toy_v1 / "port_info.csv", [["port_code"], ["PUS"]])

    toy_v2 = instances_dir / "toy_v2"
    _write_csv(
        toy_v2 / "metadata.csv",
        [
            ["key", "value"],
            ["scenario_name", "Toy V2"],
            ["planning_horizon_start", "2026-02-01"],
            ["planning_horizon_end", "2026-11-30"],
        ],
    )
    _write_csv(toy_v2 / "port_info.csv", [["port_code"], ["TYO"]])

    return ["toy_v1", "toy_v2"]


@pytest.fixture
def sample_algorithms(simulation_env):
    root = simulation_env["algorithms_dir"]

    valid = root / "yongs" / "only_virtual"
    valid.mkdir(parents=True, exist_ok=True)
    (valid / "__init__.py").write_text("", encoding="utf-8")
    (valid / "solver.py").write_text(
        "def algorithm(instance_data, timelimit):\n    return {'ok': True}\n",
        encoding="utf-8",
    )

    second = root / "kim" / "mcf_v5"
    second.mkdir(parents=True, exist_ok=True)
    (second / "__init__.py").write_text("", encoding="utf-8")
    (second / "solver.py").write_text(
        "def algorithm(instance_data, timelimit):\n    return {'ok': True}\n",
        encoding="utf-8",
    )

    invalid = root / "bad" / "broken"
    invalid.mkdir(parents=True, exist_ok=True)
    (invalid / "__init__.py").write_text("", encoding="utf-8")
    (invalid / "solver.py").write_text("x = 1\n", encoding="utf-8")

    return {
        "valid": "yongs/only_virtual",
        "valid2": "kim/mcf_v5",
        "invalid": "bad/broken",
    }


@pytest.fixture
def mock_task_delay(monkeypatch):
    class _TaskResult:
        id = "task-test-001"

    def _fake_delay(_simulation_id):
        return _TaskResult()

    monkeypatch.setattr("simulation.views.run_simulation_task.delay", _fake_delay)


@pytest.fixture
def simulation_factory(db, user):
    def _make(**kwargs):
        data = {
            "instance_name": "toy_v1",
            "algorithm_name": "yongs/only_virtual",
            "status": "PENDING",
            "progress": 0,
            "created_by": user,
        }
        data.update(kwargs)
        return SimulationRun.objects.create(**data)

    return _make


@pytest.fixture
def algorithm_zip_factory():
    def _make(file_name="new_algo.zip", internal_files=None):
        if internal_files is None:
            internal_files = {
                "gildong/vessel_swap/__init__.py": b"",
                "gildong/vessel_swap/solver.py": (
                    b"def algorithm(instance_data, timelimit):\n" b"    return {'ok': True}\n"
                ),
            }

        bio = io.BytesIO()
        with zipfile.ZipFile(bio, "w", zipfile.ZIP_DEFLATED) as zf:
            for path, content in internal_files.items():
                zf.writestr(path, content)
        bio.seek(0)
        return SimpleUploadedFile(file_name, bio.read(), content_type="application/zip")

    return _make
