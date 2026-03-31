"""RAG 子代理（兼容层）。"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from app.a2a.mcp.tool_gateway import MCPToolGateway
from app.a2a.subagents.rag.executor import RagSubAgentExecutor


class RagSubAgent:
    """检索子代理。

    职责：仅处理收到的问题，生成并执行 rag_search，不做额外补检索。
    """

    def __init__(self, mcp_server: Any):
        """初始化 RagSubAgent 实例。
        
        Args:
            mcp_server: 参数。
        """
        self.tool_gateway = MCPToolGateway(mcp_server=mcp_server, agent_type="rag")
        self.executor = RagSubAgentExecutor(self.tool_gateway)

    async def _run_single_request(self, idx: int, row: dict[str, Any]) -> dict[str, Any]:
        """执行 _run_single_request 内部逻辑。
        
        Args:
            idx: 参数。
            row: 参数。
        
        Returns:
            执行结果。
        """
        req = row if isinstance(row, dict) else {}
        subquery_id = int(req.get("subquery_id") or idx)
        subquery = str(req.get("subquery") or "").strip()
        arguments_obj = req.get("arguments")
        arguments = arguments_obj if isinstance(arguments_obj, dict) else {}

        item = {
            "subquery_id": subquery_id,
            "subquery": subquery,
            "tool_name": "rag_search",
            "arguments": arguments,
        }

        try:
            result = await self.executor.run_request(
                {
                    "subquery_id": subquery_id,
                    "subquery": subquery,
                    "arguments": arguments,
                    "constraints": {"allow_extra_retrieval": False},
                }
            )
            evidence_obj = result.get("evidence")
            evidence = evidence_obj if isinstance(evidence_obj, list) else []
            no_signal = bool(result.get("no_signal", len(evidence) == 0))

            item.update(
                {
                    "ok": True,
                    "query_statements": result.get("query_statements") if isinstance(result.get("query_statements"), list) else [],
                    "tool_results": result.get("tool_results") if isinstance(result.get("tool_results"), list) else [],
                    "evidence": evidence,
                    "no_signal": no_signal,
                    "reason": str(result.get("reason") or "ok"),
                    "telemetry": result.get("telemetry") if isinstance(result.get("telemetry"), dict) else {},
                }
            )
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            item.update(
                {
                    "ok": False,
                    "query_statements": [],
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
        """调用 batch。
        
        Args:
            requests: 参数。
            user_query: 参数。
            session_id: 参数。
            user_id: 参数。
            reason: 参数。
        
        Returns:
            执行结果。
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

        note = f"Retrieval 子代理完成 {total_requests} 个检索任务"
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
