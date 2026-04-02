"""后端通用响应构造。"""

from __future__ import annotations

from typing import Any, AsyncIterator

from fastapi.responses import JSONResponse, StreamingResponse


def unauthorized_response(message: str = "未登录") -> JSONResponse:
    """构造 401 响应。"""
    return JSONResponse(status_code=401, content={"ok": False, "error": message})


def not_found_response(message: str = "资源不存在") -> JSONResponse:
    """构造 404 响应。"""
    return JSONResponse(status_code=404, content={"ok": False, "error": message})


def rate_limited_response(
    endpoint: str,
    dimension: str,
    retry_after_ms: int,
    trace_id: str,
    limit: int,
    window_seconds: float,
) -> JSONResponse:
    """构造 429 响应。"""
    return JSONResponse(
        status_code=429,
        content={
            "ok": False,
            "error": "rate_limited",
            "endpoint": endpoint,
            "dimension": dimension,
            "retry_after_ms": retry_after_ms,
            "trace_id": trace_id,
            "limit": limit,
            "window_seconds": window_seconds,
        },
    )


def jsonrpc_error_response(request_id: Any, trace_id: str, detail: str) -> JSONResponse:
    """构造 JSON-RPC 错误响应。"""
    return JSONResponse(
        status_code=200,
        content={
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": -32000,
                "message": "Server error",
                "data": {"detail": str(detail or ""), "trace_id": trace_id},
            },
        },
    )


def sse_streaming_response(event_iter: AsyncIterator[str]) -> StreamingResponse:
    """构造标准 SSE StreamingResponse。"""
    return StreamingResponse(
        event_iter,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
