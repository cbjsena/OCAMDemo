"""
Instance 앱 테스트용 pytest fixtures 및 공통 데이터

공용 fixture:
- authenticated_client: 로그인된 Django test client
- test_instances_setup: 테스트용 인스턴스 생성
- instance_with_csv_files: CSV 파일이 있는 인스턴스
"""

import csv
import shutil
import zipfile

import pytest
from django.conf import settings
from django.contrib.auth.models import User
from django.test import Client


@pytest.fixture
def authenticated_client():
    """로그인된 Django test client 반환"""
    client = Client()
    user = User.objects.create_user(
        username="testuser", email="test@example.com", password="testpass123"
    )
    client.login(username="testuser", password="testpass123")
    yield client
    # cleanup
    user.delete()


@pytest.fixture
def instances_dir(tmp_path):
    """임시 인스턴스 디렉토리 반환"""
    instances_dir = tmp_path / "instances"
    instances_dir.mkdir()

    # Django settings의 INSTANCES_DIR 임시 변경
    original_dir = settings.INSTANCES_DIR
    settings.INSTANCES_DIR = str(instances_dir)

    yield instances_dir

    # cleanup
    settings.INSTANCES_DIR = original_dir
    if instances_dir.exists():
        shutil.rmtree(instances_dir)


@pytest.fixture
def toy_v1_instance(instances_dir):
    """toy_v1 인스턴스 생성 (metadata.csv + port_info.csv + vessel_data.csv)"""
    instance_path = instances_dir / "toy_v1"
    instance_path.mkdir()

    # metadata.csv
    metadata_file = instance_path / "metadata.csv"
    with open(metadata_file, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["key", "value"])
        writer.writerow(["scenario_name", "Toy Scenario V1"])
        writer.writerow(["planning_horizon_start", "2026-01-01"])
        writer.writerow(["planning_horizon_end", "2026-12-31"])

    # port_info.csv
    port_file = instance_path / "port_info.csv"
    with open(port_file, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["port_code", "port_name", "continent"])
        writer.writerow(["PUS", "Busan", "Asia"])
        writer.writerow(["TYO", "Tokyo", "Asia"])
        writer.writerow(["SIN", "Singapore", "Asia"])

    # vessel_data.csv
    vessel_file = instance_path / "vessel_data.csv"
    with open(vessel_file, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["vessel_code", "vessel_name", "capacity"])
        writer.writerow(["V001", "Ship A", "10000"])
        writer.writerow(["V002", "Ship B", "15000"])

    return instance_path


@pytest.fixture
def toy_v2_instance(instances_dir):
    """toy_v2 인스턴스 생성 (동일한 구조)"""
    instance_path = instances_dir / "toy_v2"
    instance_path.mkdir()

    # metadata.csv
    metadata_file = instance_path / "metadata.csv"
    with open(metadata_file, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["key", "value"])
        writer.writerow(["scenario_name", "Toy Scenario V2"])
        writer.writerow(["planning_horizon_start", "2026-02-01"])
        writer.writerow(["planning_horizon_end", "2026-11-30"])

    # port_info.csv (동일)
    port_file = instance_path / "port_info.csv"
    with open(port_file, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["port_code", "port_name", "continent"])
        writer.writerow(["PUS", "Busan", "Asia"])
        writer.writerow(["TYO", "Tokyo", "Asia"])
        writer.writerow(["SIN", "Singapore", "Asia"])

    # vessel_data.csv (동일)
    vessel_file = instance_path / "vessel_data.csv"
    with open(vessel_file, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["vessel_code", "vessel_name", "capacity"])
        writer.writerow(["V001", "Ship A", "10000"])
        writer.writerow(["V002", "Ship B", "15000"])

    return instance_path


@pytest.fixture
def toy_v3_different_instance(instances_dir):
    """toy_v3 인스턴스 (다른 데이터)"""
    instance_path = instances_dir / "toy_v3"
    instance_path.mkdir()

    # metadata.csv
    metadata_file = instance_path / "metadata.csv"
    with open(metadata_file, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["key", "value"])
        writer.writerow(["scenario_name", "Toy Scenario V3"])
        writer.writerow(["planning_horizon_start", "2026-03-01"])
        writer.writerow(["planning_horizon_end", "2026-10-31"])

    # port_info.csv (다른 데이터)
    port_file = instance_path / "port_info.csv"
    with open(port_file, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["port_code", "port_name", "continent"])
        writer.writerow(["LAX", "Los Angeles", "North America"])
        writer.writerow(["NYC", "New York", "North America"])

    # vessel_data.csv (없음 - 파일 누락 테스트용)

    return instance_path


@pytest.fixture
def valid_instance_zip(instances_dir, tmp_path):
    """유효한 인스턴스 ZIP 파일 생성"""
    zip_path = tmp_path / "new_case.zip"

    with zipfile.ZipFile(zip_path, "w") as zf:
        # metadata.csv
        metadata_content = b"""key,value
scenario_name,New Case
planning_horizon_start,2026-04-01
planning_horizon_end,2026-09-30
"""
        zf.writestr("metadata.csv", metadata_content)

        # port_info.csv
        port_content = b"""port_code,port_name,continent
HKG,Hong Kong,Asia
DXB,Dubai,Middle East
"""
        zf.writestr("port_info.csv", port_content)

    return zip_path


@pytest.fixture
def invalid_zip_no_metadata(tmp_path):
    """metadata.csv 없는 ZIP 파일"""
    zip_path = tmp_path / "bad_case.zip"

    with zipfile.ZipFile(zip_path, "w") as zf:
        # metadata.csv 없음
        port_content = b"""port_code,port_name,continent
PUS,Busan,Asia
"""
        zf.writestr("port_info.csv", port_content)

    return zip_path


@pytest.fixture
def invalid_file_txt(tmp_path):
    """txt 파일 (확장자 오류 테스트용)"""
    txt_path = tmp_path / "invalid.txt"
    txt_path.write_text("This is not a zip file")
    return txt_path


@pytest.mark.django_db
class TestInstanceBase:
    """Instance 테스트 기베 클래스"""

    pass
