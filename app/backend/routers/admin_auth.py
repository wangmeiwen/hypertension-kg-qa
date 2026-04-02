"""管理员认证路由。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.backend.services.admin_auth_service import AdminAuthService


def create_admin_auth_router(service: AdminAuthService) -> APIRouter:
    """创建管理员认证路由。"""
    router = APIRouter()

    @router.post("/admin/login")
    async def admin_login(data: dict) -> dict[str, Any]:
        """管理员登录。"""
        return await service.admin_login(data)

    @router.get("/admin/status")
    async def admin_status() -> dict[str, Any]:
        """检查管理员状态。"""
        return await service.admin_status()

    @router.post("/admin/logout")
    async def admin_logout() -> dict[str, Any]:
        """退出登录。"""
        return await service.admin_logout()

    return router
