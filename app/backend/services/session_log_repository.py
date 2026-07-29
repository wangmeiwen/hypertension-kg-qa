"""会话观测日志的只读访问组件。"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any


def safe_log_id(raw: Any, fallback: str = "unknown") -> str:
    """将外部标识清洗为安全的日志路径片段。"""
    text = str(raw or "").strip()
    if not text:
        return fallback
    cleaned = "".join(
        ch if (ch.isalnum() or ch in {"-", "_"}) else "_" for ch in text
    ).strip("_")
    return cleaned or fallback


def read_request_records(
    *,
    project_root: Path,
    session_id: Any,
    request_id: Any,
    category: str,
    logger: logging.Logger,
    warning_event: str,
) -> list[dict[str, Any]]:
    """读取某个请求在指定日志类别下的全部 JSONL 记录。"""
    safe_request_id = str(request_id or "").strip()
    if not safe_request_id:
        return []
    safe_session_id = safe_log_id(session_id)
    records_dir = project_root / "logs" / "sessions" / safe_session_id / category
    if not records_dir.exists():
        return []

    rows: list[dict[str, Any]] = []
    for file_path in sorted(records_dir.glob("*.jsonl")):
        try:
            for line in file_path.read_text(
                encoding="utf-8", errors="ignore"
            ).splitlines():
                if not line.strip():
                    continue
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    continue
                if str(payload.get("request_id") or "") == safe_request_id:
                    rows.append(payload)
        except (OSError, ValueError, TypeError) as exc:
            logger.warning(
                "%s: session_id=%s request_id=%s file=%s error_type=%s error=%s",
                warning_event,
                safe_session_id,
                safe_request_id,
                str(file_path),
                type(exc).__name__,
                exc,
            )
    rows.sort(key=lambda item: str(item.get("ts") or ""))
    return rows
