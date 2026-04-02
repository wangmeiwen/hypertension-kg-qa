"""请求上下文解析工具。"""

from __future__ import annotations

from typing import Optional

from fastapi import Request


def get_trace_id(request: Optional[Request]) -> str:
    """从请求对象提取 trace_id。"""
    if request is None:
        return ""
    return str(getattr(request.state, "request_id", "") or "")


def resolve_api_identity(request: Request) -> dict[str, str]:
    """解析会话与用户身份，优先 request.state。"""
    state_session_id = str(getattr(request.state, "session_id", "") or "")
    state_user_id = str(getattr(request.state, "user_id", "") or "")
    query_session_id = str(request.query_params.get("session_id") or "")
    query_user_id = str(request.query_params.get("user_id") or "")
    return {
        "session_id": state_session_id or query_session_id,
        "user_id": state_user_id or query_user_id,
    }
