"""管理员会话管理服务。"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from app.backend.repositories.database import MessageManager, SessionManager, SessionMemoryManager


class AdminSessionsService:
    """封装管理员会话与记忆管理相关业务。"""

    def __init__(
        self,
        await_call: Callable[..., Awaitable[Any]],
        get_session_memory_service: Callable[[], Any],
    ) -> None:
        self._await_call = await_call
        self._get_session_memory_service = get_session_memory_service

    async def admin_get_sessions(self, limit: int = 100, offset: int = 0) -> dict[str, Any]:
        """获取所有会话列表。"""
        sessions = await self._await_call(SessionManager.get_all_sessions, limit, offset)
        total = await self._await_call(SessionManager.get_total_sessions_count)
        return {"ok": True, "data": sessions, "total": total}

    async def admin_get_session_messages(self, session_id: str) -> dict[str, Any]:
        """获取指定会话消息。"""
        session = await self._await_call(SessionManager.get_session, session_id)
        if not session:
            return {"ok": False, "error": "会话不存在"}
        messages = await self._await_call(MessageManager.get_messages, session_id)
        return {"ok": True, "session": session, "messages": messages}

    async def admin_get_session_memory(self, session_id: str) -> dict[str, Any]:
        """获取指定会话记忆快照。"""
        session = await self._await_call(SessionManager.get_session, session_id)
        if not session:
            return {"ok": False, "error": "会话不存在"}
        snapshot = await self._await_call(self._get_session_memory_service().load_memory, session_id)
        return {"ok": True, "data": snapshot}

    async def admin_rebuild_session_memory(self, session_id: str) -> dict[str, Any]:
        """重建指定会话记忆。"""
        session = await self._await_call(SessionManager.get_session, session_id)
        if not session:
            return {"ok": False, "error": "会话不存在"}
        await self._await_call(self._get_session_memory_service().clear_memory, session_id)
        snapshot = await self._await_call(self._get_session_memory_service().rebuild_from_messages, session_id)
        return {"ok": True, "data": snapshot}

    async def admin_clear_session_messages(self, session_id: str) -> dict[str, Any]:
        """清空指定会话历史消息。"""
        session = await self._await_call(SessionManager.get_session, session_id)
        if not session:
            return {"ok": False, "error": "会话不存在"}

        existing_messages = await self._await_call(MessageManager.get_messages, session_id)
        removed_count = len(existing_messages)
        if removed_count > 0:
            await self._await_call(MessageManager.delete_messages, session_id)
        await self._await_call(SessionMemoryManager.clear_summary, session_id)
        await self._await_call(SessionManager.update_session_time, session_id)

        return {"ok": True, "message": "会话历史记录已清空", "removed_count": removed_count}

    async def admin_delete_session(self, session_id: str) -> dict[str, Any]:
        """删除会话。"""
        success = await self._await_call(SessionManager.delete_session, session_id)
        if success:
            return {"ok": True, "message": "会话已删除"}
        return {"ok": False, "error": "删除失败或会话不存在"}
