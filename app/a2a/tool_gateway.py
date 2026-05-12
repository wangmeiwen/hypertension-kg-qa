"""A2A 子代理工具调用网关。"""

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
    """封装 A2A 子代理对核心 MCP 服务的调用和白名单校验。"""

    def __init__(self, mcp_server: Any | None = None, agent_type: str = "orchestrator"):
        """初始化 MCP 工具网关。

        若未显式传入 `mcp_server`，则自动使用全局 MCP server 单例。
        `agent_type` 决定后续工具白名单策略。

        Args:
            mcp_server: MCP 服务实例；为 None 时自动获取默认实例。
            agent_type: 子代理类型，如 kg/rag/orchestrator。

        Returns:
            None: 无返回值。
        """
        self.mcp_server = mcp_server if mcp_server is not None else get_mcp_server()
        self.agent_type = str(agent_type or "orchestrator").strip().lower()

    def ensure_allowed_tools(self, tool_name: str) -> None:
        """校验当前子代理是否允许调用指定工具。

        当代理类型配置了白名单且请求工具不在白名单中时，抛出 PermissionError。

        Args:
            tool_name: 待调用工具名。

        Returns:
            None: 校验通过时无返回值。
        """
        name = str(tool_name or "").strip()
        allowed = _AGENT_TOOL_WHITELIST.get(self.agent_type, set())
        if not allowed:
            return
        if name not in allowed:
            raise PermissionError(f"tool_not_allowed:{self.agent_type}:{name}")

    @staticmethod
    def normalize_mcp_result(tool_name: str, raw: Any) -> dict[str, Any]:
        """将原始 MCP 返回值标准化为 ToolCallResult 结构。

        Args:
            tool_name: 工具名。
            raw: MCP 原始返回对象，允许为任意类型。

        Returns:
            dict[str, Any]: 标准化后的工具调用结果字典。
        """
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
        """调用单个 MCP 工具并返回标准化结果。

        Args:
            tool_name: 工具名。
            payload: 工具入参字典。

        Returns:
            dict[str, Any]: 标准化后的单次工具调用结果。
        """
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
        """批量调用多个工具并返回结果列表。

        - `allow_parallel=True` 时走 MCP 批量并行接口；
        - `allow_parallel=False` 时按顺序串行调用。

        Args:
            calls: 调用列表，每项应包含 tool_name 与 payload。
            allow_parallel: 是否允许并行调用。

        Returns:
            list[dict[str, Any]]: 与输入顺序对齐的标准化结果列表。
        """
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
