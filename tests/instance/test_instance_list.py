"""
Instance List 테스트 (IN_LST_DIS_*)

테스트되는 시나리오:
- IN_LST_DIS_001: Instance List 메뉴 라우팅
- IN_LST_DIS_002: Instance 사이드바 3개 메뉴 구성
- IN_LST_DIS_003: Instance List 다운로드 컬럼 노출
- IN_LST_DIS_004: 인스턴스 폴더 다운로드 (정상)
- IN_LST_DIS_005: 인스턴스 폴더 다운로드 (없음)
"""

import zipfile
from io import BytesIO

import pytest
from django.urls import reverse


@pytest.mark.django_db
class TestInstanceList:
    """Instance List 화면 테스트"""

    def test_instance_list_menu_routing(self, authenticated_client, toy_v1_instance):
        # Scenario: IN_LST_DIS_001
        # Instance List 메뉴 라우팅
        response = authenticated_client.get(reverse("instance:instance_list"))

        assert response.status_code == 200
        assert "Instance List" in response.content.decode()
        assert "instances" in response.context

    def test_instance_sidebar_three_menus(self, authenticated_client, toy_v1_instance):
        # Scenario: IN_LST_DIS_002
        # Instance 사이드바 3개 메뉴 구성
        response = authenticated_client.get(reverse("instance:instance_list"))

        assert response.status_code == 200
        content = response.content.decode()

        # 3개 메뉴 확인
        assert "Instance List" in content
        assert "Instance Upload" in content
        assert "Compare Instances" in content

        # 메뉴 링크 확인
        assert "/instance/" in content or "instance:instance_list" in str(response.context)
        assert "/instance/upload/" in content or "instance:instance_upload" in str(response.context)
        assert "/instance/compare/" in content or "instance:instance_compare" in str(
            response.context
        )

    def test_instance_list_download_column_visible(self, authenticated_client, toy_v1_instance):
        # Scenario: IN_LST_DIS_003
        # Instance List 다운로드 컬럼 노출
        response = authenticated_client.get(reverse("instance:instance_list"))

        assert response.status_code == 200
        content = response.content.decode()

        # Download 컬럼 헤더 확인
        assert "Download" in content

        # 다운로드 버튼/아이콘 확인
        assert "bi-download" in content or "fa-download" in content or "download" in content.lower()

    def test_instance_folder_download_success(self, authenticated_client, toy_v1_instance):
        # Scenario: IN_LST_DIS_004
        # 인스턴스 폴더 다운로드 (정상)
        response = authenticated_client.get(
            reverse("instance:instance_folder_download", kwargs={"instance_name": "toy_v1"})
        )

        # Status 200, ZIP 파일 반환
        assert response.status_code == 200
        assert response["Content-Type"] == "application/zip"
        assert "attachment" in response["Content-Disposition"]
        assert "toy_v1.zip" in response["Content-Disposition"]

        # ZIP 내부 파일 확인 (FileResponse는 streaming_content 사용)
        zip_content = b"".join(response.streaming_content)
        zip_buffer = BytesIO(zip_content)
        with zipfile.ZipFile(zip_buffer, "r") as zf:
            file_list = zf.namelist()
            assert "metadata.csv" in file_list
            assert "port_info.csv" in file_list
            assert "vessel_data.csv" in file_list

    def test_instance_folder_download_not_found(self, authenticated_client):
        # Scenario: IN_LST_DIS_005
        # 인스턴스 폴더 다운로드 (없음)
        response = authenticated_client.get(
            reverse(
                "instance:instance_folder_download",
                kwargs={"instance_name": "no_such_instance"},
            )
        )

        # Status 404
        assert response.status_code == 404

    def test_instance_list_not_logged_in(self, client):
        # 로그인하지 않은 사용자는 리디렉트
        response = client.get(reverse("instance:instance_list"))
        assert response.status_code == 302  # redirect to login
