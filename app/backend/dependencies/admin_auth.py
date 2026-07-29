"""管理员鉴权依赖。"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, Request

from app.backend.dependencies.request_context import get_trace_id
from app.backend.infrastructure.errors import (
    ForbiddenError,
    RateLimitedError,
    UnauthorizedError,
)
from app.backend.infrastructure.runtime_container import BackendRuntimeContainer
from app.backend.security.admin_access import resolve_admin_access_rule
from app.backend.security.admin_auth import get_current_admin_session


def require_admin_login() -> dict[str, Any]:
    """要求当前请求已登录管理员。"""
    session = get_current_admin_session()
    if session is None:
        raise UnauthorizedError("未登录")
    return session


def require_admin_access(
    request: Request,
    session: dict[str, Any] = Depends(require_admin_login),
) -> dict[str, Any]:
    """校验当前管理路由的权限并执行按管理员维度限流。"""
    rule = resolve_admin_access_rule(request.method, request.url.path)
    request.state.admin_permission = rule.permission
    request.state.admin_actor = str(session["username"])
    permissions = {
        str(permission) for permission in session["permissions"] if str(permission)
    }
    if rule.permission not in permissions:
        request.state.admin_audit_outcome = "denied"
        raise ForbiddenError(f"permission_denied:{rule.permission}")

    if not hasattr(request.app.state, "runtime"):
        return session
    runtime = request.app.state.runtime
    if not isinstance(runtime, BackendRuntimeContainer):
        return session
    limiter = runtime.rate_limiter
    if limiter is None:
        return session

    window_seconds = runtime.config.rate_limit_window_seconds
    result = limiter.check(
        key=f"admin:{rule.permission}:{request.state.admin_actor}",
        limit=rule.limit,
        window_seconds=window_seconds,
    )
    if bool(result["allowed"]):
        return session

    request.state.admin_audit_outcome = "rate_limited"
    raise RateLimitedError(
        endpoint=f"admin:{rule.permission}",
        dimension="admin",
        retry_after_ms=int(result["retry_after_ms"]),
        trace_id=get_trace_id(request),
        limit=int(result["limit"]),
        window_seconds=float(result["window_seconds"]),
    )
