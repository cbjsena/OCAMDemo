# instance/views.py
import logging
import shutil
import zipfile
from io import BytesIO

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404
from django.shortcuts import redirect, render

from common import messages as msg
from common.menus import INSTANCE_SIDEBAR_MENU
from instance.services.instance_service import (
    build_sidebar_menu,
    compare_instances,
    discover_instances,
    get_csv_file_path,
    get_instance_by_name,
    get_instances_dir,
    read_csv_file,
    save_csv_file,
)

logger = logging.getLogger(__name__)


@login_required
def instance_list(request):
    """인스턴스 목록 표시."""
    instances = discover_instances()
    return render(
        request,
        "instance/instance_list.html",
        {
            "current_top_menu": "instance",
            "sidebar_menu": INSTANCE_SIDEBAR_MENU,
            "instances": instances,
        },
    )


@login_required
def instance_detail(request, instance_name, filename=None):
    """인스턴스 상세 — 좌측 사이드바에 파일 목록, 우측에 CSV 데이터 표시."""
    instance = get_instance_by_name(instance_name)
    if not instance:
        messages.error(request, msg.INSTANCE_NOT_FOUND.format(name=instance_name))
        return redirect("instance:instance_list")

    file_menu = build_sidebar_menu(instance)

    # 기본 파일: 첫 번째 CSV
    if not filename and instance["files"]:
        filename = instance["files"][0]

    headers, rows = [], []
    if filename:
        try:
            headers, rows = read_csv_file(instance_name, filename)
        except FileNotFoundError:
            messages.error(request, msg.ITEM_NOT_FOUND.format(item=filename))

    return render(
        request,
        "instance/instance_detail.html",
        {
            "current_top_menu": "instance",
            "sidebar_menu": INSTANCE_SIDEBAR_MENU,
            "instance": instance,
            "instance_name": instance_name,
            "file_menu": file_menu,
            "current_file": filename,
            "headers": headers,
            "rows": rows,
            "show_csv_buttons": bool(filename),
        },
    )


@login_required
def csv_download(request, instance_name, filename):
    """CSV 파일 다운로드."""
    try:
        file_path = get_csv_file_path(instance_name, filename)
        return FileResponse(
            open(file_path, "rb"),
            as_attachment=True,
            filename=filename,
            content_type="text/csv; charset=utf-8",
        )
    except FileNotFoundError:
        raise Http404(msg.ITEM_NOT_FOUND.format(item=filename))


@login_required
def csv_upload(request, instance_name, filename):
    """CSV 파일 업로드."""
    if request.method != "POST":
        return redirect("instance:instance_detail", instance_name=instance_name, filename=filename)

    csv_file = request.FILES.get("csv_file")
    if not csv_file:
        messages.error(request, msg.FILE_NOT_SELECTED)
        return redirect("instance:instance_detail", instance_name=instance_name, filename=filename)

    if not csv_file.name.endswith(".csv"):
        messages.error(request, msg.INVALID_FILE_EXT.format(ext="csv"))
        return redirect("instance:instance_detail", instance_name=instance_name, filename=filename)

    try:
        content = csv_file.read()
        save_csv_file(instance_name, filename, content)
        messages.success(request, msg.CSV_UPLOAD_SUCCESS.format(filename=filename))
    except Exception as e:
        logger.exception("CSV upload failed")
        messages.error(request, msg.SAVE_ERROR.format(target=filename, error=str(e)))

    return redirect("instance:instance_detail", instance_name=instance_name, filename=filename)


@login_required
def instance_folder_download(request, instance_name):
    """인스턴스 폴더 전체를 ZIP으로 다운로드."""
    instance = get_instance_by_name(instance_name)
    if not instance:
        raise Http404(f"Instance '{instance_name}' not found")

    # ZIP 생성
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        instance_path = instance["path"]
        for file_path in instance_path.glob("*"):
            if file_path.is_file():
                arcname = file_path.name
                zf.write(file_path, arcname)

    zip_buffer.seek(0)
    return FileResponse(
        zip_buffer,
        as_attachment=True,
        filename=f"{instance_name}.zip",
        content_type="application/zip",
    )


@login_required
def instance_upload(request):
    """ZIP 파일을 업로드하여 새 인스턴스로 추가."""
    if request.method == "POST":
        zip_file = request.FILES.get("folder_zip")
        if not zip_file:
            messages.error(request, msg.FILE_NOT_SELECTED)
            return redirect("instance:instance_upload")

        if not zip_file.name.endswith(".zip"):
            messages.error(request, msg.INVALID_FILE_EXT.format(ext="zip"))
            return redirect("instance:instance_upload")

        try:
            # ZIP 파일명에서 폴더명 결정 (확장자 제거)
            folder_name = zip_file.name.rsplit(".", 1)[0]
            instance_path = get_instances_dir() / folder_name

            if instance_path.exists():
                messages.error(request, f"Instance '{folder_name}' already exists")
                return redirect("instance:instance_upload")

            instance_path.mkdir(parents=True, exist_ok=True)

            # ZIP 추출
            with zipfile.ZipFile(zip_file, "r") as zf:
                zf.extractall(instance_path)

            # metadata.csv 확인
            metadata_path = instance_path / "metadata.csv"
            if not metadata_path.exists():
                shutil.rmtree(instance_path)
                messages.error(request, "Invalid instance: metadata.csv not found in ZIP")
                return redirect("instance:instance_upload")

            messages.success(request, f"Instance '{folder_name}' uploaded successfully")
            return redirect("instance:instance_list")

        except Exception as e:
            logger.exception("Instance folder upload failed")
            messages.error(request, msg.SAVE_ERROR.format(target="instance", error=str(e)))
            return redirect("instance:instance_upload")

    return render(
        request,
        "instance/instance_upload.html",
        {
            "current_top_menu": "instance",
            "sidebar_menu": INSTANCE_SIDEBAR_MENU,
        },
    )


@login_required
def instance_compare(request):
    """2개 인스턴스를 선택하여 파일 비교."""
    instances = discover_instances()
    comparison_result = None
    selected_instances = []

    if request.method == "POST":
        instance_name_1 = request.POST.get("instance_1")
        instance_name_2 = request.POST.get("instance_2")

        if not instance_name_1 or not instance_name_2:
            messages.error(request, "Please select two instances to compare")
            return redirect("instance:instance_compare")

        if instance_name_1 == instance_name_2:
            messages.error(request, "Please select two different instances")
            return redirect("instance:instance_compare")

        try:
            comparison_result = compare_instances(instance_name_1, instance_name_2)
            selected_instances = [instance_name_1, instance_name_2]
        except Exception as e:
            logger.exception("Instance comparison failed")
            messages.error(request, f"Comparison failed: {str(e)}")

    return render(
        request,
        "instance/instance_compare.html",
        {
            "current_top_menu": "instance",
            "sidebar_menu": INSTANCE_SIDEBAR_MENU,
            "instances": instances,
            "comparison_result": comparison_result,
            "selected_instances": selected_instances,
        },
    )
