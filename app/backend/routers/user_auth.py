"""普通用户注册与登录路由。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.backend.dependencies.user_auth import require_user_login
from app.backend.services.user_auth_service import UserAuthService


class UserCredentials(BaseModel):
    """注册和登录共用的账号密码请求。"""

    username: str
    password: str


def create_user_auth_router(service: UserAuthService) -> APIRouter:
    """创建普通用户认证路由。"""
    router = APIRouter(prefix="/auth", tags=["user-auth"])

    @router.post("/register")
    async def register(credentials: UserCredentials) -> dict[str, Any]:
        return await service.register(credentials.model_dump())

    @router.post("/login")
    async def login(credentials: UserCredentials) -> dict[str, Any]:
        return await service.login(credentials.model_dump())

    @router.get("/me")
    async def me(user: dict[str, Any] = Depends(require_user_login)) -> dict[str, Any]:
        return {
            "ok": True,
            "data": {"id": user["user_id"], "username": user["username"]},
        }

    @router.post("/logout")
    async def logout(
        user: dict[str, Any] = Depends(require_user_login),
    ) -> dict[str, Any]:
        return await service.logout(str(user["token"]))

    return router
