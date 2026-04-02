"""请求守卫工具。"""

from __future__ import annotations

from typing import Optional

from fastapi import Request
from fastapi.responses import JSONResponse

from app.backend.dependencies.request_context import get_trace_id
from app.backend.repositories.database import SessionManager
from app.common.feature_flags import get_feature_flag
from app.common.request_rate_limiter import InMemorySlidingWindowRateLimiter


async def is_session_owner(session_id: str, user_id: str) -> bool:
    """校验指定会话是否属于目标用户。"""
    session = await SessionManager.get_session(session_id)
    if not session:
        return False
    return str(session.get("user_id") or "") == str(user_id or "")


def enforce_heavy_api_rate_limit(
    endpoint: str,
    user_id: str,
    session_id: str,
    request: Optional[Request],
    rate_limiter: Optional[InMemorySlidingWindowRateLimiter],
    endpoint_limits: dict[str, dict[str, int]],
    window_seconds: float,
) -> Optional[JSONResponse]:
    """执行重接口限流检查。"""
    if not get_feature_flag("ENABLE_HEAVY_API_RATE_LIMIT", True):
        return None
    if rate_limiter is None:
        return None
    config = endpoint_limits.get(endpoint)
    if not config:
        return None

    trace_id = get_trace_id(request)
    safe_user_id = str(user_id or "").strip()
    safe_session_id = str(session_id or "").strip()
    checks: list[tuple[str, str, int]] = []

    user_limit = int(config.get("user_limit") or 0)
    session_limit = int(config.get("session_limit") or 0)
    if safe_user_id and user_limit > 0:
        checks.append(("user_id", safe_user_id, user_limit))
    if safe_session_id and session_limit > 0:
        checks.append(("session_id", safe_session_id, session_limit))

    for dimension, value, limit in checks:
        key = f"{endpoint}:{dimension}:{value}"
        result = rate_limiter.check(
            key=key,
            limit=limit,
            window_seconds=window_seconds,
        )
        if bool(result.get("allowed")):
            continue
        retry_after_ms = int(result.get("retry_after_ms") or 0)
        return JSONResponse(
            status_code=429,
            content={
                "ok": False,
                "error": "rate_limited",
                "endpoint": endpoint,
                "dimension": dimension,
                "retry_after_ms": retry_after_ms,
                "trace_id": trace_id,
                "limit": int(result.get("limit") or limit),
                "window_seconds": float(result.get("window_seconds") or window_seconds),
            },
        )
    return None
