"""运行时依赖解析。"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import Request

from app.a2a import A2AService
from app.a2a.agent import GraphSubAgent, RagSubAgent
from app.backend.infrastructure.runtime_container import BackendRuntimeContainer
from app.backend.services import SessionMemoryService


def get_runtime(request: Request) -> BackendRuntimeContainer:
    """从 app.state 提取运行时容器。"""
    runtime: Any = getattr(request.app.state, "runtime", None)
    if isinstance(runtime, BackendRuntimeContainer):
        return runtime
    raise RuntimeError("runtime_container_not_initialized")


def get_session_memory_service(request: Request) -> SessionMemoryService:
    """获取会话记忆服务依赖。"""
    runtime = get_runtime(request)
    if runtime.session_memory_service is None:
        raise RuntimeError("session_memory_service_not_initialized")
    return runtime.session_memory_service


def get_mcp_server(request: Request) -> Any:
    """获取 MCP 服务依赖。"""
    runtime = get_runtime(request)
    if runtime.mcp_server is None:
        raise RuntimeError("mcp_server_not_initialized")
    return runtime.mcp_server


def get_a2a_service(
    request: Request,
    kind: Literal["orchestrator", "graph", "retrieval", "evaluator"],
) -> A2AService:
    """按类型获取 A2A 服务依赖。"""
    runtime = get_runtime(request)
    mapping: dict[str, Any] = {
        "orchestrator": runtime.a2a_service,
        "graph": runtime.a2a_graph_service,
        "retrieval": runtime.a2a_retrieval_service,
        "evaluator": runtime.a2a_evaluator_service,
    }
    service = mapping.get(kind)
    if isinstance(service, A2AService):
        return service
    raise RuntimeError(f"a2a_service_not_initialized:{kind}")


def get_graph_subagent(request: Request) -> GraphSubAgent:
    """获取图谱子 Agent 依赖。"""
    runtime = get_runtime(request)
    if runtime.graph_subagent is None:
        raise RuntimeError("graph_subagent_not_initialized")
    return runtime.graph_subagent


def get_rag_subagent(request: Request) -> RagSubAgent:
    """获取检索子 Agent 依赖。"""
    runtime = get_runtime(request)
    if runtime.rag_subagent is None:
        raise RuntimeError("rag_subagent_not_initialized")
    return runtime.rag_subagent
