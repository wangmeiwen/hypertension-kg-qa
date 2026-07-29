"""A2A 协议服务（纯异步实现）。"""

from __future__ import annotations

import asyncio
import copy
import inspect
import logging
import time
import uuid
from typing import Any, Awaitable, Callable, Optional

from app.a2a.cards import (
    build_kg_subagent_card,
    build_orchestrator_card,
    build_rag_subagent_card,
)
from app.a2a.runtime.request_context import RequestContextParser

TerminalStates = {"completed", "failed", "canceled"}
logger = logging.getLogger(__name__)


class A2AService:
    """轻量 A2A 协议服务（仅异步 API）。"""

    def __init__(
        self,
        *,
        run_query: Callable[..., Awaitable[dict[str, Any]] | dict[str, Any]],
        agent_name: str = "Hypertension Orchestrator Agent",
        agent_description: str = "A2A Agent",
        protocol_version: str = "0.2.5",
        agent_url: str = "/a2a/rpc",
        events_base_path: str = "/a2a/tasks",
        max_workers: int = 4,
    ) -> None:
        """初始化 A2A 服务实例。

        Args:
            run_query: 任务执行函数（支持异步或同步返回）。
            agent_name: Agent 展示名称。
            agent_description: Agent 描述文本。
            protocol_version: A2A 协议版本。
            agent_url: A2A RPC 地址。
            events_base_path: 任务事件流基础路径。
            max_workers: 保留参数，当前未启用线程池。

        Returns:
            None: 无返回值。
        """
        _ = max_workers
        self._run_query = run_query
        self._agent_name = str(agent_name or "A2A Agent").strip()
        self._agent_description = str(agent_description or "").strip()
        self._protocol_version = str(protocol_version or "0.2.5").strip() or "0.2.5"
        self._agent_url = str(agent_url or "/a2a/rpc").strip() or "/a2a/rpc"
        self._events_base_path = str(events_base_path or "/a2a/tasks").strip() or "/a2a/tasks"

        self._lock = asyncio.Lock()
        self._tasks: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _public_task_payload(task: dict[str, Any], include_events: bool = False) -> dict[str, Any]:
        """构建对外可返回的任务载荷。

        Args:
            task: 任务对象字典。
            include_events: 是否返回 events 字段。

        Returns:
            dict[str, Any]: 可对外返回的任务数据。
        """
        payload = {
            key: value
            for key, value in task.items()
            if key not in {"async_task", "task_loop", "cancel_event"}
        }
        if not include_events:
            payload.pop("events", None)
        return copy.deepcopy(payload)

    def get_agent_card(self) -> dict[str, Any]:
        """获取当前服务对应的 Agent Card。

        Returns:
            dict[str, Any]: Agent Card 信息。
        """
        name_lower = self._agent_name.lower()
        if "graph" in name_lower:
            card = build_kg_subagent_card(agent_url=self._agent_url, protocol_version=self._protocol_version)
        elif "retrieval" in name_lower or "rag" in name_lower:
            card = build_rag_subagent_card(agent_url=self._agent_url, protocol_version=self._protocol_version)
        else:
            card = build_orchestrator_card(agent_url=self._agent_url, protocol_version=self._protocol_version)
        card["name"] = self._agent_name
        card["description"] = self._agent_description
        return card

    @staticmethod
    def _new_task_id() -> str:
        """生成唯一任务 ID。

        Returns:
            str: 新任务 ID。
        """
        return f"task-{uuid.uuid4().hex}"

    @staticmethod
    def _build_status_message(*, text: str, metadata: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """构建标准状态消息对象。

        Args:
            text: 输入文本。
            metadata: 附加元数据。

        Returns:
            dict[str, Any]: 状态消息对象。
        """
        return {
            "messageId": f"msg-{uuid.uuid4().hex}",
            "role": "agent",
            "kind": "message",
            "parts": [{"kind": "text", "text": str(text or "")}],
            "metadata": metadata if isinstance(metadata, dict) else {},
        }

    async def _append_event(self, task_id: str, event: dict[str, Any]) -> None:
        """向任务事件列表追加事件。

        Args:
            task_id: 任务 ID。
            event: 事件字典。

        Returns:
            None: 无返回值。
        """
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            events_obj = task.get("events")
            events = events_obj if isinstance(events_obj, list) else []
            events.append(
                {
                    "ts": int(time.time() * 1000),
                    "event": str(event.get("event") or ""),
                    "detail": str(event.get("detail") or ""),
                    "tool_name": str(event.get("tool_name") or ""),
                    "metadata": event.get("metadata") if isinstance(event.get("metadata"), dict) else {},
                }
            )
            task["events"] = events

    async def _set_status(
        self,
        task_id: str,
        *,
        state: str,
        text: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        """更新任务状态。

        Args:
            task_id: 任务 ID。
            state: 任务状态字符串。
            text: 输入文本。
            metadata: 附加元数据。

        Returns:
            None: 无返回值。
        """
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            task["status"] = {
                "state": str(state or "running"),
                "message": self._build_status_message(text=text, metadata=metadata or {}),
                "updatedAt": int(time.time() * 1000),
            }

    async def _is_task_marked_canceled(self, task_id: str) -> bool:
        """判断任务是否已被标记取消。

        Args:
            task_id: 任务 ID。

        Returns:
            bool: 是否已取消。
        """
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return True
            status_obj = task.get("status")
            status = status_obj if isinstance(status_obj, dict) else {}
            state = str(status.get("state") or "").strip().lower()
            if state == "canceled":
                return True
            cancel_event = task.get("cancel_event")
            return bool(cancel_event.is_set()) if isinstance(cancel_event, asyncio.Event) else False

    def _build_artifacts(self, result: dict[str, Any], progress_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """根据执行结果构建任务产物列表。

        Args:
            result: 任务执行结果字典。
            progress_events: 采集到的进度事件列表。

        Returns:
            list[dict[str, Any]]: 产物列表。
        """
        output = str(result.get("output") or "")
        output_type = str(result.get("output_type") or "")
        confidence = float(result.get("confidence") or 0.0)

        tool_observations_obj = result.get("tool_observations")
        tool_observations = tool_observations_obj if isinstance(tool_observations_obj, list) else []
        telemetry_obj = result.get("telemetry")
        telemetry = telemetry_obj if isinstance(telemetry_obj, dict) else {}

        evidence_summary_obj = result.get("evidence_summary")
        evidence_summary = evidence_summary_obj if isinstance(evidence_summary_obj, dict) else {}
        processing_steps_obj = result.get("processing_steps")
        processing_steps = processing_steps_obj if isinstance(processing_steps_obj, list) else []

        return [
            {
                "artifactId": f"art-{uuid.uuid4().hex}",
                "name": "final_answer",
                "parts": [{"kind": "text", "text": output}],
                "metadata": {
                    "output_type": output_type,
                    "confidence": confidence,
                },
            },
            {
                "artifactId": f"art-{uuid.uuid4().hex}",
                "name": "tool_observations",
                "parts": [{"kind": "data", "data": tool_observations}],
                "metadata": {"count": len(tool_observations)},
            },
            {
                "artifactId": f"art-{uuid.uuid4().hex}",
                "name": "telemetry",
                "parts": [{"kind": "data", "data": telemetry}],
                "metadata": {},
            },
            {
                "artifactId": f"art-{uuid.uuid4().hex}",
                "name": "progress_events",
                "parts": [{"kind": "data", "data": progress_events}],
                "metadata": {"count": len(progress_events)},
            },
            {
                "artifactId": f"art-{uuid.uuid4().hex}",
                "name": "evidence_summary",
                "parts": [{"kind": "data", "data": evidence_summary}],
                "metadata": {},
            },
            {
                "artifactId": f"art-{uuid.uuid4().hex}",
                "name": "processing_steps",
                "parts": [{"kind": "data", "data": processing_steps}],
                "metadata": {"count": len(processing_steps)},
            },
        ]

    async def _run_task(self, task_id: str) -> None:
        """执行单个任务主流程。

        Args:
            task_id: 任务 ID。

        Returns:
            None: 无返回值。
        """
        progress_events: list[dict[str, Any]] = []

        def _progress_callback(event: dict[str, Any]) -> None:
            """收集并转发进度事件。

            Args:
                event: 事件字典。

            Returns:
                None: 无返回值。
            """
            row = event if isinstance(event, dict) else {}
            progress_events.append(row)
            asyncio.create_task(self._append_event(task_id, row))

        def _cancel_check() -> bool:
            """查询任务取消状态。

            Returns:
                bool: True 表示应取消任务。
            """
            task = self._tasks.get(task_id)
            if not task:
                return True
            cancel_event = task.get("cancel_event")
            return bool(cancel_event.is_set()) if isinstance(cancel_event, asyncio.Event) else False

        try:
            await self._set_status(task_id, state="running", text="task_running")
            async with self._lock:
                task = self._tasks.get(task_id)
                if not task:
                    return
                message_obj = task.get("message")
                message = message_obj if isinstance(message_obj, dict) else {}
                context_id = str(task.get("contextId") or "")
                user_id = str(task.get("userId") or "")
                metadata_obj = task.get("metadata")
                metadata = metadata_obj if isinstance(metadata_obj, dict) else {}

            parsed = RequestContextParser.parse_message(message)
            query_kwargs = {
                "input_text": parsed.text,
                "context_id": context_id,
                "user_id": user_id,
                "metadata": metadata,
                "cancel_check": _cancel_check,
                "progress_callback": _progress_callback,
            }

            result_obj = self._run_query(**query_kwargs)
            if inspect.isawaitable(result_obj):
                result = await result_obj
            else:
                result = result_obj

            if await self._is_task_marked_canceled(task_id):
                await self._set_status(task_id, state="canceled", text="task_canceled")
                await self._append_event(
                    task_id,
                    {
                        "event": "task_canceled",
                        "detail": "task canceled",
                        "tool_name": "a2a",
                        "metadata": {},
                    },
                )
                return

            parsed_result = result if isinstance(result, dict) else {}
            artifacts = self._build_artifacts(parsed_result, progress_events)
            output = str(parsed_result.get("output") or "")
            output_type = str(parsed_result.get("output_type") or "")
            confidence = float(parsed_result.get("confidence") or 0.0)

            async with self._lock:
                task = self._tasks.get(task_id)
                if not task:
                    return
                task["artifacts"] = artifacts
                task["status"] = {
                    "state": "completed",
                    "message": self._build_status_message(
                        text="task_completed",
                        metadata={
                            "output": output,
                            "output_type": output_type,
                            "confidence": confidence,
                        },
                    ),
                    "updatedAt": int(time.time() * 1000),
                }

            await self._append_event(
                task_id,
                {
                    "event": "task_completed",
                    "detail": "task completed",
                    "tool_name": "a2a",
                    "metadata": {
                        "output_type": output_type,
                        "confidence": confidence,
                    },
                },
            )

        except asyncio.CancelledError:
            await self._set_status(task_id, state="canceled", text="task_canceled")
            await self._append_event(
                task_id,
                {
                    "event": "task_canceled",
                    "detail": "task canceled by cancellation",
                    "tool_name": "a2a",
                    "metadata": {},
                },
            )
            raise
        except Exception as exc:
            logger.exception(
                "a2a_task_failed: task_id=%s error_type=%s error=%s",
                task_id,
                type(exc).__name__,
                exc,
            )
            await self._set_status(
                task_id,
                state="failed",
                text="task_failed",
                metadata={"error": str(exc), "error_type": type(exc).__name__},
            )
            await self._append_event(
                task_id,
                {
                    "event": "task_failed",
                    "detail": str(exc),
                    "tool_name": "a2a",
                    "metadata": {"error_type": type(exc).__name__},
                },
            )

    async def asend_message(self, message: dict[str, Any], trace_id: str = "") -> dict[str, Any]:
        """提交消息并创建异步任务。

        Args:
            message: A2A 协议消息对象。
            trace_id: 请求追踪 ID。

        Returns:
            dict[str, Any]: 新任务信息。
        """
        message_obj = message if isinstance(message, dict) else {}
        metadata_obj = message_obj.get("metadata")
        metadata = metadata_obj if isinstance(metadata_obj, dict) else {}

        task_id = self._new_task_id()
        now_ms = int(time.time() * 1000)
        context_id = str(message_obj.get("contextId") or metadata.get("context_id") or "")
        user_id = str(message_obj.get("userId") or metadata.get("user_id") or "")

        task_payload = {
            "id": task_id,
            "contextId": context_id,
            "userId": user_id,
            "metadata": {
                **metadata,
                "trace_id": str(trace_id or metadata.get("trace_id") or ""),
            },
            "message": copy.deepcopy(message_obj),
            "status": {
                "state": "submitted",
                "message": self._build_status_message(text="task_submitted", metadata={}),
                "updatedAt": now_ms,
            },
            "events": [
                {
                    "ts": now_ms,
                    "event": "task_submitted",
                    "detail": "task submitted",
                    "tool_name": "a2a",
                    "metadata": {},
                }
            ],
            "artifacts": [],
            "createdAt": now_ms,
            "updatedAt": now_ms,
            "cancel_event": asyncio.Event(),
            "task_loop": asyncio.get_running_loop(),
        }

        async_task = asyncio.create_task(self._run_task(task_id), name=f"a2a-task-{task_id}")
        task_payload["async_task"] = async_task

        async with self._lock:
            self._tasks[task_id] = task_payload

        return self._public_task_payload(task_payload)

    async def aget_task(self, task_id: str) -> Optional[dict[str, Any]]:
        """获取指定任务详情。

        Args:
            task_id: 任务 ID。

        Returns:
            Optional[dict[str, Any]]: 任务详情或 None。
        """
        safe_task_id = str(task_id or "").strip()
        async with self._lock:
            task = self._tasks.get(safe_task_id)
            if not task:
                return None
            return self._public_task_payload(task, include_events=True)

    async def alist_tasks(self, context_id: str = "", limit: int = 20) -> list[dict[str, Any]]:
        """列出任务列表。

        Args:
            context_id: 上下文 ID。
            limit: 结果数量上限。

        Returns:
            list[dict[str, Any]]: 任务列表。
        """
        safe_limit = max(1, min(int(limit or 20), 500))
        safe_context = str(context_id or "").strip()
        async with self._lock:
            rows = list(self._tasks.values())
        if safe_context:
            rows = [row for row in rows if str(row.get("contextId") or "") == safe_context]
        rows.sort(
            key=lambda item: int(((item.get("status") or {}).get("updatedAt") or item.get("updatedAt") or 0)),
            reverse=True,
        )
        return [self._public_task_payload(row) for row in rows[:safe_limit]]

    async def aget_task_updates(self, task_id: str, cursor: int = 0) -> Optional[dict[str, Any]]:
        """按游标拉取任务增量事件。

        Args:
            task_id: 任务 ID。
            cursor: 事件游标位置。

        Returns:
            Optional[dict[str, Any]]: 增量事件快照。
        """
        safe_task_id = str(task_id or "").strip()
        safe_cursor = max(0, int(cursor or 0))

        async with self._lock:
            task = self._tasks.get(safe_task_id)
            if not task:
                return None
            events_obj = task.get("events")
            events = events_obj if isinstance(events_obj, list) else []
            sliced = events[safe_cursor:]
            status_obj = task.get("status")
            status = status_obj if isinstance(status_obj, dict) else {}
            state = str(status.get("state") or "")
            is_terminal = state.lower() in TerminalStates
            task_payload = self._public_task_payload(task, include_events=True)

        return {
            "taskId": safe_task_id,
            "state": state,
            "cursor": safe_cursor,
            "next_cursor": safe_cursor + len(sliced),
            "events": copy.deepcopy(sliced),
            "terminal": is_terminal,
            "task": task_payload if is_terminal else None,
        }

    async def acancel_task(self, task_id: str, reason: str = "manual_cancel") -> dict[str, Any]:
        """取消指定任务。

        Args:
            task_id: 任务 ID。
            reason: 取消或失败原因。

        Returns:
            dict[str, Any]: 取消操作结果。
        """
        safe_task_id = str(task_id or "").strip()
        if not safe_task_id:
            return {"ok": False, "error": "task_id_required"}

        async with self._lock:
            task = self._tasks.get(safe_task_id)
            if not task:
                return {"ok": False, "error": "task_not_found", "task_id": safe_task_id}
            status_obj = task.get("status")
            status = status_obj if isinstance(status_obj, dict) else {}
            state = str(status.get("state") or "").strip().lower()
            if state in TerminalStates:
                return {"ok": False, "error": "task_already_terminal", "task": self._public_task_payload(task)}
            cancel_event = task.get("cancel_event")
            async_task = task.get("async_task")

        if isinstance(cancel_event, asyncio.Event):
            cancel_event.set()
        if isinstance(async_task, asyncio.Task) and not async_task.done():
            async_task.cancel()

        await self._set_status(
            safe_task_id,
            state="canceled",
            text="task_canceled",
            metadata={"reason": str(reason or "manual_cancel")},
        )
        await self._append_event(
            safe_task_id,
            {
                "event": "task_canceled",
                "detail": str(reason or "manual_cancel"),
                "tool_name": "a2a",
                "metadata": {},
            },
        )

        task_payload = await self.aget_task(safe_task_id)
        return {"ok": True, "task_id": safe_task_id, "task": task_payload}

    def _build_stream_info(self, task_id: str, cursor: int = 0) -> dict[str, Any]:
        """构建事件流订阅信息。

        Args:
            task_id: 任务 ID。
            cursor: 事件游标位置。

        Returns:
            dict[str, Any]: 事件流地址与游标。
        """
        safe_task_id = str(task_id or "").strip()
        safe_cursor = max(0, int(cursor or 0))
        return {"url": f"{self._events_base_path}/{safe_task_id}/events?cursor={safe_cursor}", "cursor": safe_cursor}

    @staticmethod
    def _rpc_ok(rpc_id: Any, result: dict[str, Any]) -> dict[str, Any]:
        """构建 JSON-RPC 成功响应。

        Args:
            rpc_id: JSON-RPC 请求 ID。
            result: 任务执行结果字典。

        Returns:
            dict[str, Any]: JSON-RPC 成功响应对象。
        """
        return {"jsonrpc": "2.0", "id": rpc_id, "result": result}

    @staticmethod
    def _rpc_error(rpc_id: Any, code: int, message: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        """构建 JSON-RPC 错误响应。

        Args:
            rpc_id: JSON-RPC 请求 ID。
            code: JSON-RPC 错误码。
            message: A2A 协议消息对象。
            data: 请求体数据字典。

        Returns:
            dict[str, Any]: JSON-RPC 错误响应对象。
        """
        return {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "error": {
                "code": int(code),
                "message": str(message or "error"),
                "data": data if isinstance(data, dict) else {},
            },
        }

    async def ahandle_rpc(self, data: dict[str, Any], trace_id: str = "") -> dict[str, Any]:
        """处理 A2A JSON-RPC 请求。

        Args:
            data: 请求体数据字典。
            trace_id: 请求追踪 ID。

        Returns:
            dict[str, Any]: RPC 处理结果。
        """
        payload = data if isinstance(data, dict) else {}
        rpc_id = payload.get("id")
        method = str(payload.get("method") or "").strip()
        params_obj = payload.get("params")
        params = params_obj if isinstance(params_obj, dict) else {}

        try:
            if method in {"message/send", "message/stream"}:
                message_obj = params.get("message")
                message = message_obj if isinstance(message_obj, dict) else {}
                task = await self.asend_message(message, trace_id)
                task_id = str(task.get("id") or "").strip()
                result: dict[str, Any] = {"task": task}
                if method == "message/stream":
                    result["stream"] = self._build_stream_info(task_id, cursor=0)
                return self._rpc_ok(rpc_id, result)

            if method == "tasks/get":
                task_id = str(params.get("taskId") or "").strip()
                stored_task = await self.aget_task(task_id)
                if not stored_task:
                    return self._rpc_error(rpc_id, -32004, "task_not_found", {"taskId": task_id})
                return self._rpc_ok(rpc_id, {"task": stored_task})

            if method == "tasks/resubscribe":
                task_id = str(params.get("taskId") or "").strip()
                cursor = int(params.get("cursor") or 0)
                stored_task = await self.aget_task(task_id)
                if not stored_task:
                    return self._rpc_error(rpc_id, -32004, "task_not_found", {"taskId": task_id})
                return self._rpc_ok(
                    rpc_id,
                    {
                        "task": stored_task,
                        "stream": self._build_stream_info(task_id, cursor=cursor),
                    },
                )

            if method == "tasks/cancel":
                task_id = str(params.get("taskId") or "").strip()
                reason = str(params.get("reason") or "manual_cancel")
                result = await self.acancel_task(task_id, reason)
                return self._rpc_ok(rpc_id, result)

            if method == "tasks/list":
                context_id = str(params.get("contextId") or "").strip()
                limit = int(params.get("limit") or 20)
                rows = await self.alist_tasks(context_id=context_id, limit=limit)
                return self._rpc_ok(rpc_id, {"tasks": rows})

            return self._rpc_error(rpc_id, -32601, "method_not_found", {"method": method})
        except Exception as exc:
            logger.exception(
                "a2a_rpc_failed: method=%s trace_id=%s error_type=%s error=%s",
                method,
                str(trace_id or "").strip(),
                type(exc).__name__,
                exc,
            )
            return self._rpc_error(
                rpc_id,
                -32000,
                "internal_error",
                {"detail": str(exc), "method": method, "trace_id": str(trace_id or "").strip()},
            )

    async def ashutdown(self) -> None:
        """关闭服务并取消未完成任务。

        Returns:
            None: 无返回值。
        """
        async with self._lock:
            rows = list(self._tasks.values())
        for row in rows:
            async_task = row.get("async_task")
            if isinstance(async_task, asyncio.Task) and not async_task.done():
                async_task.cancel()
        if rows:
            await asyncio.gather(
                *[row["async_task"] for row in rows if isinstance(row.get("async_task"), asyncio.Task)],
                return_exceptions=True,
            )
