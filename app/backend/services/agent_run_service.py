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
from app.core.langsmith_tracing import set_trace_metadata
from app.backend.observability.session_logger import (
    ensure_session_meta,
    reset_session_context,
    set_session_context,
)


class AgentRunService:
    """封装 /agent/run 相关业务。"""

    def __init__(
        self,
        get_a2a: Any,
        log_api_error: Any,
    ) -> None:
        """初始化实例并注入运行依赖。

        Args:
            get_a2a: 延迟获取 A2AService 的回调，用于避免服务初始化阶段立即构建运行时。
            log_api_error: 统一记录 API 错误的回调，保留端点、错误类别和上下文信息。

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
        """执行一次 Agent 请求并转换为旧接口兼容响应。

        Args:
            input_text: 用户原始问题文本。
            session_id: 会话标识，用于绑定上下文和追踪日志。
            user_id: 用户标识，用于会话归属和追踪维度。
            strategy: Agent 运行策略名称。
            max_turns: 可选的最大迭代轮数。
            trace_id: 本次请求的追踪标识。
            a2a_mode: 调用方传入的 A2A 运行模式。

        Returns:
            Dict[str, Any]: 兼容前端消费的成功或失败响应。
        """
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
            traces = self.build_traces_from_processing_steps(
                result.get("processing_steps", [])
            )
            response: Dict[str, Any] = {
                "ok": True,
                "answer": result.get("output", ""),
                "output_type": result.get("output_type", ""),
                "strategy": strategy,
                "traces": traces,
                "tool_observations": result.get("tool_observations", []),
                "reflection_events": result.get("reflection_events", []),
                "telemetry": result.get("telemetry", {}),
                "evidence_summary": result.get("evidence_summary", {}),
                "question_plan": result.get("question_plan", {}),
                "evidence_bundle": result.get("evidence_bundle", {}),
                "confidence": result.get("confidence", "low"),
                "confidence_breakdown": result.get("confidence_breakdown", {}),
                "trace_id": trace_id,
                "run_path": "a2a",
                "a2a_mode": a2a_mode,
            }
            if str(result.get("a2a_task_id") or "").strip():
                response["a2a_task_id"] = str(result.get("a2a_task_id"))
            return response
        except ValueError as exc:
            self._log_api_error("/agent/run", "validation", exc, strategy=strategy)
            return {
                "ok": False,
                "error": f"invalid_request:{str(exc)}",
                "trace_id": trace_id,
            }
        except RuntimeError as exc:
            self._log_api_error("/agent/run", "runtime", exc, strategy=strategy)
            return {
                "ok": False,
                "error": f"agent_runtime_error:{str(exc)}",
                "trace_id": trace_id,
            }
        except (
            RuntimeError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            OSError,
            ImportError,
        ) as exc:
            self._log_api_error("/agent/run", "unknown", exc, strategy=strategy)
            return {
                "ok": False,
                "error": f"agent_unknown_error:{str(exc)}",
                "trace_id": trace_id,
            }
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
        """通过 A2A 服务提交任务、轮询结果并提取最终响应。

        Args:
            input_text: 用户原始问题文本。
            session_id: 会话标识，会写入 A2A message metadata。
            user_id: 用户标识，会写入 A2A message metadata。
            strategy: Agent 运行策略名称。
            max_turns: 可选的最大迭代轮数。
            trace_id: 本次请求的追踪标识。

        Returns:
            Dict[str, Any]: 从 A2A task artifact 和 status 中整理出的 Agent 结果。

        Raises:
            RuntimeError: A2A 任务缺失、失败、取消或返回结构不满足预期。
            TimeoutError: A2A 任务超过配置的等待时间。
        """
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

        llm_timeout_seconds = max(
            1.0,
            safe_float(os.getenv("LLM_REQUEST_TIMEOUT_SECONDS", "600"), default=600.0),
        )
        llm_retry_attempts = max(
            1.0,
            safe_float(os.getenv("LLM_REQUEST_RETRY_MAX_ATTEMPTS", "3"), default=3.0),
        )
        default_agent_timeout = max(
            180.0,
            llm_timeout_seconds * llm_retry_attempts + 300.0,
        )
        timeout_seconds = max(
            3.0,
            min(
                safe_float(
                    os.getenv(
                        "AGENT_RUN_A2A_TIMEOUT_SECONDS", str(int(default_agent_timeout))
                    ),
                    default=default_agent_timeout,
                ),
                7200.0,
            ),
        )
        poll_ms = max(
            100,
            min(
                int(
                    safe_float(os.getenv("AGENT_RUN_A2A_POLL_MS", "250"), default=250.0)
                ),
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
                status_text = str(
                    status_meta.get("error") or status_meta.get("error_type") or state
                )
            raise RuntimeError(f"a2a_task_{state}:{status_text}")

        final_answer_obj = extract_a2a_artifact(snapshot, "final_answer")
        final_answer = str(final_answer_obj or "").strip()
        message_obj = extract_a2a_status_message(snapshot)
        if not final_answer:
            final_answer = extract_text_from_parts(message_obj.get("parts"))

        message_meta_obj = message_obj.get("metadata")
        message_meta = message_meta_obj if isinstance(message_meta_obj, dict) else {}
        output_type = str(message_meta.get("output_type") or "").strip()
        confidence = str(message_meta.get("confidence") or "low")
        confidence_breakdown_obj = message_meta.get("confidence_breakdown")
        confidence_breakdown = (
            confidence_breakdown_obj
            if isinstance(confidence_breakdown_obj, dict)
            else {}
        )

        tool_observations_obj = extract_a2a_artifact(snapshot, "tool_observations")
        tool_observations = (
            tool_observations_obj if isinstance(tool_observations_obj, list) else []
        )
        telemetry_obj = extract_a2a_artifact(snapshot, "telemetry")
        telemetry = telemetry_obj if isinstance(telemetry_obj, dict) else {}
        evidence_summary_obj = extract_a2a_artifact(snapshot, "evidence_summary")
        evidence_summary = (
            evidence_summary_obj if isinstance(evidence_summary_obj, dict) else {}
        )
        question_plan_obj = extract_a2a_artifact(snapshot, "question_plan")
        question_plan = question_plan_obj if isinstance(question_plan_obj, dict) else {}
        evidence_bundle_obj = extract_a2a_artifact(snapshot, "evidence_bundle")
        evidence_bundle = (
            evidence_bundle_obj if isinstance(evidence_bundle_obj, dict) else {}
        )
        progress_events_obj = extract_a2a_artifact(snapshot, "progress_events")
        progress_events = (
            progress_events_obj if isinstance(progress_events_obj, list) else []
        )
        processing_steps = build_processing_steps_from_progress_events(progress_events)

        return {
            "output": final_answer,
            "output_type": output_type,
            "confidence": confidence,
            "confidence_breakdown": confidence_breakdown,
            "tool_observations": tool_observations,
            "reflection_events": progress_events,
            "processing_steps": processing_steps,
            "telemetry": telemetry,
            "evidence_summary": evidence_summary,
            "question_plan": question_plan,
            "evidence_bundle": evidence_bundle,
            "a2a_task_id": task_id,
        }
