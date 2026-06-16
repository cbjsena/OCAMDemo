# common/templatetags/common_filters.py (항목 I)
from django import template

register = template.Library()


@register.filter
def split(value, sep=","):
    """문자열을 sep 기준으로 나누어 리스트로 반환."""
    if not value:
        return []
    return str(value).split(sep)


@register.filter
def strip(value):
    """문자열 양쪽 공백 제거."""
    if value is None:
        return ""
    return str(value).strip()


@register.filter
def replace_underscore(value):
    """언더스코어를 공백으로 변환 (CSV 파일명 → 메뉴 표시용)."""
    if not value:
        return ""
    return str(value).replace("_", " ").title()


@register.filter
def filename_stem(value):
    """파일명에서 확장자를 제거. 예: 'port_info.csv' → 'port_info'"""
    if not value:
        return ""
    return str(value).rsplit(".", 1)[0] if "." in str(value) else str(value)
