"""Agent 运行服务。"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Dict, List, Optional

from app.a2a import A2AService
from app.backend.services.a2a_event_stream_service import (
    build_processing_steps_from_progress_events,
    extract_a2a_artifact,
    extract_a2a_status_message,
    extract_text_from_parts,
    safe_float,
)
from app.common.langsmith_tracing import set_trace_metadata
from app.common.session_logger import ensure_session_meta, reset_session_context, set_session_context


class AgentRunService:
    """封装 /agent/run 相关业务。"""

    def __init__(
        self,
        get_a2a: Any,
        log_api_error: Any,
    ) -> None:
        """初始化实例并注入运行依赖。
        
        Args:
            get_a2a: 函数输入参数。
            log_api_error: 函数输入参数。
        
        Returns:
            None: 无返回值。
        """
        self._get_a2a = get_a2a
        self._log_api_error = log_api_error

    @staticmethod
    def resolve_agent_run_mode(payload: Dict[str, Any]) -> str:
        """解析 agent 运行模式。"""
        mode = str(payload.get("a2a_mode") or "").strip().lower()
        if not mode:
            return "auto"
        if mode in {"auto", "force", "prefer"}:
            return mode
        if mode in {"off", "legacy"}:
            raise ValueError("a2a_mode_not_supported")
        raise ValueError("a2a_mode_invalid")

    @staticmethod
    def build_traces_from_processing_steps(steps_obj: Any) -> List[Dict[str, Any]]:
        """将处理步骤转换为 trace 列表。"""
        steps = steps_obj if isinstance(steps_obj, list) else []
        rows: List[Dict[str, Any]] = []
        for idx, step in enumerate(steps, start=1):
            rows.append({"step": idx, "action": str(step or "")})
        return rows

    @staticmethod
    def bind_agent_trace_metadata(
        trace_id: str,
        session_id: str,
        user_id: str,
        strategy: str,
        a2a_mode: str,
    ) -> None:
        """写入 /agent/run 追踪元数据。"""
        set_trace_metadata(
            {
                "trace_id": trace_id,
                "request_id": trace_id,
                "session_id": str(session_id or ""),
                "user_id": str(user_id or ""),
                "endpoint": "/agent/run",
                "strategy": strategy,
                "a2a_mode": a2a_mode,
            },
            tags=["api", "agent_run"],
        )

    async def run_agent(
        self,
        input_text: str,
        session_id: str,
        user_id: str,
        strategy: str,
        max_turns: Optional[int],
        trace_id: str,
        a2a_mode: str,
    ) -> Dict[str, Any]:
        """执行 Agent 请求并输出兼容响应。"""
        if session_id:
            ensure_session_meta(str(session_id), str(user_id or ""))
        session_tokens = set_session_context(str(session_id or ""), str(user_id or ""))

        try:
            result = await self._run_agent_via_a2a(
                input_text=input_text,
                session_id=session_id,
                user_id=user_id,
                strategy=strategy,
                max_turns=max_turns,
                trace_id=trace_id,
            )
            traces = self.build_traces_from_processing_steps(result.get("processing_steps", []))
            response: Dict[str, Any] = {
                "ok": True,
                "answer": result.get("output", ""),
                "output_type": result.get("output_type", ""),
                "strategy": strategy,
                "traces": traces,
                "tool_observations": result.get("tool_observations", []),
                "reflection_events": result.get("reflection_events", []),
                "telemetry": result.get("telemetry", {}),
                "confidence": result.get("confidence", 0.0),
                "trace_id": trace_id,
                "run_path": "a2a",
                "a2a_mode": a2a_mode,
            }
            if str(result.get("a2a_task_id") or "").strip():
                response["a2a_task_id"] = str(result.get("a2a_task_id"))
            return response
        except ValueError as exc:
            self._log_api_error("/agent/run", "validation", exc, strategy=strategy)
            return {"ok": False, "error": f"invalid_request:{str(exc)}", "trace_id": trace_id}
        except RuntimeError as exc:
            self._log_api_error("/agent/run", "runtime", exc, strategy=strategy)
            return {"ok": False, "error": f"agent_runtime_error:{str(exc)}", "trace_id": trace_id}
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            self._log_api_error("/agent/run", "unknown", exc, strategy=strategy)
            return {"ok": False, "error": f"agent_unknown_error:{str(exc)}", "trace_id": trace_id}
        finally:
            reset_session_context(session_tokens)

    async def _run_agent_via_a2a(
        self,
        input_text: str,
        session_id: str,
        user_id: str,
        strategy: str,
        max_turns: Optional[int],
        trace_id: str,
    ) -> Dict[str, Any]:
        """通过 A2A 服务执行一次 Agent 调用。"""
        service: A2AService = self._get_a2a()
        metadata: Dict[str, Any] = {
            "session_id": str(session_id or ""),
            "user_id": str(user_id or ""),
            "strategy": str(strategy or "iterative"),
            "trace_id": str(trace_id or ""),
            "source": "agent_run",
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
        task = await service.asend_message(message, trace_id)
        task_id = str((task or {}).get("id") or "").strip()
        if not task_id:
            raise RuntimeError("a2a_task_id_missing")

        timeout_seconds = max(
            3.0,
            min(
                safe_float(os.getenv("AGENT_RUN_A2A_TIMEOUT_SECONDS", "180"), default=180.0),
                900.0,
            ),
        )
        poll_ms = max(
            100,
            min(
                int(safe_float(os.getenv("AGENT_RUN_A2A_POLL_MS", "250"), default=250.0)),
                3000,
            ),
        )
        started = time.time()

        while True:
            snapshot = await service.aget_task(task_id)
            if not snapshot:
                raise RuntimeError("a2a_task_not_found")
            status_obj = snapshot.get("status")
            status = status_obj if isinstance(status_obj, dict) else {}
            state = str(status.get("state") or "").strip().lower()
            if state in {"completed", "failed", "canceled"}:
                break
            if time.time() - started >= timeout_seconds:
                raise TimeoutError(f"a2a_task_timeout:{task_id}")
            await asyncio.sleep(float(poll_ms) / 1000.0)

        if state != "completed":
            message_obj = extract_a2a_status_message(snapshot)
            status_text = extract_text_from_parts(message_obj.get("parts"))
            metadata_obj = message_obj.get("metadata")
            status_meta = metadata_obj if isinstance(metadata_obj, dict) else {}
            if not status_text:
                status_text = str(status_meta.get("error") or status_meta.get("error_type") or state)
            raise RuntimeError(f"a2a_task_{state}:{status_text}")

        final_answer_obj = extract_a2a_artifact(snapshot, "final_answer")
        final_answer = str(final_answer_obj or "").strip()
        message_obj = extract_a2a_status_message(snapshot)
        if not final_answer:
            final_answer = extract_text_from_parts(message_obj.get("parts"))

        message_meta_obj = message_obj.get("metadata")
        message_meta = message_meta_obj if isinstance(message_meta_obj, dict) else {}
        output_type = str(message_meta.get("output_type") or "").strip()
        confidence = safe_float(message_meta.get("confidence"), default=0.0)

        tool_observations_obj = extract_a2a_artifact(snapshot, "tool_observations")
        tool_observations = tool_observations_obj if isinstance(tool_observations_obj, list) else []
        telemetry_obj = extract_a2a_artifact(snapshot, "telemetry")
        telemetry = telemetry_obj if isinstance(telemetry_obj, dict) else {}
        progress_events_obj = extract_a2a_artifact(snapshot, "progress_events")
        progress_events = progress_events_obj if isinstance(progress_events_obj, list) else []
        processing_steps = build_processing_steps_from_progress_events(progress_events)

        return {
            "output": final_answer,
            "output_type": output_type,
            "confidence": confidence,
            "tool_observations": tool_observations,
            "reflection_events": progress_events,
            "processing_steps": processing_steps,
            "telemetry": telemetry,
            "a2a_task_id": task_id,
        }
