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
        queries_used_obj = first_item.get("queries_used")
        queries_used = queries_used_obj if isinstance(queries_used_obj, list) else []
        query_records_obj = first_item.get("query_records")
        query_records = query_records_obj if isinstance(query_records_obj, list) else []
        tool_results_obj = first_item.get("tool_results")
        tool_results = tool_results_obj if isinstance(tool_results_obj, list) else []
        evidence_obj = first_item.get("evidence")
        evidence = evidence_obj if isinstance(evidence_obj, list) else []

        signal = str(first_item.get("signal") or "").strip()
        coverage_obj = first_item.get("coverage")
        coverage = coverage_obj if isinstance(coverage_obj, dict) else {}
        missing_points_obj = first_item.get("missing_points")
        missing_points = missing_points_obj if isinstance(missing_points_obj, list) else []
        direct_support = bool(first_item.get("direct_support"))
        suggested_next_route = str(first_item.get("suggested_next_route") or "").strip()

        no_signal = bool(first_item.get("no_signal"))
        reason = str(first_item.get("reason") or "")
        token_usage_obj = first_item.get("token_usage")
        token_usage = token_usage_obj if isinstance(token_usage_obj, dict) else {}

        if not signal:
            signal = "none" if no_signal else ("weak" if evidence else "none")
        if not coverage:
            evidence_count = len(evidence)
            coverage = {
                "coverage_status": "none" if evidence_count == 0 else "partial",
                "coverage_ratio": 0.0 if evidence_count == 0 else min(1.0, round(float(evidence_count) / 3.0, 4)),
                "evidence_count": evidence_count,
            }

        if not queries_used:
            queries_used = []
            for statement in query_statements:
                if not isinstance(statement, dict):
                    continue
                payload_obj = statement.get("payload")
                payload = payload_obj if isinstance(payload_obj, dict) else {}
                query = str(payload.get("query") or payload.get("keyword") or payload.get("cypher") or "").strip()
                if query:
                    queries_used.append(query)

        return SubAgentResult(
            subtask_id=task.subtask_id,
            target_agent=task.target_agent,
            question=task.question,
            query_statements=query_statements,
            queries_used=[str(item) for item in queries_used],
            query_records=query_records,
            tool_results=tool_results,
            evidence=evidence,
            signal=signal,
            coverage=coverage,
            missing_points=[str(item) for item in missing_points],
            direct_support=direct_support,
            suggested_next_route=suggested_next_route,
            no_signal=no_signal,
            reason=reason,
            token_usage=token_usage,
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
            "query_focus": task.query_focus,
            "query_terms": task.query_terms,
            "query_directions": task.query_directions,
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
