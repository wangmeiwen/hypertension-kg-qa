"""会话流式输出服务。"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, Dict, List, Optional

from app.backend.repositories.database import MessageManager
from app.backend.services.a2a_event_stream_service import A2AEventStreamService
from app.backend.services.session_memory_service import SessionMemoryService


class SessionStreamService:
    """封装会话流式输出、assistant 落库与 memory refresh 调度。"""

    def __init__(
        self,
        await_call: Any,
        get_session_memory_service: Any,
        log_api_error: Any,
        event_stream_service: A2AEventStreamService,
        message_manager: Any = MessageManager,
    ) -> None:
        self._await_call = await_call
        self._get_session_memory_service = get_session_memory_service
        self._log_api_error = log_api_error
        self._event_stream_service = event_stream_service
        self._message_manager = message_manager

    async def generate_data_with_session_lines(
        self,
        input_text: str,
        user_id: str,
        session_id: str,
        image_data: Any = None,
        user_message_id: int = 0,
        request_id: str = "",
        original_input_text: str = "",
        resolved_query: Optional[Dict[str, Any]] = None,
        memory_context: Optional[Dict[str, Any]] = None,
    ) -> AsyncIterator[str]:
        """基于 A2A 生成流式响应并保存助手回复。"""
        _ = image_data
        _ = user_message_id
        full_reply = ""
        full_think = ""
        done_status = "done"
        done_processing_steps: List[str] = []
        done_tool_observations: List[Dict[str, Any]] = []
        done_evidence_summary: Dict[str, Any] = {}
        assistant_message_persisted = False
        assistant_message_id = 0
        memory_refresh_scheduled = False
        done_emitted = False

        async def persist_assistant_message_once() -> int:
            nonlocal assistant_message_persisted, assistant_message_id
            if assistant_message_persisted:
                return int(assistant_message_id or 0)
            if not full_reply:
                return 0
            assistant_message = await self._await_call(
                self._message_manager.add_message,
                session_id,
                "assistant",
                full_reply,
                full_think,
            )
            assistant_message_id = int((assistant_message or {}).get("id") or 0)
            assistant_message_persisted = assistant_message_id > 0
            return int(assistant_message_id or 0)

        def schedule_memory_refresh(persisted_assistant_message_id: int) -> None:
            nonlocal memory_refresh_scheduled
            if memory_refresh_scheduled or int(persisted_assistant_message_id or 0) <= 0:
                return

            turn_artifacts = {
                "processing_steps": done_processing_steps,
                "tool_observations": done_tool_observations,
                "evidence_summary": done_evidence_summary,
                "assistant_reply": full_reply,
            }

            async def refresh_memory_task() -> None:
                memory_service: SessionMemoryService = self._get_session_memory_service()
                await self._await_call(
                    memory_service.refresh_after_turn,
                    session_id,
                    int(persisted_assistant_message_id or 0),
                    turn_artifacts,
                )

            def on_refresh_done(task: asyncio.Task) -> None:
                try:
                    task.result()
                except Exception as exc:
                    self._log_api_error(
                        "/process_with_session",
                        "memory_refresh_failed",
                        exc,
                        session_id=session_id,
                        user_id=user_id,
                        assistant_message_id=int(persisted_assistant_message_id or 0),
                        request_id=request_id,
                    )

            try:
                refresh_task = asyncio.create_task(
                    refresh_memory_task(),
                    name=f"memory-refresh-{session_id}-{persisted_assistant_message_id}",
                )
                refresh_task.add_done_callback(on_refresh_done)
                memory_refresh_scheduled = True
            except Exception as exc:
                self._log_api_error(
                    "/process_with_session",
                    "memory_refresh_schedule_failed",
                    exc,
                    session_id=session_id,
                    user_id=user_id,
                    assistant_message_id=int(persisted_assistant_message_id or 0),
                    request_id=request_id,
                )

        try:
            async for msg in self._event_stream_service.iter_a2a_stream_events(
                input_text=input_text,
                user_id=user_id,
                session_id=session_id,
                request_id=request_id,
                original_input_text=original_input_text,
                resolved_query=resolved_query,
                memory_context=memory_context,
            ):
                if msg.get("type") == "reply":
                    full_reply += str(msg.get("msg") or "")
                elif msg.get("type") == "msg":
                    full_think += str(msg.get("msg") or "")
                elif msg.get("type") == "done":
                    done_status = str(msg.get("status") or "done")
                    processing_steps_obj = msg.get("processing_steps")
                    if isinstance(processing_steps_obj, list):
                        done_processing_steps = [str(item or "") for item in processing_steps_obj]
                    tool_observations_obj = msg.get("tool_observations")
                    if isinstance(tool_observations_obj, list):
                        done_tool_observations = [
                            item if isinstance(item, dict) else {"raw": str(item or "")}
                            for item in tool_observations_obj
                        ]
                    evidence_summary_obj = msg.get("evidence_summary")
                    if isinstance(evidence_summary_obj, dict):
                        done_evidence_summary = evidence_summary_obj

                if msg.get("type") == "done" and not full_reply:
                    full_reply = str(msg.get("msg") or "").strip()

                yield json.dumps(msg, ensure_ascii=False) + "\n"

                if msg.get("type") == "done":
                    done_emitted = True
                    if done_status == "done" and full_reply:
                        try:
                            persisted_id = await persist_assistant_message_once()
                            if persisted_id > 0:
                                schedule_memory_refresh(persisted_id)
                        except Exception as exc:
                            self._log_api_error(
                                "/process_with_session",
                                "assistant_persist_failed_after_done",
                                exc,
                                session_id=session_id,
                                user_id=user_id,
                                request_id=request_id,
                                done_emitted=done_emitted,
                            )
                    break
        except asyncio.CancelledError as exc:
            self._log_api_error(
                "/process_with_session",
                "generate_data_with_session_cancelled",
                exc,
                session_id=session_id,
                user_id=user_id,
            )
            raise
        except Exception as exc:
            self._log_api_error(
                "/process_with_session",
                "generate_data_with_session_exception",
                exc,
                session_id=session_id,
                user_id=user_id,
            )
            if not done_emitted:
                yield json.dumps({"type": "done", "status": "error", "msg": "流式输出异常终止"}, ensure_ascii=False) + "\n"
                done_emitted = True
        finally:
            if full_reply and not assistant_message_persisted:
                try:
                    persisted_id = await persist_assistant_message_once()
                    if persisted_id > 0:
                        schedule_memory_refresh(persisted_id)
                except Exception as exc:
                    self._log_api_error(
                        "/process_with_session",
                        "persist_assistant_turn_finally",
                        exc,
                        session_id=session_id,
                        user_id=user_id,
                        request_id=request_id,
                        done_emitted=done_emitted,
                    )
