"""管理端结构化审计事件。"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging


logger = logging.getLogger("admin_audit")
ADMIN_AUDIT_SCHEMA_VERSION = "admin_audit.v1"


def log_admin_audit_event(
    *,
    trace_id: str,
    actor: str,
    method: str,
    path: str,
    permission: str,
    outcome: str,
    status_code: int,
    duration_ms: float,
    client_ip: str,
) -> None:
    """记录不含请求正文、凭据和文件内容的管理端审计事件。"""
    payload = {
        "schema_version": ADMIN_AUDIT_SCHEMA_VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "trace_id": trace_id,
        "actor": actor,
        "method": method,
        "path": path,
        "permission": permission,
        "outcome": outcome,
        "status_code": status_code,
        "duration_ms": round(duration_ms, 3),
        "client_ip": client_ip,
    }
    logger.info(
        "admin_audit %s", json.dumps(payload, ensure_ascii=False, sort_keys=True)
    )
