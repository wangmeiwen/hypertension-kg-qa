"""会话轮次准备服务。"""

from __future__ import annotations

import uuid
from typing import Any, Dict

from app.backend.repositories.session_manager import MessageManager, SessionManager
from app.backend.services.session_memory_service import SessionMemoryService
from app.core.langsmith_tracing import set_trace_metadata


class SessionTurnService:
    """封装会话校验、消息落库与 runtime context 构建。"""

    DEFAULT_SESSION_TITLE = "新会话"

    def __init__(
        self,
        await_call: Any,
        get_session_memory_service: Any,
        log_api_error: Any,
        message_manager: Any = MessageManager,
        session_manager: Any = SessionManager,
    ) -> None:
        """初始化实例并注入运行依赖。

        Args:
            await_call: 兼容同步/异步仓储调用的执行器。
            get_session_memory_service: 延迟获取会话记忆服务的回调。
            log_api_error: 统一记录 API 错误的回调。
            message_manager: 消息仓储实现，默认使用数据库 MessageManager。
            session_manager: 会话仓储实现，默认使用数据库 SessionManager。

        Returns:
            None: 无返回值。
        """
        self._await_call = await_call
        self._get_session_memory_service = get_session_memory_service
        self._log_api_error = log_api_error
        self._message_manager = message_manager
        self._session_manager = session_manager

    @staticmethod
    def bind_process_session_trace_metadata(
        trace_id: str,
        session_id: str,
        user_id: str,
    ) -> None:
        """写入 /process_with_session 追踪元数据。"""
        set_trace_metadata(
            {
                "trace_id": trace_id,
                "request_id": trace_id,
                "endpoint": "/process_with_session",
                "session_id": str(session_id or ""),
                "user_id": str(user_id or ""),
            },
            tags=["api", "process_with_session"],
        )

    @classmethod
    def _build_session_title(cls, input_text: str) -> str:
        """基于用户首问生成会话标题。"""
        text = str(input_text or "").strip()
        if not text:
            return cls.DEFAULT_SESSION_TITLE
        return text[:30] + "..." if len(text) > 30 else text

    async def prepare_session_turn(
        self,
        input_text: str,
        session_id: str,
        user_id: str,
        image_data: Any = None,
        file_name: str = "",
        file_id: str = "",
    ) -> Dict[str, Any]:
        """准备会话轮次上下文，含会话校验、消息落库和记忆上下文构建。

        Args:
            input_text: 用户本轮输入文本。
            session_id: 传入的会话标识，为空时会创建新会话。
            user_id: 用户标识，参与会话归属校验。
            image_data: 可选图片数据，用于标记消息内容来源。
            file_name: 可选上传文件名，用于标记消息内容来源。
            file_id: 可选阿里 File API 文件 ID，用于标记文件已可被模型读取。

        Returns:
            Dict[str, Any]: 会话准备结果，失败时返回错误码，成功时返回会话、消息和记忆上下文。
        """
        safe_user_id = str(user_id or "").strip() or str(uuid.uuid4())
        safe_session_id = str(session_id or "").strip()
        first_question_title = self._build_session_title(input_text)

        if not safe_session_id:
            created_session = await self._await_call(
                self._session_manager.create_session,
                safe_user_id,
                first_question_title,
            )
            safe_session_id = str((created_session or {}).get("id") or "")
        else:
            existed = await self._await_call(self._session_manager.get_session, safe_session_id)
            if not existed or str(existed.get("user_id") or "") != safe_user_id:
                return {"ok": False, "error": "session_not_found"}
            existed_title = str(existed.get("title") or "").strip()
            message_count = await self._await_call(self._message_manager.count_messages, safe_session_id)
            if message_count == 0 and existed_title in {"", self.DEFAULT_SESSION_TITLE}:
                await self._await_call(
                    self._session_manager.update_session_title,
                    safe_session_id,
                    first_question_title,
                )

        message_content = input_text
        if image_data:
            message_content = f"[图片] {input_text}"
        elif str(file_name or "").strip():
            file_marker = str(file_name or "").strip()
            safe_file_id = str(file_id or "").strip()
            if safe_file_id:
                file_marker = f"{file_marker}; file_id={safe_file_id}"
            message_content = f"[文件:{file_marker}] {input_text}".strip()

        user_message = await self._await_call(
            self._message_manager.add_message,
            safe_session_id,
            "user",
            message_content,
        )
        user_message_id = int((user_message or {}).get("id") or 0)

        runtime_payload: Dict[str, Any] = {
            "original_input_text": str(input_text or ""),
            "memory_context": {},
        }
        try:
            memory_service: SessionMemoryService = self._get_session_memory_service()
            runtime_payload = await self._await_call(
                memory_service.build_runtime_context,
                safe_session_id,
                str(input_text or ""),
                user_message_id,
            )
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            self._log_api_error(
                "/process_with_session",
                "build_runtime_context",
                exc,
                session_id=safe_session_id,
            )

        return {
            "ok": True,
            "user_id": safe_user_id,
            "session_id": safe_session_id,
            "user_message_id": user_message_id,
            "original_input_text": str(runtime_payload.get("original_input_text") or input_text or ""),
            "memory_context": runtime_payload.get("memory_context") if isinstance(runtime_payload.get("memory_context"), dict) else {},
        }
