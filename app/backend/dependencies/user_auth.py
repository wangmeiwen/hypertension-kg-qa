"""普通用户鉴权依赖。"""

from __future__ import annotations

from typing import Any

from fastapi import Request

from app.backend.dependencies.request_context import extract_bearer_token
from app.backend.infrastructure.errors import UnauthorizedError
from app.backend.services.user_auth_service import UserAuthService


async def require_user_login(request: Request) -> dict[str, Any]:
    """校验用户 token，并将服务端身份绑定到请求。"""
    token = extract_bearer_token(request.headers.get("Authorization", ""))
    user = await UserAuthService().authenticate_token(token)
    if user is None:
        raise UnauthorizedError("用户未登录或登录已过期")
    request.state.user_id = user["user_id"]
    return {**user, "token": token}
