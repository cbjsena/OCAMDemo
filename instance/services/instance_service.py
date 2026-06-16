# instance/services/instance_service.py
"""
인스턴스 폴더 스캔 및 CSV 파일 관리 서비스.

instances 폴더 구조:
    instances/
        toy_v1/
            metadata.csv        ← 인스턴스 이름: "toy_v1"
            port_info.csv
            vessel_data.csv
        toy_v2/
            000_base/
                metadata.csv    ← 인스턴스 이름: "toy_v2~000_base"
                port_info.csv
"""

import csv
import io
from pathlib import Path

from django.conf import settings

from common.constants import INSTANCE_SEPARATOR, METADATA_FILENAME


def read_instance_metadata(metadata_path: Path) -> dict[str, str]:
    """metadata.csv를 key-value 맵으로 읽어 반환."""
    metadata: dict[str, str] = {}
    with open(metadata_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) < 2:
                continue
            key = row[0].strip()
            value = row[1].strip()
            # 헤더("key,value")는 건너뛴다.
            if key.lower() == "key" and value.lower() == "value":
                continue
            if key:
                metadata[key] = value
    return metadata


def get_instances_dir() -> Path:
    """인스턴스 루트 디렉토리 반환."""
    return Path(settings.INSTANCES_DIR)


def discover_instances() -> list[dict]:
    """
    instances 폴더를 재귀 스캔하여 metadata.csv가 있는 모든 인스턴스를 반환.

    Returns:
        [{"name": "toy_v1", "path": Path(...), "files": ["metadata.csv", "port_info.csv", ...]}, ]
    """
    root = get_instances_dir()
    if not root.exists():
        return []

    instances = []
    for metadata_path in sorted(root.rglob(METADATA_FILENAME)):
        instance_dir = metadata_path.parent
        relative = instance_dir.relative_to(root)
        # 하위 폴더 구분자: / → ~
        name = str(relative).replace("\\", "/").replace("/", INSTANCE_SEPARATOR)

        csv_files = sorted([f.name for f in instance_dir.glob("*.csv")])
        metadata = read_instance_metadata(metadata_path)
        instances.append(
            {
                "name": name,
                "path": instance_dir,
                "files": csv_files,
                "scenario_name": metadata.get("scenario_name", ""),
                "planning_horizon_start": metadata.get("planning_horizon_start", ""),
                "planning_horizon_end": metadata.get("planning_horizon_end", ""),
            }
        )

    return instances


def get_instance_by_name(name: str) -> dict | None:
    """이름으로 인스턴스를 찾아 반환."""
    root = get_instances_dir()
    # ~ → 경로 구분자
    relative = name.replace(INSTANCE_SEPARATOR, "/")
    instance_dir = root / relative

    metadata_path = instance_dir / METADATA_FILENAME
    if not metadata_path.exists():
        return None

    csv_files = sorted([f.name for f in instance_dir.glob("*.csv")])
    metadata = read_instance_metadata(metadata_path)
    return {
        "name": name,
        "path": instance_dir,
        "files": csv_files,
        "scenario_name": metadata.get("scenario_name", ""),
        "planning_horizon_start": metadata.get("planning_horizon_start", ""),
        "planning_horizon_end": metadata.get("planning_horizon_end", ""),
    }


def read_csv_file(instance_name: str, filename: str) -> tuple[list[str], list[list[str]]]:
    """
    인스턴스의 CSV 파일을 읽어서 (headers, rows) 반환.

    Returns:
        (["col1", "col2", ...], [["val1", "val2", ...], ...])
    """
    inst = get_instance_by_name(instance_name)
    if not inst:
        raise FileNotFoundError(f"Instance '{instance_name}' not found")

    file_path = inst["path"] / filename
    if not file_path.exists():
        raise FileNotFoundError(f"File '{filename}' not found in instance '{instance_name}'")

    with open(file_path, "r", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        rows = list(reader)

    if not rows:
        return [], []

    headers = rows[0]
    data = rows[1:]
    return headers, data


def save_csv_file(instance_name: str, filename: str, content: bytes) -> int:
    """
    업로드된 CSV 파일을 인스턴스 폴더에 저장.

    Returns:
        저장된 행 수 (헤더 제외)
    """
    inst = get_instance_by_name(instance_name)
    if not inst:
        raise FileNotFoundError(f"Instance '{instance_name}' not found")

    file_path = inst["path"] / filename

    # UTF-8 BOM 처리
    text = content.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)

    with open(file_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerows(rows)

    return max(0, len(rows) - 1)  # 헤더 제외


def get_csv_file_path(instance_name: str, filename: str) -> Path:
    """CSV 파일의 실제 경로를 반환."""
    inst = get_instance_by_name(instance_name)
    if not inst:
        raise FileNotFoundError(f"Instance '{instance_name}' not found")

    file_path = inst["path"] / filename
    if not file_path.exists():
        raise FileNotFoundError(f"File '{filename}' not found in instance '{instance_name}'")

    return file_path


def build_sidebar_menu(instance: dict) -> list[dict]:
    """
    인스턴스의 CSV 파일 목록을 사이드바 메뉴 구조로 변환.

    CSV 파일명 규칙: 단어_단어.csv → 메뉴에서 _ 제외, 계층 구조로 표시
    예: port_info.csv → group="port", item="info"
    """
    menu = []
    for filename in instance.get("files", []):
        stem = filename.rsplit(".", 1)[0] if "." in filename else filename
        parts = stem.split("_")
        display_name = " ".join(p.capitalize() for p in parts)
        menu.append(
            {
                "filename": filename,
                "display_name": display_name,
                "group": parts[0].capitalize() if parts else "",
                "parts": parts,
            }
        )
    return menu


def compare_instances(instance_name_1: str, instance_name_2: str) -> dict:
    """
    2개 인스턴스를 비교하여 각 파일의 동일 여부 및 다른 행 개수 반환.

    Returns:
        {
            "instance_1": "toy_v1",
            "instance_2": "toy_v2",
            "files": [
                {
                    "filename": "metadata.csv",
                    "status": "same" | "different",
                    "diff_rows": 0 | 5  (다른 파일인 경우)
                },
                ...
            ]
        }
    """
    inst1 = get_instance_by_name(instance_name_1)
    inst2 = get_instance_by_name(instance_name_2)

    if not inst1 or not inst2:
        raise ValueError("One or both instances not found")

    files_1 = set(inst1.get("files", []))
    files_2 = set(inst2.get("files", []))
    all_files = sorted(files_1 | files_2)

    comparison = {
        "instance_1": instance_name_1,
        "instance_2": instance_name_2,
        "files": [],
    }

    for filename in all_files:
        file_info = {
            "filename": filename,
            "status": "missing",
            "diff_rows": 0,
        }

        # 한쪽 파일이 없는 경우
        if filename not in files_1:
            file_info["status"] = "missing_in_1"
        elif filename not in files_2:
            file_info["status"] = "missing_in_2"
        else:
            # 두 파일 모두 존재 → 내용 비교
            try:
                headers_1, rows_1 = read_csv_file(instance_name_1, filename)
                headers_2, rows_2 = read_csv_file(instance_name_2, filename)

                # 헤더 비교
                if headers_1 != headers_2:
                    file_info["status"] = "different_headers"
                    file_info["diff_rows"] = max(len(rows_1), len(rows_2))
                # 행 개수 비교
                elif len(rows_1) != len(rows_2):
                    file_info["status"] = "different_rows"
                    file_info["diff_rows"] = abs(len(rows_1) - len(rows_2))
                # 행 내용 비교
                else:
                    diff_count = 0
                    for i, (row_1, row_2) in enumerate(zip(rows_1, rows_2)):
                        if row_1 != row_2:
                            diff_count += 1

                    if diff_count > 0:
                        file_info["status"] = "different"
                        file_info["diff_rows"] = diff_count
                    else:
                        file_info["status"] = "same"
            except Exception as e:
                file_info["status"] = "error"
                file_info["error"] = str(e)

        comparison["files"].append(file_info)

    return comparison
