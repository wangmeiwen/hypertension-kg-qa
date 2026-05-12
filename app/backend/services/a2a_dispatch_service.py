"""A2A 路由分发服务。"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Callable, Optional

from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.a2a import A2AService
from app.core.langsmith_tracing import set_trace_metadata


class A2ADispatchService:
    """统一处理 A2A 三类 service 的路由分发。"""

    def __init__(
        self,
        get_a2a: Callable[[], A2AService],
        get_a2a_graph: Callable[[], A2AService],
        get_a2a_retrieval: Callable[[], A2AService],
        log_api_error: Callable[..., None],
    ) -> None:
        """初始化 A2A 分发服务并注入三类服务获取器。

        Args:
            get_a2a: 获取主编排 A2AService 的函数。
            get_a2a_graph: 获取图谱子代理 A2AService 的函数。
            get_a2a_retrieval: 获取检索子代理 A2AService 的函数。
            log_api_error: 统一记录 API 异常的函数。

        Returns:
            None: 无返回值。
        """
        self._service_map: dict[str, Callable[[], A2AService]] = {
            "orchestrator": get_a2a,
            "graph": get_a2a_graph,
            "retrieval": get_a2a_retrieval,
        }
        self._log_api_error = log_api_error

    def _get_service(self, service_kind: str) -> A2AService:
        """按服务类型获取目标 A2AService。

        Args:
            service_kind: 服务类型，支持 orchestrator、graph、retrieval。

        Returns:
            对应类型的 A2AService。
        """
        getter = self._service_map.get(service_kind)
        if getter is None:
            raise ValueError(f"unknown_service_kind:{service_kind}")
        return getter()

    @staticmethod
    def build_rpc_fallback(data: dict[str, Any], trace_id: str, detail: str) -> dict[str, Any]:
        """构建 A2A RPC 兜底错误响应。"""
        return {
            "jsonrpc": "2.0",
            "id": (data or {}).get("id"),
            "error": {
                "code": -32000,
                "message": "Server error",
                "data": {"detail": str(detail or ""), "trace_id": trace_id},
            },
        }

    async def handle_rpc(
        self,
        service_kind: str,
        data: dict[str, Any],
        trace_id: str,
        endpoint: str,
    ) -> JSONResponse:
        """分发通用 A2A JSON-RPC 请求。

        Args:
            service_kind: 目标 A2A 服务类型。
            data: JSON-RPC 请求体。
            trace_id: 当前请求追踪 ID。
            endpoint: 当前 HTTP endpoint，用于日志标识。

        Returns:
            包装后的 JSON-RPC HTTP 响应。
        """
        set_trace_metadata(
            {
                "trace_id": trace_id,
                "request_id": trace_id,
                "endpoint": endpoint,
                "rpc_method": str((data or {}).get("method") or ""),
            },
            tags=["api", "a2a"],
        )
        try:
            service = self._get_service(service_kind)
            response = await service.ahandle_rpc(data, trace_id)
            return JSONResponse(status_code=200, content=response)
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            self._log_api_error(endpoint, "runtime", exc)
            fallback = self.build_rpc_fallback(data=data, trace_id=trace_id, detail=str(exc))
            return JSONResponse(status_code=200, content=fallback)

    async def list_tasks(
        self,
        service_kind: str,
        trace_id: str,
        endpoint: str,
        context_id: str = "",
        limit: int = 20,
    ) -> dict[str, Any]:
        """分发 A2A 任务列表查询。"""
        try:
            service = self._get_service(service_kind)
            rows = await service.alist_tasks(context_id, limit)
            return {"ok": True, "tasks": rows, "trace_id": trace_id}
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            self._log_api_error(endpoint, "runtime", exc, context_id=context_id)
            return {"ok": False, "error": f"a2a_list_tasks_error:{str(exc)}", "trace_id": trace_id}

    async def get_task(
        self,
        service_kind: str,
        task_id: str,
        trace_id: str,
        endpoint: str,
    ) -> dict[str, Any] | JSONResponse:
        """分发 A2A 单任务查询。"""
        try:
            service = self._get_service(service_kind)
            task = await service.aget_task(task_id)
            if not task:
                return JSONResponse(
                    status_code=404,
                    content={"ok": False, "error": "task_not_found", "task_id": task_id, "trace_id": trace_id},
                )
            return {"ok": True, "task": task, "trace_id": trace_id}
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            self._log_api_error(endpoint, "runtime", exc, task_id=task_id)
            return {"ok": False, "error": f"a2a_get_task_error:{str(exc)}", "trace_id": trace_id}

    async def stream_task_events(
        self,
        service_kind: str,
        task_id: str,
        request: Request,
        trace_id: str,
        endpoint: str,
        cursor: int = 0,
        poll_ms: int = 500,
        timeout_seconds: int = 300,
    ) -> StreamingResponse | JSONResponse:
        """分发 A2A 任务事件流请求。"""
        set_trace_metadata(
            {
                "trace_id": trace_id,
                "request_id": trace_id,
                "endpoint": endpoint,
                "task_id": str(task_id or ""),
            },
            tags=["api", "a2a", "stream"],
        )
        try:
            service = self._get_service(service_kind)
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            self._log_api_error(endpoint, "runtime", exc, task_id=task_id)
            return JSONResponse(
                status_code=500,
                content={"ok": False, "error": f"a2a_stream_init_error:{str(exc)}", "trace_id": trace_id},
            )

        safe_cursor = max(0, int(cursor or 0))
        safe_poll_ms = max(100, min(int(poll_ms or 500), 5000))
        safe_timeout_seconds = max(5, min(int(timeout_seconds or 300), 3600))

        if safe_cursor <= 0:
            last_event_id = str(request.headers.get("Last-Event-ID") or "").strip()
            try:
                if last_event_id:
                    safe_cursor = max(0, int(last_event_id))
            except ValueError:
                safe_cursor = 0

        async def event_stream():
            """按游标轮询任务事件并生成 SSE 文本块。

            Args:
                无。

            Returns:
                异步生成器逐条产出 SSE 协议字符串。
            """
            nonlocal safe_cursor
            started_at = time.time()
            while True:
                snapshot = await service.aget_task_updates(task_id, safe_cursor)
                if snapshot is None:
                    payload = {"type": "error", "error": "task_not_found", "task_id": task_id}
                    yield f"event: error\\ndata: {json.dumps(payload, ensure_ascii=False)}\\n\\n"
                    break

                events_obj = snapshot.get("events")
                events = events_obj if isinstance(events_obj, list) else []
                next_cursor = int(snapshot.get("next_cursor") or safe_cursor)
                base_cursor = next_cursor - len(events)

                for idx, event in enumerate(events, start=1):
                    event_cursor = base_cursor + idx
                    payload = {
                        "type": "progress",
                        "task_id": str(task_id or ""),
                        "cursor": event_cursor,
                        "event": event,
                    }
                    yield f"id: {event_cursor}\\nevent: progress\\ndata: {json.dumps(payload, ensure_ascii=False)}\\n\\n"

                safe_cursor = next_cursor
                state = str(snapshot.get("state") or "")
                terminal = bool(snapshot.get("terminal"))
                if terminal:
                    task_payload = {
                        "type": "task",
                        "task_id": str(task_id or ""),
                        "cursor": safe_cursor,
                        "state": state,
                        "task": snapshot.get("task"),
                    }
                    yield f"id: {safe_cursor}\\nevent: task\\ndata: {json.dumps(task_payload, ensure_ascii=False)}\\n\\n"
                    done_payload = {
                        "type": "done",
                        "task_id": str(task_id or ""),
                        "cursor": safe_cursor,
                        "state": state,
                    }
                    yield f"id: {safe_cursor}\\nevent: done\\ndata: {json.dumps(done_payload, ensure_ascii=False)}\\n\\n"
                    break

                if time.time() - started_at >= safe_timeout_seconds:
                    timeout_payload = {
                        "type": "stream_timeout",
                        "task_id": str(task_id or ""),
                        "cursor": safe_cursor,
                    }
                    yield f"id: {safe_cursor}\\nevent: timeout\\ndata: {json.dumps(timeout_payload, ensure_ascii=False)}\\n\\n"
                    break

                if not events:
                    heartbeat_payload = {
                        "type": "heartbeat",
                        "task_id": str(task_id or ""),
                        "cursor": safe_cursor,
                    }
                    yield f"id: {safe_cursor}\\nevent: heartbeat\\ndata: {json.dumps(heartbeat_payload, ensure_ascii=False)}\\n\\n"

                await asyncio.sleep(float(safe_poll_ms) / 1000.0)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    async def cancel_task(
        self,
        service_kind: str,
        task_id: str,
        trace_id: str,
        endpoint: str,
        data: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any] | JSONResponse:
        """分发 A2A 任务取消请求。"""
        payload = data if isinstance(data, dict) else {}
        reason = str(payload.get("reason") or "manual_cancel")
        try:
            service = self._get_service(service_kind)
            result = await service.acancel_task(task_id, reason)
            if not bool(result.get("ok")) and str(result.get("error") or "") == "task_not_found":
                return JSONResponse(
                    status_code=404,
                    content={"ok": False, "error": "task_not_found", "task_id": task_id, "trace_id": trace_id},
                )
            result["trace_id"] = trace_id
            return result
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            self._log_api_error(endpoint, "runtime", exc, task_id=task_id)
            return {"ok": False, "error": f"a2a_cancel_task_error:{str(exc)}", "trace_id": trace_id}
