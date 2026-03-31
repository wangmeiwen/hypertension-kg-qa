"""主代理到子代理调用客户端。"""

from __future__ import annotations

import asyncio
from typing import Any

from app.a2a.models import SubAgentResult, SubTask


class SubAgentClientFactory:
    """主代理子代理客户端。"""

    def __init__(self, graph_subagent: Any, rag_subagent: Any):
        """初始化 SubAgentClientFactory 实例。
        
        Args:
            graph_subagent: 参数。
            rag_subagent: 参数。
        """
        self.graph_subagent = graph_subagent
        self.rag_subagent = rag_subagent

    def normalize_subagent_response(
        self,
        *,
        task: SubTask,
        raw_result: dict[str, Any],
    ) -> SubAgentResult:
        """标准化 subagent_response。
        
        Args:
            task: 参数。
            raw_result: 参数。
        
        Returns:
            执行结果。
        """
        payload_obj = raw_result.get("result")
        payload = payload_obj if isinstance(payload_obj, dict) else {}
        items_obj = payload.get("items")
        items = items_obj if isinstance(items_obj, list) else []

        first_item = items[0] if items else {}
        if not isinstance(first_item, dict):
            first_item = {}

        query_statements_obj = first_item.get("query_statements")
        query_statements = query_statements_obj if isinstance(query_statements_obj, list) else []
        tool_results_obj = first_item.get("tool_results")
        tool_results = tool_results_obj if isinstance(tool_results_obj, list) else []
        evidence_obj = first_item.get("evidence")
        evidence = evidence_obj if isinstance(evidence_obj, list) else []

        no_signal = bool(first_item.get("no_signal"))
        reason = str(first_item.get("reason") or "")

        return SubAgentResult(
            subtask_id=task.subtask_id,
            target_agent=task.target_agent,
            question=task.question,
            query_statements=query_statements,
            tool_results=tool_results,
            evidence=evidence,
            no_signal=no_signal,
            reason=reason,
            telemetry={
                "raw_summary": payload.get("summary") if isinstance(payload.get("summary"), dict) else {},
            },
        )

    async def send_task(
        self,
        task: SubTask,
        serial_guard: asyncio.Semaphore | None = None,
    ) -> SubAgentResult:
        """执行 send_task。
        
        Args:
            task: 参数。
            serial_guard: 参数。
        
        Returns:
            执行结果。
        """
        request = {
            "subquery_id": task.subtask_id,
            "subquery": task.question,
            "tool_name": task.tool_name,
            "arguments": task.arguments,
            "reason": task.reason,
            "constraints": task.constraints.model_dump(),
        }

        guard = serial_guard
        if guard is None:
            if task.target_agent == "kg":
                raw_result, _ = await self.graph_subagent.invoke_batch(
                    requests=[request],
                    user_query=task.question,
                    session_id="",
                    user_id="",
                    reason=task.reason,
                )
            else:
                raw_result, _ = await self.rag_subagent.invoke_batch(
                    requests=[request],
                    user_query=task.question,
                    session_id="",
                    user_id="",
                    reason=task.reason,
                )
        else:
            async with guard:
                if task.target_agent == "kg":
                    raw_result, _ = await self.graph_subagent.invoke_batch(
                        requests=[request],
                        user_query=task.question,
                        session_id="",
                        user_id="",
                        reason=task.reason,
                    )
                else:
                    raw_result, _ = await self.rag_subagent.invoke_batch(
                        requests=[request],
                        user_query=task.question,
                        session_id="",
                        user_id="",
                        reason=task.reason,
                    )

        return self.normalize_subagent_response(
            task=task,
            raw_result=raw_result if isinstance(raw_result, dict) else {},
        )
