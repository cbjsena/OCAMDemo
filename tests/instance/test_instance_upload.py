"""
Instance Upload 테스트 (IN_UPL_DIS_*)

테스트되는 시나리오:
- IN_UPL_DIS_001: Instance Upload 메뉴 라우팅
- IN_UPL_DIS_002: 인스턴스 폴더 업로드 (정상)
- IN_UPL_DIS_003: 인스턴스 폴더 업로드 (파일 미선택)
- IN_UPL_DIS_004: 인스턴스 폴더 업로드 (확장자 오류)
- IN_UPL_DIS_005: 인스턴스 폴더 업로드 (metadata 누락)
- IN_UPL_DIS_006: 인스턴스 폴더 업로드 (중복 이름)
"""

import zipfile

import pytest
from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse


@pytest.mark.django_db
class TestInstanceUpload:
    """Instance Upload 기능 테스트"""

    def test_upload_menu_routing(self, authenticated_client):
        # Scenario: IN_UPL_DIS_001
        # Instance Upload 메뉴 라우팅
        response = authenticated_client.get(reverse("instance:instance_upload"))

        assert response.status_code == 200
        content = response.content.decode()
        assert "Upload" in content or "upload" in content.lower()
        assert "folder_zip" in content or "file" in content.lower()

    def test_upload_folder_success(self, authenticated_client, instances_dir, valid_instance_zip):
        # Scenario: IN_UPL_DIS_002
        # 인스턴스 폴더 업로드 (정상)
        with open(valid_instance_zip, "rb") as f:
            zip_content = f.read()

        uploaded_file = SimpleUploadedFile(
            name="new_case.zip", content=zip_content, content_type="application/zip"
        )

        response = authenticated_client.post(
            reverse("instance:instance_upload"),
            {"folder_zip": uploaded_file},
            follow=True,
        )

        # Redirect to instance list
        assert response.status_code == 200

        # 성공 메시지
        messages = list(get_messages(response.wsgi_request))
        assert any(
            "uploaded successfully" in str(m).lower() for m in messages
        ), f"Expected 'uploaded successfully' message, got: {[str(m) for m in messages]}"

        # 폴더 생성 확인
        assert (instances_dir / "new_case").exists()
        assert (instances_dir / "new_case" / "metadata.csv").exists()

    def test_upload_file_not_selected(self, authenticated_client):
        # Scenario: IN_UPL_DIS_003
        # 인스턴스 폴더 업로드 (파일 미선택)
        response = authenticated_client.post(reverse("instance:instance_upload"), {}, follow=True)

        assert response.status_code == 200

        # 오류 메시지
        messages = list(get_messages(response.wsgi_request))
        assert any(
            "file" in str(m).lower() and ("not" in str(m).lower() or "select" in str(m).lower())
            for m in messages
        )

    def test_upload_invalid_extension(self, authenticated_client, invalid_file_txt):
        # Scenario: IN_UPL_DIS_004
        # 인스턴스 폴더 업로드 (확장자 오류)
        with open(invalid_file_txt, "rb") as f:
            txt_content = f.read()

        uploaded_file = SimpleUploadedFile(
            name="invalid.txt", content=txt_content, content_type="text/plain"
        )

        response = authenticated_client.post(
            reverse("instance:instance_upload"),
            {"folder_zip": uploaded_file},
            follow=True,
        )

        assert response.status_code == 200

        # 오류 메시지
        messages = list(get_messages(response.wsgi_request))
        assert any(
            "zip" in str(m).lower() for m in messages
        ), f"Expected 'zip' message, got: {[str(m) for m in messages]}"

    def test_upload_missing_metadata(
        self, authenticated_client, instances_dir, invalid_zip_no_metadata
    ):
        # Scenario: IN_UPL_DIS_005
        # 인스턴스 폴더 업로드 (metadata 누락)
        with open(invalid_zip_no_metadata, "rb") as f:
            zip_content = f.read()

        uploaded_file = SimpleUploadedFile(
            name="bad_case.zip", content=zip_content, content_type="application/zip"
        )

        response = authenticated_client.post(
            reverse("instance:instance_upload"),
            {"folder_zip": uploaded_file},
            follow=True,
        )

        assert response.status_code == 200

        # 오류 메시지
        messages = list(get_messages(response.wsgi_request))
        assert any(
            "metadata" in str(m).lower() for m in messages
        ), f"Expected 'metadata' message, got: {[str(m) for m in messages]}"

        # 폴더가 생성되지 않았거나 롤백됨
        assert not (instances_dir / "bad_case").exists()

    def test_upload_duplicate_name(self, authenticated_client, instances_dir, toy_v1_instance):
        # Scenario: IN_UPL_DIS_006
        # 인스턴스 폴더 업로드 (중복 이름)

        # toy_v1이 이미 존재하므로 동일 이름을 가진 ZIP 생성
        import os
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            # toy_v1.zip 파일 생성
            with zipfile.ZipFile(tmp_path, "w") as zf:
                zf.writestr("metadata.csv", b"key,value\nscenario_name,Duplicate")

            # 파일을 읽어서 SimpleUploadedFile로 생성
            with open(tmp_path, "rb") as f:
                zip_content = f.read()

            # SimpleUploadedFile을 사용한 올바른 파일 업로드
            uploaded_file = SimpleUploadedFile(
                name="toy_v1.zip", content=zip_content, content_type="application/zip"
            )

            response = authenticated_client.post(
                reverse("instance:instance_upload"),
                {"folder_zip": uploaded_file},
                follow=True,
            )

            assert response.status_code == 200

            # 오류 메시지
            messages = list(get_messages(response.wsgi_request))
            assert any(
                "already exists" in str(m).lower() or "exist" in str(m).lower() for m in messages
            ), f"Expected 'already exists' message, got: {[str(m) for m in messages]}"
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def test_upload_not_logged_in(self, client):
        # 로그인하지 않은 사용자는 리디렉트
        response = client.get(reverse("instance:instance_upload"))
        assert response.status_code == 302  # redirect to login
