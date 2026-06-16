"""
Compare Instances 테스트 (IN_CMP_DIS_*)

테스트되는 시나리오:
- IN_CMP_DIS_001: Compare Instances 메뉴 라우팅
- IN_CMP_DIS_002: Compare Instances 유효성 (미선택)
- IN_CMP_DIS_003: Compare Instances 유효성 (동일 인스턴스 선택)
- IN_CMP_DIS_004: Compare Instances 결과 (완전 동일)
- IN_CMP_DIS_005: Compare Instances 결과 (차이 행 개수)
- IN_CMP_DIS_006: Compare Instances 결과 (파일 누락)
- IN_CMP_DIS_007: Compare Instances 결과 (헤더 불일치)
- IN_CMP_DIS_008: Compare Instances 결과 (행 수 불일치)
"""

import csv

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse


@pytest.mark.django_db
class TestInstanceCompare:
    """Instance Compare 기능 테스트"""

    def test_compare_menu_routing(self, authenticated_client, toy_v1_instance, toy_v2_instance):
        # Scenario: IN_CMP_DIS_001
        # Compare Instances 메뉴 라우팅
        response = authenticated_client.get(reverse("instance:instance_compare"))

        assert response.status_code == 200
        content = response.content.decode()
        assert "Compare" in content or "compare" in content.lower()
        assert "instance_1" in content or "Instance 1" in content
        assert "instance_2" in content or "Instance 2" in content

    def test_compare_validation_not_selected(self, authenticated_client):
        # Scenario: IN_CMP_DIS_002
        # Compare Instances 유효성 (미선택)
        response = authenticated_client.post(reverse("instance:instance_compare"), {}, follow=True)

        assert response.status_code == 200

        # 오류 메시지
        messages = list(get_messages(response.wsgi_request))
        assert any("select" in str(m).lower() or "instance" in str(m).lower() for m in messages)

    def test_compare_validation_same_instance(self, authenticated_client, toy_v1_instance):
        # Scenario: IN_CMP_DIS_003
        # Compare Instances 유효성 (동일 인스턴스 선택)
        response = authenticated_client.post(
            reverse("instance:instance_compare"),
            {"instance_1": "toy_v1", "instance_2": "toy_v1"},
            follow=True,
        )

        assert response.status_code == 200

        # 오류 메시지
        messages = list(get_messages(response.wsgi_request))
        assert any("different" in str(m).lower() for m in messages)

    def test_compare_result_identical(self, authenticated_client, toy_v1_instance, toy_v2_instance):
        # Scenario: IN_CMP_DIS_004
        # Compare Instances 결과 (완전 동일)
        # toy_v1과 toy_v2는 구조가 동일하지만 내용이 조금 다르므로 별도 fixture 필요

        # toy_v1과 동일한 toy_v2 내용으로 교체
        port_file = toy_v2_instance / "port_info.csv"
        with open(port_file, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["port_code", "port_name", "continent"])
            writer.writerow(["PUS", "Busan", "Asia"])
            writer.writerow(["TYO", "Tokyo", "Asia"])
            writer.writerow(["SIN", "Singapore", "Asia"])

        response = authenticated_client.post(
            reverse("instance:instance_compare"),
            {"instance_1": "toy_v1", "instance_2": "toy_v2"},
            follow=True,
        )

        assert response.status_code == 200
        content = response.content.decode()

        # 비교 결과 테이블 확인
        assert "Comparison Results" in content or "port_info.csv" in content
        # Same 상태 확인
        assert "Same" in content or "same" in content.lower()

    def test_compare_result_different_rows(
        self, authenticated_client, toy_v1_instance, toy_v3_different_instance
    ):
        # Scenario: IN_CMP_DIS_005
        # Compare Instances 결과 (차이 행 개수)

        # toy_v1과 toy_v3는 내용이 다름
        response = authenticated_client.post(
            reverse("instance:instance_compare"),
            {"instance_1": "toy_v1", "instance_2": "toy_v3"},
            follow=True,
        )

        assert response.status_code == 200
        content = response.content.decode()

        # 비교 결과에서 different 또는 mismatch 확인
        assert any(
            keyword in content.lower() for keyword in ["different", "mismatch", "comparison"]
        )

    def test_compare_result_file_missing(
        self, authenticated_client, toy_v1_instance, toy_v3_different_instance
    ):
        # Scenario: IN_CMP_DIS_006
        # Compare Instances 결과 (파일 누락)
        # toy_v3는 vessel_data.csv가 없음

        response = authenticated_client.post(
            reverse("instance:instance_compare"),
            {"instance_1": "toy_v1", "instance_2": "toy_v3"},
            follow=True,
        )

        assert response.status_code == 200
        content = response.content.decode()

        # Missing 상태 확인
        assert "Missing" in content or "missing" in content.lower()

    def test_compare_result_header_mismatch(self, authenticated_client, instances_dir):
        # Scenario: IN_CMP_DIS_007
        # Compare Instances 결과 (헤더 불일치)

        # header_mismatch_1 인스턴스
        inst1 = instances_dir / "header_mismatch_1"
        inst1.mkdir()

        metadata = inst1 / "metadata.csv"
        metadata.write_text("key,value\nscenario_name,Header Test 1")

        port_file = inst1 / "port_info.csv"
        with open(port_file, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["port_code", "port_name", "continent"])
            writer.writerow(["PUS", "Busan", "Asia"])

        # header_mismatch_2 인스턴스 (헤더가 다름)
        inst2 = instances_dir / "header_mismatch_2"
        inst2.mkdir()

        metadata2 = inst2 / "metadata.csv"
        metadata2.write_text("key,value\nscenario_name,Header Test 2")

        port_file2 = inst2 / "port_info.csv"
        with open(port_file2, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["port_id", "port_desc", "region"])  # 다른 헤더
            writer.writerow(["PUS", "Busan", "Asia"])

        response = authenticated_client.post(
            reverse("instance:instance_compare"),
            {"instance_1": "header_mismatch_1", "instance_2": "header_mismatch_2"},
            follow=True,
        )

        assert response.status_code == 200
        content = response.content.decode()

        # Header Mismatch 확인
        assert "Header" in content or "header" in content.lower()
        assert "Mismatch" in content or "mismatch" in content.lower()

    def test_compare_result_row_count_mismatch(self, authenticated_client, instances_dir):
        # Scenario: IN_CMP_DIS_008
        # Compare Instances 결과 (행 수 불일치)

        # row_count_1 인스턴스
        inst1 = instances_dir / "row_count_1"
        inst1.mkdir()

        metadata = inst1 / "metadata.csv"
        metadata.write_text("key,value\nscenario_name,Row Test 1")

        port_file = inst1 / "port_info.csv"
        with open(port_file, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["port_code", "port_name", "continent"])
            writer.writerow(["PUS", "Busan", "Asia"])
            writer.writerow(["TYO", "Tokyo", "Asia"])

        # row_count_2 인스턴스 (행 수 다름)
        inst2 = instances_dir / "row_count_2"
        inst2.mkdir()

        metadata2 = inst2 / "metadata.csv"
        metadata2.write_text("key,value\nscenario_name,Row Test 2")

        port_file2 = inst2 / "port_info.csv"
        with open(port_file2, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["port_code", "port_name", "continent"])
            writer.writerow(["PUS", "Busan", "Asia"])
            # TYO 행 없음

        response = authenticated_client.post(
            reverse("instance:instance_compare"),
            {"instance_1": "row_count_1", "instance_2": "row_count_2"},
            follow=True,
        )

        assert response.status_code == 200
        content = response.content.decode()

        # Row Count Mismatch 확인
        assert "Row" in content or "row" in content.lower()
        assert "Count" in content or "Mismatch" in content

    def test_compare_not_logged_in(self, client):
        # 로그인하지 않은 사용자는 리디렉트
        response = client.get(reverse("instance:instance_compare"))
        assert response.status_code == 302  # redirect to login
