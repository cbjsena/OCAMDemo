# tests/conftest.py (항목 G)
"""
pytest 공통 fixture.
- user, auth_client: 인증 테스트 기본
- tmp_instance: 임시 인스턴스 폴더 생성
"""

import csv
import os

import pytest
from django.contrib.auth.models import User


def pytest_configure(config):
    """로컬 환경일 때 마이그레이션 생략 (테스트 속도 향상)."""
    if os.getenv("APP_ENV") == "local":
        config.option.nomigrations = True


@pytest.fixture
def user(db):
    """일반 사용자."""
    return User.objects.create_user(username="test_user", password="password")


@pytest.fixture
def other_user(db):
    """다른 사용자 (권한 분리 테스트용)."""
    return User.objects.create_user(username="other_user", password="password")


@pytest.fixture
def admin_user(db):
    """관리자 사용자."""
    return User.objects.create_superuser(username="admin_user", password="password")


@pytest.fixture
def auth_client(client, user):
    """자동으로 로그인된 Client."""
    client.login(username="test_user", password="password")
    return client


@pytest.fixture
def tmp_instance(tmp_path, settings):
    """
    임시 인스턴스 폴더 생성.
    settings.INSTANCES_DIR을 tmp_path로 변경하고,
    test_instance 폴더에 metadata.csv + sample_data.csv 생성.
    """
    instances_dir = tmp_path / "instances"
    settings.INSTANCES_DIR = str(instances_dir)

    inst_dir = instances_dir / "test_instance"
    inst_dir.mkdir(parents=True)

    # metadata.csv
    metadata_path = inst_dir / "metadata.csv"
    with open(metadata_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["key", "value"])
        writer.writerow(["name", "test_instance"])
        writer.writerow(["description", "Test instance for unit tests"])

    # sample_data.csv
    data_path = inst_dir / "sample_data.csv"
    with open(data_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "name", "value"])
        writer.writerow(["1", "item_a", "100"])
        writer.writerow(["2", "item_b", "200"])

    return {
        "name": "test_instance",
        "path": inst_dir,
        "instances_dir": instances_dir,
    }


@pytest.fixture
def tmp_algorithms(tmp_path, settings):
    """
    임시 알고리즘 폴더 생성.
    settings.ALGORITHMS_DIR을 tmp_path로 변경.
    """
    algo_dir = tmp_path / "algorithms"
    settings.ALGORITHMS_DIR = str(algo_dir)

    # 유효한 알고리즘
    valid_dir = algo_dir / "tester" / "greedy"
    valid_dir.mkdir(parents=True)
    solver_path = valid_dir / "solver.py"
    solver_path.write_text(
        'def algorithm(instance_path=None):\n    return {"objective_value": 42.0}\n',
        encoding="utf-8",
    )

    # 무효한 알고리즘 (함수 없음)
    invalid_dir = algo_dir / "tester" / "broken"
    invalid_dir.mkdir(parents=True)
    (invalid_dir / "solver.py").write_text("x = 1\n", encoding="utf-8")

    return {
        "algorithms_dir": algo_dir,
        "valid": "tester/greedy",
        "invalid": "tester/broken",
    }


@pytest.fixture
def tmp_outputs(tmp_path, settings):
    """임시 outputs 폴더."""
    outputs_dir = tmp_path / "outputs"
    outputs_dir.mkdir()
    settings.OUTPUTS_DIR = str(outputs_dir)
    return outputs_dir
