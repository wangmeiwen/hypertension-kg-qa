"""数据库结果行的通用转换。"""

from __future__ import annotations

from typing import Any, Optional


def to_iso_timestamp(value: Any) -> Optional[str]:
    """将数据库时间值转换为 ISO 文本。"""
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    return str(isoformat()) if callable(isoformat) else str(value)
