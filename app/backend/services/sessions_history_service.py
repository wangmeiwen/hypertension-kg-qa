"""会话历史与上传服务。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Awaitable, Callable
import uuid

from fastapi import UploadFile

from app.backend.repositories.database import MessageManager, SessionManager, SessionMemoryManager


class SessionsHistoryService:
    """封装会话历史、上传和取消任务能力。"""

    def __init__(
        self,
        await_call: Callable[..., Awaitable[Any]],
        is_session_owner: Callable[[str, str], Awaitable[bool]],
        get_a2a: Callable[[], Any],
        upload_dir: Path,
        log_api_error: Callable[..., None],
    ) -> None:
        self._await_call = await_call
        self._is_session_owner = is_session_owner
        self._get_a2a = get_a2a
        self._upload_dir = upload_dir
        self._log_api_error = log_api_error

    async def create_session(self, data: dict[str, Any]) -> dict[str, Any]:
        """创建新会话。"""
        user_id = data.get("user_id") or str(uuid.uuid4())
        title = data.get("title", "新会话")
        session = await self._await_call(SessionManager.create_session, user_id, title)
        return {"ok": True, "data": session}

    async def list_sessions(self, user_id: str = "", limit: int = 50) -> dict[str, Any]:
        """获取用户会话列表。"""
        if not user_id:
            return {"ok": False, "error": "user_id_required", "data": []}
        sessions = await self._await_call(SessionManager.get_sessions, user_id, limit)
        return {"ok": True, "data": sessions}

    async def get_session(self, session_id: str, user_id: str = "") -> dict[str, Any]:
        """获取会话详情。"""
        if not user_id:
            return {"ok": False, "error": "user_id_required"}

        session = await self._await_call(SessionManager.get_session, session_id)
        if not session or str(session.get("user_id") or "") != user_id:
            return {"ok": False, "error": "session_not_found"}

        messages = await self._await_call(MessageManager.get_messages, session_id)
        return {"ok": True, "data": {**session, "messages": messages}}

    async def update_session(self, session_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """更新会话标题。"""
        title = data.get("title")
        user_id = str(data.get("user_id") or "").strip()
        if not user_id:
            return {"ok": False, "error": "user_id_required"}
        if not title:
            return {"ok": False, "error": "title_required"}

        if not await self._is_session_owner(session_id, user_id):
            return {"ok": False, "error": "session_not_found"}

        success = await self._await_call(SessionManager.update_session_title, session_id, title)
        if not success:
            return {"ok": False, "error": "session_not_found"}
        return {"ok": True}

    async def delete_session(self, session_id: str, user_id: str = "") -> dict[str, Any]:
        """删除会话。"""
        if not user_id:
            return {"ok": False, "error": "user_id_required"}

        if not await self._is_session_owner(session_id, user_id):
            return {"ok": False, "error": "session_not_found"}

        success = await self._await_call(SessionManager.delete_session, session_id)
        if not success:
            return {"ok": False, "error": "session_not_found"}
        return {"ok": True}

    async def add_message(self, session_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """添加消息到会话。"""
        user_id = str(data.get("user_id") or "").strip()
        role = data.get("role")
        content = data.get("content")
        think_content = data.get("think_content", "")

        if not user_id:
            return {"ok": False, "error": "user_id_required"}
        if not role or not content:
            return {"ok": False, "error": "role_and_content_required"}

        session = await self._await_call(SessionManager.get_session, session_id)
        if not session or str(session.get("user_id") or "") != user_id:
            return {"ok": False, "error": "session_not_found"}

        message = await self._await_call(MessageManager.add_message, session_id, role, content, think_content)
        return {"ok": True, "data": message}

    async def get_messages(self, session_id: str, user_id: str = "") -> dict[str, Any]:
        """获取会话消息。"""
        if not user_id:
            return {"ok": False, "error": "user_id_required"}

        session = await self._await_call(SessionManager.get_session, session_id)
        if not session or str(session.get("user_id") or "") != user_id:
            return {"ok": False, "error": "session_not_found"}

        messages = await self._await_call(MessageManager.get_messages, session_id)
        return {"ok": True, "data": messages}

    async def rollback_last_question(self, session_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """回撤最后一个用户问题。"""
        user_id = str(data.get("user_id") or "").strip()
        if not user_id:
            return {"ok": False, "error": "user_id_required"}

        session = await self._await_call(SessionManager.get_session, session_id)
        if not session or str(session.get("user_id") or "") != user_id:
            return {"ok": False, "error": "session_not_found"}

        rollback_result = await self._await_call(MessageManager.rollback_last_question, session_id)
        if not bool(rollback_result.get("ok")):
            return {"ok": False, "error": "no_question_to_rollback"}

        await self._await_call(SessionMemoryManager.clear_summary, session_id)
        return {
            "ok": True,
            "data": {
                "removed_count": int(rollback_result.get("removed_count") or 0),
                "question": str(rollback_result.get("question") or ""),
                "memory_cleared": True,
            },
        }

    async def rollback_to_question(self, session_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """回撤到指定用户问题。"""
        user_id = str(data.get("user_id") or "").strip()
        if not user_id:
            return {"ok": False, "error": "user_id_required"}

        raw_message_id = data.get("message_id")
        if not isinstance(raw_message_id, int) or raw_message_id <= 0:
            return {"ok": False, "error": "message_id_required"}

        session = await self._await_call(SessionManager.get_session, session_id)
        if not session or str(session.get("user_id") or "") != user_id:
            return {"ok": False, "error": "session_not_found"}

        rollback_result = await self._await_call(MessageManager.rollback_to_question, session_id, raw_message_id)
        if not bool(rollback_result.get("ok")):
            return {"ok": False, "error": "question_not_found"}

        await self._await_call(SessionMemoryManager.clear_summary, session_id)
        return {
            "ok": True,
            "data": {
                "removed_count": int(rollback_result.get("removed_count") or 0),
                "question": str(rollback_result.get("question") or ""),
                "memory_cleared": True,
            },
        }

    async def upload_file(self, file: UploadFile) -> dict[str, Any]:
        """上传聊天文件并返回 URL。"""
        if not file.filename:
            return {"ok": False, "error": "文件名不能为空"}

        image_extensions = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
        doc_extensions = {".md", ".doc", ".docx", ".pdf", ".ppt", ".txt"}
        allowed_extensions = image_extensions | doc_extensions
        file_ext = Path(file.filename).suffix.lower()

        if file_ext not in allowed_extensions:
            return {"ok": False, "error": f"不支持的图片格式: {file_ext}"}

        try:
            import base64

            content = await file.read()
            file_id = str(uuid.uuid4())
            filename = f"{file_id}{file_ext}"
            file_path = self._upload_dir / filename
            await self._await_call(file_path.write_bytes, content)

            base64_data = ""
            mime_type = "application/octet-stream"
            if file_ext in image_extensions:
                base64_data = base64.b64encode(content).decode("utf-8")
                mime_type = f"image/{file_ext[1:]}"
                if file_ext == ".jpg":
                    mime_type = "image/jpeg"

            return {
                "ok": True,
                "data": {
                    "filename": filename,
                    "original_name": str(file.filename),
                    "url": f"/uploads/{filename}",
                    "base64": f"data:{mime_type};base64,{base64_data}" if base64_data else "",
                    "is_image": file_ext in image_extensions,
                },
            }
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            self._log_api_error("/upload_image", "write_file", exc, filename=file.filename)
            return {"ok": False, "error": f"上传失败: {str(exc)}"}

    async def cancel_qa_job(self, job_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """兼容接口：取消 A2A 任务。"""
        user_id = str(data.get("user_id") or "").strip()
        session_id = str(data.get("session_id") or "").strip()
        if not user_id or not session_id:
            return {"ok": False, "error": "user_id_and_session_id_required"}

        service = self._get_a2a()
        task = await service.aget_task(job_id)
        if not task:
            return {"ok": False, "error": "job_not_found"}

        metadata_obj = task.get("metadata")
        metadata = metadata_obj if isinstance(metadata_obj, dict) else {}
        payload_user_id = str(metadata.get("user_id") or "")
        payload_session_id = str(task.get("contextId") or "")
        if payload_user_id != user_id or payload_session_id != session_id:
            return {"ok": False, "error": "job_not_found"}

        result = await service.acancel_task(job_id, "manual_cancel")
        if not bool(result.get("ok")) and str(result.get("error") or "") == "task_not_found":
            return {"ok": False, "error": "job_not_found_or_finished"}
        current_status = str((((result.get("task") or {}).get("status") or {}).get("state")) or "")

        return {
            "ok": True,
            "message": "cancel_requested",
            "job_id": job_id,
            "status": current_status,
            "memory_cleared": False,
        }
