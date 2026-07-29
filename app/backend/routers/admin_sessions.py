"""管理员会话路由。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.backend.dependencies.admin_auth import require_admin_access
from app.backend.services.admin_sessions_service import AdminSessionsService


def create_admin_sessions_router(service: AdminSessionsService) -> APIRouter:
    """创建管理员会话路由。"""
    router = APIRouter(dependencies=[Depends(require_admin_access)])

    @router.get("/admin/sessions")
    async def admin_get_sessions(limit: int = 100, offset: int = 0) -> dict[str, Any]:
        """获取所有会话列表。"""
        return await service.admin_get_sessions(limit=limit, offset=offset)

    @router.get("/admin/sessions/{session_id}/messages")
    async def admin_get_session_messages(session_id: str) -> dict[str, Any]:
        """获取指定会话消息。"""
        return await service.admin_get_session_messages(session_id=session_id)

    @router.get("/admin/sessions/{session_id}/observability")
    async def admin_get_session_observability(session_id: str) -> dict[str, Any]:
        """获取指定会话的按轮次观测详情。"""
        return await service.admin_get_session_observability(session_id=session_id)

    @router.get("/admin/sessions/{session_id}/memory")
    async def admin_get_session_memory(session_id: str) -> dict[str, Any]:
        """获取指定会话记忆快照。"""
        return await service.admin_get_session_memory(session_id=session_id)

    @router.post("/admin/sessions/{session_id}/memory/rebuild")
    async def admin_rebuild_session_memory(session_id: str) -> dict[str, Any]:
        """重建指定会话记忆。"""
        return await service.admin_rebuild_session_memory(session_id=session_id)

    @router.delete("/admin/sessions/{session_id}/messages")
    async def admin_clear_session_messages(session_id: str) -> dict[str, Any]:
        """清空指定会话历史消息。"""
        return await service.admin_clear_session_messages(session_id=session_id)

    @router.delete("/admin/sessions/{session_id}")
    async def admin_delete_session(session_id: str) -> dict[str, Any]:
        """删除会话。"""
        return await service.admin_delete_session(session_id=session_id)

    return router
