"""A2A 事件流服务。"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from typing import Any, AsyncIterator, Dict, List, Optional

from app.a2a import A2AService


def safe_float(value: Any, default: float = 0.0) -> float:
    """安全转换为浮点数。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def extract_text_from_parts(parts_obj: Any) -> str:
    """从消息 parts 中提取文本。"""
    parts = parts_obj if isinstance(parts_obj, list) else []
    chunks: List[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        if str(part.get("kind") or "").strip().lower() != "text":
            continue
        text = str(part.get("text") or "").strip()
        if text:
            chunks.append(text)
    return "\n".join(chunks).strip()


def extract_a2a_artifact(task: Dict[str, Any], artifact_name: str) -> Any:
    """从 A2A 任务中提取指定产物。"""
    artifacts_obj = task.get("artifacts")
    artifacts = artifacts_obj if isinstance(artifacts_obj, list) else []
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        if str(artifact.get("name") or "").strip() != artifact_name:
            continue
        parts_obj = artifact.get("parts")
        parts = parts_obj if isinstance(parts_obj, list) else []
        for part in parts:
            if not isinstance(part, dict):
                continue
            kind = str(part.get("kind") or "").strip().lower()
            if kind == "data":
                return part.get("data")
            if kind == "text":
                return str(part.get("text") or "")
    return None


def extract_a2a_status_message(task: Dict[str, Any]) -> Dict[str, Any]:
    """提取任务状态消息对象。"""
    status_obj = task.get("status")
    status = status_obj if isinstance(status_obj, dict) else {}
    message_obj = status.get("message")
    message = message_obj if isinstance(message_obj, dict) else {}
    return message


def build_processing_steps_from_progress_events(events_obj: Any) -> List[str]:
    """从进度事件生成处理步骤文本。"""
    events = events_obj if isinstance(events_obj, list) else []
    rows: List[str] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        detail = str(event.get("detail") or event.get("event") or "").strip()
        if detail:
            rows.append(detail)
    return rows


def map_a2a_state_to_done_status(state: str) -> str:
    """将 A2A 状态映射为前端完成状态。"""
    safe_state = str(state or "").strip().lower()
    if safe_state == "completed":
        return "done"
    if safe_state == "canceled":
        return "cancelled"
    if safe_state == "failed":
        return "failed"
    return safe_state or "failed"


def extract_progress_detail(event: Dict[str, Any]) -> str:
    """提取进度事件展示文案。"""
    if not isinstance(event, dict):
        return ""
    return str(event.get("detail") or event.get("event") or "").strip()


def extract_task_final_text(task: Dict[str, Any], state: str) -> str:
    """提取任务终态展示文本。"""
    if not isinstance(task, dict):
        return str(state or "")
    if state == "completed":
        final_answer_obj = extract_a2a_artifact(task, "final_answer")
        final_answer = str(final_answer_obj or "").strip()
        if final_answer:
            return final_answer

    status_message = extract_a2a_status_message(task)
    status_text = extract_text_from_parts(status_message.get("parts"))
    if status_text and status_text not in {"task_submitted", "task_running", "task_completed"}:
        return status_text

    metadata_obj = status_message.get("metadata")
    metadata = metadata_obj if isinstance(metadata_obj, dict) else {}
    fallback = str(metadata.get("error") or metadata.get("error_type") or state or "").strip()
    return fallback or "任务结束"


class A2AEventStreamService:
    """封装 A2A 流式事件生成。"""

    def __init__(
        self,
        get_a2a: Any,
        stream_events_factory: Optional[Any] = None,
    ) -> None:
        self._get_a2a = get_a2a
        self._stream_events_factory = stream_events_factory

    async def generate_data_lines(
        self,
        input_text: str,
        user_id: str,
        session_id: str = "",
        request_id: str = "",
    ) -> AsyncIterator[str]:
        """将 A2A 事件转为逐行 JSON 流输出。"""
        async for msg in self.iter_a2a_stream_events(
            input_text=input_text,
            user_id=user_id,
            session_id=session_id,
            request_id=request_id,
        ):
            yield json.dumps(msg, ensure_ascii=False) + "\n"

    async def iter_a2a_stream_events(self, **kwargs: Any) -> AsyncIterator[Dict[str, Any]]:
        """统一迭代 A2A 流事件。"""
        if self._stream_events_factory is None:
            async for event in self.generate_a2a_stream_events(**kwargs):
                yield event
            return

        async for event in self._stream_events_factory(**kwargs):
            yield event

    async def generate_a2a_stream_events(
        self,
        input_text: str,
        user_id: str,
        session_id: str = "",
        request_id: str = "",
        strategy: str = "iterative",
        max_turns: Optional[int] = None,
        original_input_text: str = "",
        resolved_query: Optional[Dict[str, Any]] = None,
        memory_context: Optional[Dict[str, Any]] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """生成 Agent 流式输出事件。"""
        req_id = str(request_id or uuid.uuid4())
        service: A2AService = self._get_a2a()

        safe_original_input_text = str(original_input_text or input_text or "")
        safe_resolved_query = resolved_query if isinstance(resolved_query, dict) else {}
        safe_memory_context = memory_context if isinstance(memory_context, dict) else {}

        metadata: Dict[str, Any] = {
            "session_id": str(session_id or ""),
            "user_id": str(user_id or ""),
            "strategy": str(strategy or "iterative"),
            "original_input_text": safe_original_input_text,
            "resolved_query": safe_resolved_query,
            "memory_context": safe_memory_context,
            "source": "stream_api",
        }
        if isinstance(max_turns, int) and max_turns > 0:
            metadata["max_turns"] = max_turns

        message = {
            "role": "user",
            "parts": [{"kind": "text", "text": str(input_text or "")}],
            "metadata": metadata,
            "contextId": str(session_id or ""),
            "userId": str(user_id or ""),
        }
        task = await service.asend_message(message, req_id)
        task_id = str((task or {}).get("id") or "").strip()
        if not task_id:
            raise RuntimeError("a2a_task_id_missing")

        yield {"type": "job", "job_id": task_id, "request_id": req_id, "task_id": task_id}

        safe_poll_ms = max(
            100,
            min(int(safe_float(os.getenv("AGENT_RUN_A2A_POLL_MS", "250"), default=250.0)), 3000),
        )
        traces: List[Dict[str, Any]] = []
        cursor = 0

        while True:
            snapshot = await service.aget_task_updates(task_id, cursor)
            if snapshot is None:
                yield {
                    "type": "done",
                    "msg": "任务不存在或已被清理",
                    "status": "failed",
                    "traces": traces,
                    "task_id": task_id,
                }
                break

            events_obj = snapshot.get("events")
            events = events_obj if isinstance(events_obj, list) else []
            cursor = int(snapshot.get("next_cursor") or cursor)

            for event in events:
                detail = extract_progress_detail(event if isinstance(event, dict) else {})
                if not detail:
                    continue
                traces.append({"step": len(traces) + 1, "action": detail})
                yield {"type": "msg", "msg": detail, "task_id": task_id}

            state = str(snapshot.get("state") or "").strip().lower()
            if bool(snapshot.get("terminal")):
                task_obj = snapshot.get("task")
                task_payload = task_obj if isinstance(task_obj, dict) else {}
                final_text = extract_task_final_text(task_payload, state)
                processing_steps_obj = extract_a2a_artifact(task_payload, "processing_steps")
                processing_steps = processing_steps_obj if isinstance(processing_steps_obj, list) else []
                tool_observations_obj = extract_a2a_artifact(task_payload, "tool_observations")
                tool_observations = tool_observations_obj if isinstance(tool_observations_obj, list) else []
                evidence_summary_obj = extract_a2a_artifact(task_payload, "evidence_summary")
                evidence_summary = evidence_summary_obj if isinstance(evidence_summary_obj, dict) else {}
                if state == "completed" and final_text:
                    yield {"type": "reply", "msg": final_text, "task_id": task_id}
                yield {
                    "type": "done",
                    "msg": final_text,
                    "status": map_a2a_state_to_done_status(state),
                    "traces": traces,
                    "task_id": task_id,
                    "processing_steps": processing_steps,
                    "tool_observations": tool_observations,
                    "evidence_summary": evidence_summary,
                }
                break

            await asyncio.sleep(float(safe_poll_ms) / 1000.0)
