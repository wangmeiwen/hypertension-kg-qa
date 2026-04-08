"""A2A 协议路由。"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.a2a import A2AService, all_agent_cards
from app.backend.services.a2a_dispatch_service import A2ADispatchService
from app.common.langsmith_tracing import set_trace_metadata

logger = logging.getLogger(__name__)


def create_a2a_router(
    get_trace_id: Callable[[Optional[Request]], str],
    get_a2a: Callable[[], A2AService],
    dispatch_service: A2ADispatchService,
) -> APIRouter:
    """创建 A2A 路由。"""
    router = APIRouter()

    @router.get("/a2a/agent-card")
    async def a2a_agent_card(request: Request) -> dict[str, Any]:
        """返回 A2A Agent Card。"""
        trace_id = get_trace_id(request)
        set_trace_metadata(
            {
                "trace_id": trace_id,
                "request_id": trace_id,
                "endpoint": "/a2a/agent-card",
            },
            tags=["api", "a2a"],
        )
        try:
            service = get_a2a()
            return {"ok": True, "agent_card": service.get_agent_card(), "trace_id": trace_id}
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            logger.exception(
                "a2a_agent_card_failed: trace_id=%s error_type=%s error=%s",
                trace_id,
                type(exc).__name__,
                exc,
            )
            fallback = A2ADispatchService.build_rpc_fallback({"id": None}, trace_id, str(exc))
            return {"ok": False, "error": f"a2a_card_error:{str(exc)}", "trace_id": trace_id, "rpc": fallback}

    @router.get("/a2a/agents/cards")
    async def a2a_agent_cards(request: Request) -> dict[str, Any]:
        """返回当前可发现的 Agent Card 列表。"""
        trace_id = get_trace_id(request)
        set_trace_metadata(
            {
                "trace_id": trace_id,
                "request_id": trace_id,
                "endpoint": "/a2a/agents/cards",
            },
            tags=["api", "a2a"],
        )
        try:
            return {"ok": True, "cards": all_agent_cards(), "trace_id": trace_id}
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            logger.exception(
                "a2a_agent_cards_failed: trace_id=%s error_type=%s error=%s",
                trace_id,
                type(exc).__name__,
                exc,
            )
            return {"ok": False, "error": f"a2a_cards_error:{str(exc)}", "trace_id": trace_id}

    @router.post("/a2a/rpc")
    async def a2a_rpc(data: dict, request: Request) -> JSONResponse:
        """执行a2a_rpc流程并返回结果。
        
        Args:
            data: 函数输入参数。
            request: 函数输入参数。
        
        Returns:
            JSONResponse: 执行结果。
        """
        trace_id = get_trace_id(request)
        return await dispatch_service.handle_rpc("orchestrator", data, trace_id, "/a2a/rpc")

    @router.get("/a2a/tasks")
    async def a2a_list_tasks(request: Request, context_id: str = "", limit: int = 20) -> dict[str, Any]:
        """执行a2a_list_tasks流程并返回结果。
        
        Args:
            request: 函数输入参数。
            context_id: 对象标识。
            limit: 函数输入参数。
        
        Returns:
            dict[str, Any]: 结构化结果数据。
        """
        trace_id = get_trace_id(request)
        return await dispatch_service.list_tasks("orchestrator", trace_id, "/a2a/tasks", context_id=context_id, limit=limit)

    @router.get("/a2a/tasks/{task_id}")
    async def a2a_get_task(task_id: str, request: Request) -> Any:
        """执行a2a_get_task流程并返回结果。
        
        Args:
            task_id: 对象标识。
            request: 函数输入参数。
        
        Returns:
            Any: 执行结果。
        """
        trace_id = get_trace_id(request)
        return await dispatch_service.get_task("orchestrator", task_id, trace_id, "/a2a/tasks/{task_id}")

    @router.get("/a2a/tasks/{task_id}/events")
    async def a2a_task_events(
        task_id: str,
        request: Request,
        cursor: int = 0,
        poll_ms: int = 500,
        timeout_seconds: int = 300,
    ) -> Any:
        """执行a2a_task_events流程并返回结果。
        
        Args:
            task_id: 对象标识。
            request: 函数输入参数。
            cursor: 函数输入参数。
            poll_ms: 函数输入参数。
            timeout_seconds: 函数输入参数。
        
        Returns:
            Any: 执行结果。
        """
        trace_id = get_trace_id(request)
        return await dispatch_service.stream_task_events(
            "orchestrator",
            task_id,
            request,
            trace_id,
            "/a2a/tasks/{task_id}/events",
            cursor=cursor,
            poll_ms=poll_ms,
            timeout_seconds=timeout_seconds,
        )

    @router.post("/a2a/tasks/{task_id}/cancel")
    async def a2a_cancel_task(
        task_id: str,
        request: Request,
        data: Optional[dict[str, Any]] = None,
    ) -> Any:
        """执行a2a_cancel_task流程并返回结果。
        
        Args:
            task_id: 对象标识。
            request: 函数输入参数。
            data: 函数输入参数。
        
        Returns:
            Any: 执行结果。
        """
        trace_id = get_trace_id(request)
        return await dispatch_service.cancel_task(
            "orchestrator",
            task_id,
            trace_id,
            "/a2a/tasks/{task_id}/cancel",
            data=data,
        )

    @router.post("/a2a/graph/rpc")
    async def a2a_graph_rpc(data: dict, request: Request) -> JSONResponse:
        """执行a2a_graph_rpc流程并返回结果。
        
        Args:
            data: 函数输入参数。
            request: 函数输入参数。
        
        Returns:
            JSONResponse: 执行结果。
        """
        trace_id = get_trace_id(request)
        return await dispatch_service.handle_rpc("graph", data, trace_id, "/a2a/graph/rpc")

    @router.get("/a2a/graph/tasks")
    async def a2a_graph_list_tasks(request: Request, context_id: str = "", limit: int = 20) -> dict[str, Any]:
        """执行a2a_graph_list_tasks流程并返回结果。
        
        Args:
            request: 函数输入参数。
            context_id: 对象标识。
            limit: 函数输入参数。
        
        Returns:
            dict[str, Any]: 结构化结果数据。
        """
        trace_id = get_trace_id(request)
        return await dispatch_service.list_tasks("graph", trace_id, "/a2a/graph/tasks", context_id=context_id, limit=limit)

    @router.get("/a2a/graph/tasks/{task_id}")
    async def a2a_graph_get_task(task_id: str, request: Request) -> Any:
        """执行a2a_graph_get_task流程并返回结果。
        
        Args:
            task_id: 对象标识。
            request: 函数输入参数。
        
        Returns:
            Any: 执行结果。
        """
        trace_id = get_trace_id(request)
        return await dispatch_service.get_task("graph", task_id, trace_id, "/a2a/graph/tasks/{task_id}")

    @router.get("/a2a/graph/tasks/{task_id}/events")
    async def a2a_graph_task_events(
        task_id: str,
        request: Request,
        cursor: int = 0,
        poll_ms: int = 500,
        timeout_seconds: int = 300,
    ) -> Any:
        """执行a2a_graph_task_events流程并返回结果。
        
        Args:
            task_id: 对象标识。
            request: 函数输入参数。
            cursor: 函数输入参数。
            poll_ms: 函数输入参数。
            timeout_seconds: 函数输入参数。
        
        Returns:
            Any: 执行结果。
        """
        trace_id = get_trace_id(request)
        return await dispatch_service.stream_task_events(
            "graph",
            task_id,
            request,
            trace_id,
            "/a2a/graph/tasks/{task_id}/events",
            cursor=cursor,
            poll_ms=poll_ms,
            timeout_seconds=timeout_seconds,
        )

    @router.post("/a2a/graph/tasks/{task_id}/cancel")
    async def a2a_graph_cancel_task(
        task_id: str,
        request: Request,
        data: Optional[dict[str, Any]] = None,
    ) -> Any:
        """执行a2a_graph_cancel_task流程并返回结果。
        
        Args:
            task_id: 对象标识。
            request: 函数输入参数。
            data: 函数输入参数。
        
        Returns:
            Any: 执行结果。
        """
        trace_id = get_trace_id(request)
        return await dispatch_service.cancel_task(
            "graph",
            task_id,
            trace_id,
            "/a2a/graph/tasks/{task_id}/cancel",
            data=data,
        )

    @router.post("/a2a/retrieval/rpc")
    async def a2a_retrieval_rpc(data: dict, request: Request) -> JSONResponse:
        """执行a2a_retrieval_rpc流程并返回结果。
        
        Args:
            data: 函数输入参数。
            request: 函数输入参数。
        
        Returns:
            JSONResponse: 执行结果。
        """
        trace_id = get_trace_id(request)
        return await dispatch_service.handle_rpc("retrieval", data, trace_id, "/a2a/retrieval/rpc")

    @router.get("/a2a/retrieval/tasks")
    async def a2a_retrieval_list_tasks(request: Request, context_id: str = "", limit: int = 20) -> dict[str, Any]:
        """执行a2a_retrieval_list_tasks流程并返回结果。
        
        Args:
            request: 函数输入参数。
            context_id: 对象标识。
            limit: 函数输入参数。
        
        Returns:
            dict[str, Any]: 结构化结果数据。
        """
        trace_id = get_trace_id(request)
        return await dispatch_service.list_tasks("retrieval", trace_id, "/a2a/retrieval/tasks", context_id=context_id, limit=limit)

    @router.get("/a2a/retrieval/tasks/{task_id}")
    async def a2a_retrieval_get_task(task_id: str, request: Request) -> Any:
        """执行a2a_retrieval_get_task流程并返回结果。
        
        Args:
            task_id: 对象标识。
            request: 函数输入参数。
        
        Returns:
            Any: 执行结果。
        """
        trace_id = get_trace_id(request)
        return await dispatch_service.get_task("retrieval", task_id, trace_id, "/a2a/retrieval/tasks/{task_id}")

    @router.get("/a2a/retrieval/tasks/{task_id}/events")
    async def a2a_retrieval_task_events(
        task_id: str,
        request: Request,
        cursor: int = 0,
        poll_ms: int = 500,
        timeout_seconds: int = 300,
    ) -> Any:
        """执行a2a_retrieval_task_events流程并返回结果。
        
        Args:
            task_id: 对象标识。
            request: 函数输入参数。
            cursor: 函数输入参数。
            poll_ms: 函数输入参数。
            timeout_seconds: 函数输入参数。
        
        Returns:
            Any: 执行结果。
        """
        trace_id = get_trace_id(request)
        return await dispatch_service.stream_task_events(
            "retrieval",
            task_id,
            request,
            trace_id,
            "/a2a/retrieval/tasks/{task_id}/events",
            cursor=cursor,
            poll_ms=poll_ms,
            timeout_seconds=timeout_seconds,
        )

    @router.post("/a2a/retrieval/tasks/{task_id}/cancel")
    async def a2a_retrieval_cancel_task(
        task_id: str,
        request: Request,
        data: Optional[dict[str, Any]] = None,
    ) -> Any:
        """执行a2a_retrieval_cancel_task流程并返回结果。
        
        Args:
            task_id: 对象标识。
            request: 函数输入参数。
            data: 函数输入参数。
        
        Returns:
            Any: 执行结果。
        """
        trace_id = get_trace_id(request)
        return await dispatch_service.cancel_task(
            "retrieval",
            task_id,
            trace_id,
            "/a2a/retrieval/tasks/{task_id}/cancel",
            data=data,
        )

    return router
