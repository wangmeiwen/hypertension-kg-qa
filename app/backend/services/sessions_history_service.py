"""会话历史与上传服务。"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Awaitable, Callable
import uuid

from fastapi import UploadFile
import httpx

from app.backend.repositories.session_manager import MessageManager, SessionManager
from app.backend.security.upload_policy import inspect_upload
from app.core.config import Config

CHAT_FILE_API_EXTENSIONS = {
    ".txt",
    ".docx",
    ".pdf",
    ".xlsx",
    ".epub",
    ".mobi",
    ".md",
    ".csv",
    ".json",
    ".bmp",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
}
CHAT_IMAGE_EXTENSIONS = {".bmp", ".png", ".jpg", ".jpeg", ".gif"}
ALIYUN_FILE_API_MAX_BYTES = 150 * 1024 * 1024


class SessionsHistoryService:
    """封装会话历史、上传和取消任务能力。"""

    def __init__(
        self,
        await_call: Callable[..., Awaitable[Any]],
        is_session_owner: Callable[[str, str], Awaitable[bool]],
        get_a2a: Callable[[], Any],
        get_session_memory_service: Callable[[], Any],
        record_user_upload: Callable[..., Awaitable[None]],
        upload_dir: Path,
        log_api_error: Callable[..., None],
    ) -> None:
        """初始化实例并注入运行依赖。

        Args:
            await_call: 兼容同步/异步仓储和文件操作的执行器。
            is_session_owner: 校验会话归属的回调。
            get_a2a: 延迟获取 A2A 服务实例的回调。
            get_session_memory_service: 延迟获取会话记忆服务的回调。
            record_user_upload: 持久化上传资源归属的回调。
            upload_dir: 聊天上传文件保存目录。
            log_api_error: 统一记录会话历史接口错误的回调。

        Returns:
            None: 无返回值。
        """
        self._await_call = await_call
        self._is_session_owner = is_session_owner
        self._get_a2a = get_a2a
        self._get_session_memory_service = get_session_memory_service
        self._record_user_upload = record_user_upload
        self._upload_dir = upload_dir
        self._log_api_error = log_api_error

    @staticmethod
    def _strip_runtime_message_fields(
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """移除普通聊天历史不需要返回给浏览器的大字段。

        Args:
            messages: 仓储返回的消息列表。

        Returns:
            list[dict[str, Any]]: 去掉运行时思考内容后的消息列表。
        """
        return [
            {key: value for key, value in message.items() if key != "think_content"}
            for message in messages
        ]

    async def _refresh_memory_after_rollback(
        self, session_id: str, user_id: str, operation: str
    ) -> dict[str, Any]:
        """回撤消息后按剩余历史重建会话记忆。

        Args:
            session_id: 会话标识。
            user_id: 用户标识。
            operation: 当前回撤操作名称，用于错误日志分类。

        Returns:
            dict[str, Any]: 记忆清理、重建状态和可选错误类型。
        """
        result: dict[str, Any] = {
            "memory_cleared": False,
            "memory_rebuilt": False,
        }
        try:
            memory_service = self._get_session_memory_service()
            await self._await_call(memory_service.clear_memory, session_id)
            result["memory_cleared"] = True
            await self._await_call(memory_service.rebuild_from_messages, session_id)
            result["memory_rebuilt"] = True
        except Exception as exc:
            result["memory_error"] = type(exc).__name__
            self._log_api_error(
                "/sessions/rollback",
                operation,
                exc,
                session_id=session_id,
                user_id=user_id,
            )
        return result

    async def create_session(self, data: dict[str, Any]) -> dict[str, Any]:
        """创建新会话。

        Args:
            data: 包含 user_id 和 title 的会话创建 payload。

        Returns:
            dict[str, Any]: 新建会话数据。
        """
        user_id = data.get("user_id") or str(uuid.uuid4())
        title = data.get("title", "新会话")
        session = await self._await_call(SessionManager.create_session, user_id, title)
        return {"ok": True, "data": session}

    async def list_sessions(self, user_id: str = "", limit: int = 50) -> dict[str, Any]:
        """获取用户会话列表。

        Args:
            user_id: 用户标识。
            limit: 返回数量上限。

        Returns:
            dict[str, Any]: 用户会话列表或缺少用户标识错误。
        """
        if not user_id:
            return {"ok": False, "error": "user_id_required", "data": []}
        sessions = await self._await_call(SessionManager.get_sessions, user_id, limit)
        return {"ok": True, "data": sessions}

    async def get_session(self, session_id: str, user_id: str = "") -> dict[str, Any]:
        """获取会话详情和消息列表。

        Args:
            session_id: 会话标识。
            user_id: 用户标识。

        Returns:
            dict[str, Any]: 会话详情、消息列表或归属校验错误。
        """
        if not user_id:
            return {"ok": False, "error": "user_id_required"}

        session = await self._await_call(SessionManager.get_session, session_id)
        if not session or str(session.get("user_id") or "") != user_id:
            return {"ok": False, "error": "session_not_found"}

        messages = self._strip_runtime_message_fields(
            await self._await_call(MessageManager.get_messages, session_id)
        )
        return {"ok": True, "data": {**session, "messages": messages}}

    async def update_session(
        self, session_id: str, data: dict[str, Any]
    ) -> dict[str, Any]:
        """更新会话标题。

        Args:
            session_id: 会话标识。
            data: 包含 user_id 和 title 的更新 payload。

        Returns:
            dict[str, Any]: 更新结果或校验错误。
        """
        title = data.get("title")
        user_id = str(data.get("user_id") or "").strip()
        if not user_id:
            return {"ok": False, "error": "user_id_required"}
        if not title:
            return {"ok": False, "error": "title_required"}

        if not await self._is_session_owner(session_id, user_id):
            return {"ok": False, "error": "session_not_found"}

        success = await self._await_call(
            SessionManager.update_session_title, session_id, title
        )
        if not success:
            return {"ok": False, "error": "session_not_found"}
        return {"ok": True}

    async def delete_session(
        self, session_id: str, user_id: str = ""
    ) -> dict[str, Any]:
        """删除用户自己的会话。

        Args:
            session_id: 会话标识。
            user_id: 用户标识。

        Returns:
            dict[str, Any]: 删除结果或归属校验错误。
        """
        if not user_id:
            return {"ok": False, "error": "user_id_required"}

        if not await self._is_session_owner(session_id, user_id):
            return {"ok": False, "error": "session_not_found"}

        success = await self._await_call(SessionManager.delete_session, session_id)
        if not success:
            return {"ok": False, "error": "session_not_found"}
        return {"ok": True}

    async def add_message(
        self, session_id: str, data: dict[str, Any]
    ) -> dict[str, Any]:
        """添加消息到用户会话。

        Args:
            session_id: 会话标识。
            data: 包含 user_id、role、content 和可选 think_content 的消息 payload。

        Returns:
            dict[str, Any]: 新增消息数据或校验错误。
        """
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

        message = await self._await_call(
            MessageManager.add_message, session_id, role, content, think_content
        )
        return {"ok": True, "data": message}

    async def get_messages(self, session_id: str, user_id: str = "") -> dict[str, Any]:
        """获取用户会话消息列表。

        Args:
            session_id: 会话标识。
            user_id: 用户标识。

        Returns:
            dict[str, Any]: 消息列表或归属校验错误。
        """
        if not user_id:
            return {"ok": False, "error": "user_id_required"}

        session = await self._await_call(SessionManager.get_session, session_id)
        if not session or str(session.get("user_id") or "") != user_id:
            return {"ok": False, "error": "session_not_found"}

        messages = self._strip_runtime_message_fields(
            await self._await_call(MessageManager.get_messages, session_id)
        )
        return {"ok": True, "data": messages}

    async def rollback_to_question(
        self, session_id: str, data: dict[str, Any]
    ) -> dict[str, Any]:
        """回撤会话到指定用户问题，并重建会话记忆。

        Args:
            session_id: 会话标识。
            data: 包含 user_id 和 message_id 的回撤 payload。

        Returns:
            dict[str, Any]: 回撤结果、删除数量、原问题和记忆重建状态。
        """
        user_id = str(data.get("user_id") or "").strip()
        if not user_id:
            return {"ok": False, "error": "user_id_required"}

        raw_message_id = data.get("message_id")
        if not isinstance(raw_message_id, int) or raw_message_id <= 0:
            return {"ok": False, "error": "message_id_required"}

        session = await self._await_call(SessionManager.get_session, session_id)
        if not session or str(session.get("user_id") or "") != user_id:
            return {"ok": False, "error": "session_not_found"}

        rollback_result = await self._await_call(
            MessageManager.rollback_to_question, session_id, raw_message_id
        )
        if not bool(rollback_result.get("ok")):
            return {"ok": False, "error": "question_not_found"}

        memory_result = await self._refresh_memory_after_rollback(
            session_id, user_id, "rollback_to_question_memory"
        )
        return {
            "ok": True,
            "data": {
                "removed_count": int(rollback_result.get("removed_count") or 0),
                "question": str(rollback_result.get("question") or ""),
                **memory_result,
            },
        }

    async def upload_file(self, file: UploadFile, *, user_id: str) -> dict[str, Any]:
        """上传聊天文件到阿里 File API，并返回前端和模型可用的文件信息。

        Args:
            file: FastAPI 上传文件对象。
            user_id: 服务端认证得到的资源所有者 ID。

        Returns:
            dict[str, Any]: 本地备份信息、阿里 file_id、原始名和可选图片 base64。
        """
        if not file.filename:
            return {"ok": False, "error": "文件名不能为空"}
        if not user_id:
            return {"ok": False, "error": "user_id_required"}

        file_ext = Path(file.filename).suffix.lower()

        if file_ext not in CHAT_FILE_API_EXTENSIONS:
            return {"ok": False, "error": f"不支持的文件格式: {file_ext}"}

        try:
            content = await file.read()
            inspect_upload(
                content=content,
                filename=str(file.filename),
                declared_mime=file.content_type,
                allowed_suffixes=CHAT_FILE_API_EXTENSIONS,
                max_bytes=ALIYUN_FILE_API_MAX_BYTES,
            )

            aliyun_file = await self._upload_to_aliyun_file_api(
                str(file.filename), content
            )
            aliyun_file_id = str(
                aliyun_file.get("id") or aliyun_file.get("file_id") or ""
            ).strip()
            if not aliyun_file_id:
                return {"ok": False, "error": "阿里 File API 未返回 file_id"}

            local_file_id = str(uuid.uuid4())
            filename = f"{local_file_id}{file_ext}"
            file_path = self._upload_dir / filename
            await self._await_call(file_path.write_bytes, content)
            await self._record_user_upload(
                upload_id=local_file_id,
                user_id=user_id,
                storage_filename=filename,
                original_filename=str(file.filename),
                provider="dashscope",
                provider_file_id=aliyun_file_id,
            )

            base64_data = ""
            mime_type = "application/octet-stream"
            if file_ext in CHAT_IMAGE_EXTENSIONS:
                base64_data = base64.b64encode(content).decode("utf-8")
                mime_type = f"image/{file_ext[1:]}"
                if file_ext == ".jpg":
                    mime_type = "image/jpeg"

            return {
                "ok": True,
                "data": {
                    "filename": filename,
                    "upload_id": local_file_id,
                    "owner_user_id": user_id,
                    "original_name": str(file.filename),
                    "url": f"/uploads/{filename}",
                    "base64": (
                        f"data:{mime_type};base64,{base64_data}" if base64_data else ""
                    ),
                    "is_image": file_ext in CHAT_IMAGE_EXTENSIONS,
                    "file_id": aliyun_file_id,
                    "aliyun_file_id": aliyun_file_id,
                    "provider": "dashscope",
                    "purpose": str(aliyun_file.get("purpose") or "file-extract"),
                },
            }
        except (
            RuntimeError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            OSError,
            ImportError,
            httpx.HTTPError,
        ) as exc:
            self._log_api_error(
                "/upload_file", "aliyun_file_upload", exc, filename=file.filename
            )
            return {"ok": False, "error": f"上传失败: {str(exc)}"}

    async def _upload_to_aliyun_file_api(
        self, filename: str, content: bytes
    ) -> dict[str, Any]:
        """调用阿里 OpenAI 兼容 File API 上传聊天附件。"""
        config = Config()
        api_key = str(config.MODEL_API_KEY or "").strip()
        base_url = str(config.MODEL_BASE_URL or "").strip().rstrip("/")
        if not api_key:
            raise RuntimeError("MODEL_API_KEY 未配置")
        if not base_url:
            raise RuntimeError("MODEL_BASE_URL 未配置")

        url = f"{base_url}/files"
        timeout = httpx.Timeout(float(config.LLM_REQUEST_TIMEOUT_SECONDS), connect=30.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {api_key}"},
                data={"purpose": "file-extract"},
                files={"file": (filename, content)},
            )
        if response.status_code >= 400:
            raise RuntimeError(
                f"阿里 File API 上传失败: status={response.status_code} body={response.text[:300]}"
            )
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    async def cancel_qa_job(self, job_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """兼容旧接口，校验归属后取消 A2A 任务。

        Args:
            job_id: A2A 任务标识。
            data: 包含 user_id 和 session_id 的取消 payload。

        Returns:
            dict[str, Any]: 取消请求结果或任务归属校验错误。
        """
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
        if (
            not bool(result.get("ok"))
            and str(result.get("error") or "") == "task_not_found"
        ):
            return {"ok": False, "error": "job_not_found_or_finished"}
        current_status = str(
            (((result.get("task") or {}).get("status") or {}).get("state")) or ""
        )

        return {
            "ok": True,
            "message": "cancel_requested",
            "job_id": job_id,
            "status": current_status,
            "memory_cleared": False,
        }
