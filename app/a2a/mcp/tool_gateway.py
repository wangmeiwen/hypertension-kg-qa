"""子代理 MCP 工具调用网关。"""

from __future__ import annotations

from typing import Any

from app.a2a.models import ToolCallResult
from app.mcp.tools import get_mcp_server


_AGENT_TOOL_WHITELIST: dict[str, set[str]] = {
    "kg": {"kg_query", "kg_entity_relation_search"},
    "rag": {"rag_search"},
    "orchestrator": set(),
}


class MCPToolGateway:
    """封装子代理对 MCP 的调用和白名单校验。"""

    def __init__(self, mcp_server: Any | None = None, agent_type: str = "orchestrator"):
        self.mcp_server = mcp_server if mcp_server is not None else get_mcp_server()
        self.agent_type = str(agent_type or "orchestrator").strip().lower()

    def ensure_allowed_tools(self, tool_name: str) -> None:
        name = str(tool_name or "").strip()
        allowed = _AGENT_TOOL_WHITELIST.get(self.agent_type, set())
        if not allowed:
            return
        if name not in allowed:
            raise PermissionError(f"tool_not_allowed:{self.agent_type}:{name}")

    @staticmethod
    def normalize_mcp_result(tool_name: str, raw: Any) -> dict[str, Any]:
        row = raw if isinstance(raw, dict) else {}
        ok = bool(row.get("ok"))
        result_obj = row.get("result")
        result = result_obj if isinstance(result_obj, dict) else {}
        error = str(row.get("error") or "")
        meta_obj = row.get("meta")
        meta = meta_obj if isinstance(meta_obj, dict) else {}
        model = ToolCallResult(ok=ok, tool_name=tool_name, result=result, error=error, meta=meta)
        return model.model_dump()

    async def invoke_tool(self, tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.ensure_allowed_tools(tool_name)
        rows = await self.mcp_server.ainvoke_batch(
            [
                {
                    "tool_name": str(tool_name or ""),
                    "payload": payload if isinstance(payload, dict) else {},
                }
            ]
        )
        first = rows[0] if isinstance(rows, list) and rows else {}
        return self.normalize_mcp_result(str(tool_name or ""), first)

    async def invoke_batch(
        self,
        calls: list[dict[str, Any]],
        allow_parallel: bool = True,
    ) -> list[dict[str, Any]]:
        safe_calls: list[dict[str, Any]] = []
        for row in calls:
            item = row if isinstance(row, dict) else {}
            tool_name = str(item.get("tool_name") or "").strip()
            self.ensure_allowed_tools(tool_name)
            payload_obj = item.get("payload")
            payload = payload_obj if isinstance(payload_obj, dict) else {}
            safe_calls.append({"tool_name": tool_name, "payload": payload})

        if not safe_calls:
            return []

        if allow_parallel:
            rows = await self.mcp_server.ainvoke_batch(safe_calls)
            results: list[dict[str, Any]] = []
            for idx, raw in enumerate(rows):
                tool_name = str(safe_calls[idx].get("tool_name") or "")
                results.append(self.normalize_mcp_result(tool_name, raw))
            return results

        results = []
        for row in safe_calls:
            results.append(await self.invoke_tool(str(row.get("tool_name") or ""), row.get("payload") or {}))
        return results
