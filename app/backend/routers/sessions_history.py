"""会话历史与上传相关路由。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, UploadFile

from app.backend.services.sessions_history_service import SessionsHistoryService


def create_sessions_router(service: SessionsHistoryService) -> APIRouter:
    """创建会话与上传路由。"""
    router = APIRouter()

    @router.post("/sessions")
    async def create_session(data: dict[str, Any]) -> dict[str, Any]:
        """创建新会话。"""
        return await service.create_session(data)

    @router.get("/sessions")
    async def list_sessions(user_id: str = "", limit: int = 50) -> dict[str, Any]:
        """获取用户会话列表。"""
        return await service.list_sessions(user_id=user_id, limit=limit)

    @router.get("/sessions/{session_id}")
    async def get_session(session_id: str, user_id: str = "") -> dict[str, Any]:
        """获取会话详情。"""
        return await service.get_session(session_id=session_id, user_id=user_id)

    @router.put("/sessions/{session_id}")
    async def update_session(session_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """更新会话标题。"""
        return await service.update_session(session_id=session_id, data=data)

    @router.delete("/sessions/{session_id}")
    async def delete_session(session_id: str, user_id: str = "") -> dict[str, Any]:
        """删除会话。"""
        return await service.delete_session(session_id=session_id, user_id=user_id)

    @router.post("/sessions/{session_id}/messages")
    async def add_message(session_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """添加消息到会话。"""
        return await service.add_message(session_id=session_id, data=data)

    @router.get("/sessions/{session_id}/messages")
    async def get_messages(session_id: str, user_id: str = "") -> dict[str, Any]:
        """获取会话消息。"""
        return await service.get_messages(session_id=session_id, user_id=user_id)

    @router.post("/sessions/{session_id}/rollback-last-question")
    async def rollback_last_question(session_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """回撤最后一个用户问题。"""
        return await service.rollback_last_question(session_id=session_id, data=data)

    @router.post("/sessions/{session_id}/rollback-to-question")
    async def rollback_to_question(session_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """回撤到指定用户问题。"""
        return await service.rollback_to_question(session_id=session_id, data=data)

    @router.post("/upload_image")
    @router.post("/upload_file")
    async def upload_image(file: UploadFile = File(...)) -> dict[str, Any]:
        """上传聊天文件并返回 URL。"""
        return await service.upload_file(file)

    @router.post("/qa/jobs/{job_id}/cancel")
    async def cancel_qa_job(job_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """兼容接口：取消 A2A 任务。"""
        return await service.cancel_qa_job(job_id=job_id, data=data)

    return router
