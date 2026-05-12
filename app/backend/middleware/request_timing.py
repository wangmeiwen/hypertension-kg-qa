"""请求计时与链路日志中间件。"""

from __future__ import annotations

import inspect
import logging
import time
from typing import Any, AsyncIterator, Callable, Optional

from fastapi import Request
from fastapi.responses import StreamingResponse

from app.backend.dependencies.request_context import resolve_api_identity
from app.backend.security.admin_auth import reset_current_admin_token, set_current_admin_token
from app.core.langsmith_tracing import set_trace_metadata
from app.core.perf_logger import log_duration
from app.backend.observability.session_logger import (
    create_request_id,
    log_api_call,
    reset_request_context,
    set_request_context,
)

logger = logging.getLogger(__name__)


def _extract_bearer_token(authorization: str) -> Optional[str]:
    """从 Authorization 头提取 Bearer Token。"""
    auth_text = str(authorization or "").strip()
    if not auth_text:
        return None
    parts = auth_text.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    if not token:
        return None
    return token


async def _wrap_stream_body_iterator(
    body_iterator: Any,
    on_close: Callable[[str], Any],
) -> AsyncIterator[bytes]:
    """包装 StreamingResponse body_iterator，确保结束时回调统一收口。"""
    stream_error_type = ""
    try:
        if hasattr(body_iterator, "__aiter__"):
            async for chunk in body_iterator:
                yield chunk
        else:
            for chunk in body_iterator:
                yield chunk
    except BaseException as exc:
        stream_error_type = type(exc).__name__
        logger.exception(
            "stream_body_iterator_failed: error_type=%s error=%s",
            type(exc).__name__,
            exc,
        )
        raise
    finally:
        callback_ret = on_close(stream_error_type)
        if inspect.isawaitable(callback_ret):
            await callback_ret


def _resolve_api_session_user(request: Request) -> dict[str, str]:
    """兼容函数名，内部委托给 request_context。"""
    return resolve_api_identity(request)


async def request_timing_middleware(request: Request, call_next):
    """记录请求耗时与链路上下文。"""
    request_id = create_request_id()
    request.state.request_id = request_id
    request.state.trace_id = request_id
    request_token = set_request_context(request_id)
    bearer_token = _extract_bearer_token(request.headers.get("Authorization", ""))
    token_handle = set_current_admin_token(bearer_token)
    started = time.perf_counter()
    status_code = 500
    error_type = ""
    ttfb_ms = 0.0
    logged = False
    is_streaming_response = False
    client_ip = request.client.host if request.client else ""
    request_timer_ctx = log_duration("http_request", method=request.method, path=request.url.path)
    request_timer_ctx.__enter__()
    request_timer_closed = False

    def _finish_request_timer(exc: Optional[BaseException] = None) -> None:
        """关闭当前请求的性能计时上下文。

        Args:
            exc: 请求处理中捕获到的异常；为空表示正常结束。

        Returns:
            None: 无返回值。
        """
        nonlocal request_timer_closed
        if request_timer_closed:
            return
        request_timer_closed = True
        if exc is None:
            request_timer_ctx.__exit__(None, None, None)
            return
        request_timer_ctx.__exit__(type(exc), exc, exc.__traceback__)

    async def _log_stream_finished(stream_error_type: str) -> None:
        """在流式响应结束时记录完整请求日志。

        Args:
            stream_error_type: 流式迭代阶段捕获到的异常类型；空字符串表示正常结束。

        Returns:
            None: 无返回值。
        """
        nonlocal logged
        if logged:
            _finish_request_timer()
            return
        stream_total_ms = (time.perf_counter() - started) * 1000
        identity = _resolve_api_session_user(request)
        log_api_call(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            status_code=status_code,
            is_streaming=True,
            ttfb_ms=ttfb_ms,
            stream_total_ms=stream_total_ms,
            client_ip=client_ip,
            session_id=identity["session_id"],
            user_id=identity["user_id"],
            error_type=stream_error_type or error_type,
        )
        logged = True
        _finish_request_timer()

    try:
        set_trace_metadata(
            {
                "trace_id": request_id,
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
            },
            tags=["api"],
        )
        response = await call_next(request)
        response.headers["X-Trace-Id"] = request_id
        status_code = int(response.status_code)

        ttfb_ms = (time.perf_counter() - started) * 1000
        if isinstance(response, StreamingResponse):
            response.body_iterator = _wrap_stream_body_iterator(
                response.body_iterator,
                _log_stream_finished,
            )
            is_streaming_response = True
            return response

        identity = _resolve_api_session_user(request)
        log_api_call(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            status_code=status_code,
            is_streaming=False,
            ttfb_ms=ttfb_ms,
            stream_total_ms=ttfb_ms,
            client_ip=client_ip,
            session_id=identity["session_id"],
            user_id=identity["user_id"],
            error_type=error_type,
        )
        logged = True
        _finish_request_timer()
        return response
    except Exception as exc:
        error_type = type(exc).__name__
        logger.exception(
            "request_timing_middleware_failed: method=%s path=%s request_id=%s error_type=%s error=%s",
            request.method,
            request.url.path,
            request_id,
            type(exc).__name__,
            exc,
        )
        if not logged:
            stream_total_ms = (time.perf_counter() - started) * 1000
            fallback_ttfb = ttfb_ms if ttfb_ms > 0.0 else stream_total_ms
            identity = _resolve_api_session_user(request)
            log_api_call(
                request_id=request_id,
                method=request.method,
                path=request.url.path,
                status_code=status_code,
                is_streaming=False,
                ttfb_ms=fallback_ttfb,
                stream_total_ms=stream_total_ms,
                client_ip=client_ip,
                session_id=identity["session_id"],
                user_id=identity["user_id"],
                error_type=error_type,
            )
            logged = True
        _finish_request_timer(exc)
        raise
    finally:
        if not is_streaming_response:
            _finish_request_timer()
        reset_current_admin_token(token_handle)
        reset_request_context(request_token)
