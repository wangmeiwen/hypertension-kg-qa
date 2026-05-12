"""管理员鉴权依赖。"""

from __future__ import annotations

from app.backend.infrastructure.errors import UnauthorizedError
from app.backend.security.admin_auth import is_logged_in


def require_admin_login() -> None:
    """要求当前请求已登录管理员。"""
    if not is_logged_in():
        raise UnauthorizedError("未登录")
