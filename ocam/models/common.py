from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


def repr_value(value: Any) -> str:
    if isinstance(value, Path):
        return repr(str(value))
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    return repr(value)


def repr_fields(class_name: str, **fields: Any) -> str:
    body = ", ".join(
        f"{field_name}={repr_value(field_value)}"
        for field_name, field_value in fields.items()
    )
    return f"{class_name}({body})"


def serialize_temporal(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, timedelta):
        return value.total_seconds()
    return value
