# common/utils/date_utils.py
from datetime import datetime


def now_str(fmt="%Y%m%d_%H%M%S"):
    """현재 시각을 문자열로 반환 (outputs 폴더명 생성용)."""
    return datetime.now().strftime(fmt)


def parse_datetime_folder(folder_name):
    """폴더명을 datetime 객체로 변환.
    지원 포맷:
      - YYMMDD_HHMM        (OCAM 원소스: '260615_1420')
      - YYMMDD_HHMM (N)   (OCAM 중복 suffix: '260615_1420 (2)')
      - YYYYMMDD_HHMMSS   (legacy: '20260615_143022')
    """
    base = folder_name.split(" ")[0]  # '(2)' suffix 제거
    for fmt in ("%y%m%d_%H%M", "%Y%m%d_%H%M%S", "%Y%m%d_%H%M"):
        try:
            return datetime.strptime(base, fmt)
        except ValueError:
            continue
    return None
