"""查询与会话主链路路由。"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Callable, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.backend.services.a2a_event_stream_service import A2AEventStreamService
from app.backend.services.agent_run_service import AgentRunService
from app.backend.services.readiness_service import ReadinessService
from app.backend.services.session_stream_service import SessionStreamService
from app.backend.services.session_turn_service import SessionTurnService

logger = logging.getLogger(__name__)


def create_query_chat_router(
    agent_run_service: AgentRunService,
    session_turn_service: SessionTurnService,
    session_stream_service: SessionStreamService,
    event_stream_service: A2AEventStreamService,
    readiness_service: ReadinessService,
    get_trace_id: Callable[[Optional[Request]], str],
    enforce_rate_limit: Callable[..., Optional[JSONResponse]],
    rate_limiter: Any,
    endpoint_limits: dict[str, dict[str, int]],
    window_seconds: float,
) -> APIRouter:
    """创建查询、Agent 运行和会话流式问答路由。

    Args:
        agent_run_service: 非流式 Agent 运行服务。
        session_turn_service: 会话轮次准备服务。
        session_stream_service: 会话流式输出服务。
        event_stream_service: 兼容旧 /process 的事件流服务。
        readiness_service: 核心依赖就绪检查服务。
        get_trace_id: 从请求中提取 trace_id 的函数。
        enforce_rate_limit: 重接口限流检查函数。
        rate_limiter: 限流器实例。
        endpoint_limits: 各接口限流配置。
        window_seconds: 限流统计窗口长度。

    Returns:
        已注册问答主链路路由的 APIRouter。
    """
    router = APIRouter()

    @router.post("/process")
    async def process(data: dict, request: Request):
        """处理不落库的兼容流式问答请求入口。"""
        readiness_error = await readiness_service.ensure_core_ready("/process")
        if readiness_error is not None:
            return readiness_error

        input_text = str(data.get("input") or "")
        user_id = str(data.get("user_id") or "") or str(uuid.uuid4())
        session_id = str(data.get("session_id") or "")

        return StreamingResponse(
            event_stream_service.generate_data_lines(
                input_text=input_text,
                user_id=user_id,
                session_id=session_id,
                request_id=str(getattr(request.state, "request_id", "") or ""),
            ),
            media_type="text/plain",
        )

    @router.post("/agent/run")
    async def agent_run(data: dict, request: Request):
        """运行一次非流式 Agent 问答并返回结构化结果。"""
        trace_id = get_trace_id(request)
        readiness_error = await readiness_service.ensure_core_ready("/agent/run")
        if readiness_error is not None:
            return readiness_error

        input_text = str(data.get("input") or "").strip()
        session_id = str(data.get("session_id") or "")
        user_id = str(data.get("user_id") or "")
        strategy = str(data.get("strategy") or "iterative").strip().lower()
        try:
            a2a_mode = agent_run_service.resolve_agent_run_mode(data)
        except ValueError as exc:
            logger.exception(
                "agent_run_invalid_request: trace_id=%s error_type=%s error=%s",
                trace_id,
                type(exc).__name__,
                exc,
            )
            return {"ok": False, "error": f"invalid_request:{str(exc)}", "trace_id": trace_id}

        raw_max_turns = data.get("max_turns")
        max_turns: Optional[int] = raw_max_turns if isinstance(raw_max_turns, int) and raw_max_turns > 0 else None

        agent_run_service.bind_agent_trace_metadata(
            trace_id=trace_id,
            session_id=session_id,
            user_id=user_id,
            strategy=strategy,
            a2a_mode=a2a_mode,
        )

        limited = enforce_rate_limit(
            endpoint="/agent/run",
            user_id=str(user_id or ""),
            session_id=str(session_id or ""),
            request=request,
            rate_limiter=rate_limiter,
            endpoint_limits=endpoint_limits,
            window_seconds=window_seconds,
        )
        if limited is not None:
            return limited

        if not input_text:
            return {"ok": False, "error": "input_required", "trace_id": trace_id}

        return await agent_run_service.run_agent(
            input_text=input_text,
            session_id=session_id,
            user_id=user_id,
            strategy=strategy,
            max_turns=max_turns,
            trace_id=trace_id,
            a2a_mode=a2a_mode,
        )

    @router.post("/process_with_session")
    async def process_with_session(data: dict, request: Request):
        """处理流式问答请求，并将用户问题和助手回答写入会话历史。"""
        trace_id = get_trace_id(request)
        readiness_error = await readiness_service.ensure_core_ready("/process_with_session")
        if readiness_error is not None:
            return readiness_error

        input_text = str(data.get("input") or "")
        session_id = str(data.get("session_id") or "")
        user_id = str(data.get("user_id") or "")
        image_data = data.get("image", None)
        file_name = str(data.get("file_name") or "").strip()

        session_turn_service.bind_process_session_trace_metadata(
            trace_id=trace_id,
            session_id=session_id,
            user_id=user_id,
        )

        limited = enforce_rate_limit(
            endpoint="/process_with_session",
            user_id=str(user_id or ""),
            session_id=str(session_id or ""),
            request=request,
            rate_limiter=rate_limiter,
            endpoint_limits=endpoint_limits,
            window_seconds=window_seconds,
        )
        if limited is not None:
            return limited

        prepared = await session_turn_service.prepare_session_turn(
            input_text=input_text,
            session_id=session_id,
            user_id=user_id,
            image_data=image_data,
            file_name=file_name,
        )
        if not bool(prepared.get("ok")):
            return {"ok": False, "error": str(prepared.get("error") or "unknown"), "trace_id": trace_id}

        request.state.user_id = str(prepared.get("user_id") or "")
        request.state.session_id = str(prepared.get("session_id") or "")

        return StreamingResponse(
            session_stream_service.generate_data_with_session_lines(
                input_text=input_text,
                user_id=str(prepared.get("user_id") or ""),
                session_id=str(prepared.get("session_id") or ""),
                image_data=image_data,
                user_message_id=int(prepared.get("user_message_id") or 0),
                request_id=str(getattr(request.state, "request_id", "") or ""),
                original_input_text=str(prepared.get("original_input_text") or input_text or ""),
                memory_context=prepared.get("memory_context") if isinstance(prepared.get("memory_context"), dict) else {},
            ),
            media_type="text/plain",
        )

    return router
