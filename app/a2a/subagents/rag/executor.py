"""executor 模块。

提供 executor 相关能力与对外接口。
"""


from __future__ import annotations

from typing import Any

from app.a2a.mcp.tool_gateway import MCPToolGateway
from app.a2a.subagents.rag.graph import compile_rag_graph, run_rag_graph
from app.a2a.subagents.rag.state import RagSubAgentState


class RagSubAgentExecutor:
    """执行 RAG 子代理 LangGraph。"""

    def __init__(self, tool_gateway: MCPToolGateway):
        """初始化 RagSubAgentExecutor 实例。
        
        Args:
            tool_gateway: 参数。
        """
        self.tool_gateway = tool_gateway
        self.graph = compile_rag_graph(tool_gateway)

    async def run_request(self, request: dict[str, Any]) -> dict[str, Any]:
        """执行 request。
        
        Args:
            request: 参数。
        
        Returns:
            执行结果。
        """
        req = request if isinstance(request, dict) else {}
        state: RagSubAgentState = {
            "input_question": str(req.get("subquery") or req.get("question") or "").strip(),
            "subtask_id": int(req.get("subquery_id") or 1),
            "constraints": req.get("constraints") if isinstance(req.get("constraints"), dict) else {"allow_extra_retrieval": False, "max_tool_calls": 1},
            "tool_name": "rag_search",
            "arguments": req.get("arguments") if isinstance(req.get("arguments"), dict) else {},
        }
        output = await run_rag_graph(self.graph, state)
        final_obj = output.get("final_result")
        final_result = final_obj if isinstance(final_obj, dict) else {}
        return final_result
