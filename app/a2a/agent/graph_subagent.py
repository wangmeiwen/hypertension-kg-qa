"""graph_subagent 模块。

提供面向 A2A 编排层的知识图谱子代理适配入口。
"""


from __future__ import annotations

import asyncio
import time
from typing import Any

from app.a2a.tool_gateway import MCPToolGateway
from app.a2a.subagents.kg.executor import KgSubAgentExecutor


class GraphSubAgent:
    """图谱子代理。

    职责：处理收到的问题，生成并执行图谱检索语句，证据不足时最多补查 3 次。
    """

    def __init__(self, mcp_server: Any):
        """初始化图谱子代理依赖的 MCP 工具网关和执行器。

        Args:
            mcp_server: 提供知识图谱 MCP 工具调用能力的服务实例。
        """
        self.tool_gateway = MCPToolGateway(mcp_server=mcp_server, agent_type="kg")
        self.executor = KgSubAgentExecutor(self.tool_gateway)

    async def _run_single_request(self, idx: int, row: dict[str, Any]) -> dict[str, Any]:
        """执行单个图谱检索请求并标准化返回结构。

        Args:
            idx: 当前请求在批量任务中的序号，用作缺省子查询 ID。
            row: 主代理下发的子查询、工具名、参数和执行约束。

        Returns:
            包含执行状态、查询记录、证据、覆盖情况和错误信息的结果字典。
        """
        req = row if isinstance(row, dict) else {}
        subquery_id = int(req.get("subquery_id") or idx)
        subquery = str(req.get("subquery") or "").strip()
        tool_name = str(req.get("tool_name") or "kg_auto").strip() or "kg_auto"
        arguments_obj = req.get("arguments")
        arguments = arguments_obj if isinstance(arguments_obj, dict) else {}
        constraints_obj = req.get("constraints")
        constraints = constraints_obj if isinstance(constraints_obj, dict) else {"allow_extra_retrieval": False, "max_tool_calls": 3}

        item = {
            "subquery_id": subquery_id,
            "subquery": subquery,
            "tool_name": tool_name,
            "arguments": arguments,
            "constraints": constraints,
        }
        try:
            result = await self.executor.run_request(
                {
                    "subquery_id": subquery_id,
                    "subquery": subquery,
                    "tool_name": tool_name,
                    "arguments": arguments,
                    "constraints": constraints,
                }
            )
            evidence_obj = result.get("evidence")
            evidence = evidence_obj if isinstance(evidence_obj, list) else []
            no_signal = bool(result.get("no_signal", len(evidence) == 0))
            item.update(
                {
                    "ok": True,
                    "query_statements": result.get("query_statements") if isinstance(result.get("query_statements"), list) else [],
                    "queries_used": result.get("queries_used") if isinstance(result.get("queries_used"), list) else [],
                    "query_records": result.get("query_records") if isinstance(result.get("query_records"), list) else [],
                    "tool_results": result.get("tool_results") if isinstance(result.get("tool_results"), list) else [],
                    "evidence": evidence,
                    "signal": str(result.get("signal") or "none"),
                    "coverage": result.get("coverage") if isinstance(result.get("coverage"), dict) else {},
                    "missing_points": result.get("missing_points") if isinstance(result.get("missing_points"), list) else [],
                    "direct_support": bool(result.get("direct_support")),
                    "suggested_next_route": str(result.get("suggested_next_route") or ""),
                    "no_signal": no_signal,
                    "reason": str(result.get("reason") or "ok"),
                    "token_usage": result.get("token_usage") if isinstance(result.get("token_usage"), dict) else {},
                    "telemetry": result.get("telemetry") if isinstance(result.get("telemetry"), dict) else {},
                }
            )
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            item.update(
                {
                    "ok": False,
                    "query_statements": [],
                    "query_records": [],
                    "tool_results": [],
                    "evidence": [],
                    "no_signal": True,
                    "reason": f"error:{str(exc)}",
                    "error": str(exc),
                }
            )
        return item

    async def invoke_batch(
        self,
        *,
        requests: list[dict[str, Any]],
        user_query: str = "",
        session_id: str = "",
        user_id: str = "",
        reason: str = "",
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """并发执行一批图谱子任务并返回聚合结果。

        Args:
            requests: 主代理拆分出的图谱检索请求列表。
            user_query: 原始用户问题，当前保留用于后续审计和上下文扩展。
            session_id: 会话 ID，当前保留用于后续追踪。
            user_id: 用户 ID，当前保留用于后续追踪。
            reason: 本轮调度图谱子代理的原因，当前保留用于后续审计。

        Returns:
            二元组：第一个元素是符合工具响应格式的结果，第二个元素是耗时和数量统计。
        """
        _ = user_query
        _ = session_id
        _ = user_id
        _ = reason

        started = time.perf_counter()
        safe_requests = requests if isinstance(requests, list) else []

        tasks = [
            self._run_single_request(idx, row if isinstance(row, dict) else {})
            for idx, row in enumerate(safe_requests, start=1)
        ]
        items = await asyncio.gather(*tasks) if tasks else []

        total_requests = len(items)
        ok_count = sum(1 for item in items if bool(item.get("ok")))
        error_count = total_requests - ok_count
        signal_count = sum(1 for item in items if bool(item.get("ok")) and not bool(item.get("no_signal")))

        note = f"Graph 子代理完成 {total_requests} 个检索任务"
        summary = {
            "total_requests": total_requests,
            "ok_count": ok_count,
            "signal_count": signal_count,
            "error_count": error_count,
            "note": note,
        }
        result_payload = {
            "ok": True,
            "result": {
                "items": items,
                "summary": summary,
            },
        }
        meta = {
            "duration_ms": round((time.perf_counter() - started) * 1000.0, 2),
            "total_requests": total_requests,
            "ok_count": ok_count,
            "signal_count": signal_count,
        }
        return result_payload, meta
