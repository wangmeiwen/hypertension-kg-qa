"""executor 模块。

提供知识图谱子代理 LangGraph 的执行器封装。
"""


from __future__ import annotations

from typing import Any

from app.a2a.mcp.tool_gateway import MCPToolGateway
from app.a2a.subagents.kg.graph import compile_kg_graph, run_kg_graph
from app.a2a.subagents.kg.state import KgSubAgentState


class KgSubAgentExecutor:
    """执行 KG 子代理 LangGraph。"""

    def __init__(self, tool_gateway: MCPToolGateway):
        """初始化图谱子代理执行图。

        Args:
            tool_gateway: 负责调用知识图谱 MCP 工具的网关。
        """
        self.tool_gateway = tool_gateway
        self.graph = compile_kg_graph(tool_gateway)

    async def run_request(self, request: dict[str, Any]) -> dict[str, Any]:
        """将单次图谱检索请求转换为状态并运行 LangGraph。

        Args:
            request: 子代理请求字典，包含子问题、工具名、工具参数和执行约束。

        Returns:
            图谱子代理最终标准化结果。
        """
        req = request if isinstance(request, dict) else {}
        state: KgSubAgentState = {
            "input_question": str(req.get("subquery") or req.get("question") or "").strip(),
            "subtask_id": int(req.get("subquery_id") or 1),
            "constraints": req.get("constraints") if isinstance(req.get("constraints"), dict) else {"allow_extra_retrieval": False, "max_tool_calls": 1},
            "tool_name": str(req.get("tool_name") or "kg_entity_relation_search").strip(),
            "arguments": req.get("arguments") if isinstance(req.get("arguments"), dict) else {},
            "query_terms": [str(item or "").strip() for item in req.get("query_terms")] if isinstance(req.get("query_terms"), list) else [],
        }
        output = await run_kg_graph(self.graph, state)
        final_obj = output.get("final_result")
        final_result = final_obj if isinstance(final_obj, dict) else {}
        return final_result
