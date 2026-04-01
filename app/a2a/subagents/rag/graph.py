"""graph 模块。

提供 graph 相关能力与对外接口。
"""


from __future__ import annotations

from typing import Any, Awaitable, Callable

from app.a2a.mcp.tool_gateway import MCPToolGateway
from app.a2a.subagents.rag.nodes import (
    node_build_response,
    node_execute_rag_search,
    node_generate_query_statement,
    node_normalize_results,
    node_validate_constraints,
)
from app.a2a.subagents.rag.state import RagSubAgentState

try:
    from langgraph.graph import END, StateGraph

    _LANGGRAPH_AVAILABLE = True
except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError):
    END = "__end__"
    StateGraph = None
    _LANGGRAPH_AVAILABLE = False


class _FallbackGraphRunner:
    def __init__(self, tool_gateway: MCPToolGateway):
        """初始化 _FallbackGraphRunner 实例。
        
        Args:
            tool_gateway: 参数。
        """
        self.tool_gateway = tool_gateway

    async def ainvoke(self, state: RagSubAgentState) -> RagSubAgentState:
        """执行 ainvoke。
        
        Args:
            state: 参数。
        
        Returns:
            执行结果。
        """
        state = await node_validate_constraints(state)
        state = await node_generate_query_statement(state)
        state = await node_execute_rag_search(state, self.tool_gateway)
        state = await node_normalize_results(state)
        state = await node_build_response(state)
        return state


def build_rag_graph(tool_gateway: MCPToolGateway) -> Any:
    """构建 rag_graph。
    
    Args:
        tool_gateway: 参数。
    
    Returns:
        执行结果。
    """
    if not _LANGGRAPH_AVAILABLE:
        return None

    async def _execute_rag_search(state: RagSubAgentState) -> RagSubAgentState:
        """执行 _execute_rag_search 内部逻辑。
        
        Args:
            state: 参数。
        
        Returns:
            执行结果。
        """
        return await node_execute_rag_search(state, tool_gateway)

    builder: StateGraph = StateGraph(RagSubAgentState)
    builder.add_node("validate_constraints", node_validate_constraints)
    builder.add_node("generate_query_statement", node_generate_query_statement)
    builder.add_node("execute_rag_search", _execute_rag_search)
    builder.add_node("normalize_results", node_normalize_results)
    builder.add_node("build_response", node_build_response)

    builder.set_entry_point("validate_constraints")
    builder.add_edge("validate_constraints", "generate_query_statement")
    builder.add_edge("generate_query_statement", "execute_rag_search")
    builder.add_edge("execute_rag_search", "normalize_results")
    builder.add_edge("normalize_results", "build_response")
    builder.add_edge("build_response", END)
    return builder


def compile_rag_graph(tool_gateway: MCPToolGateway) -> Any:
    """执行 compile_rag_graph。
    
    Args:
        tool_gateway: 参数。
    
    Returns:
        执行结果。
    """
    graph = build_rag_graph(tool_gateway)
    if graph is None:
        return _FallbackGraphRunner(tool_gateway)
    return graph.compile()


async def run_rag_graph(compiled_graph: Any, state: RagSubAgentState) -> RagSubAgentState:
    """执行 rag_graph。
    
    Args:
        compiled_graph: 参数。
        state: 参数。
    
    Returns:
        执行结果。
    """
    ainvoke: Callable[[RagSubAgentState], Awaitable[RagSubAgentState]] = getattr(compiled_graph, "ainvoke")
    output_keys = list(
        dict.fromkeys(
            [
                *list(state.keys()),
                "query_statements",
                "tool_calls",
                "tool_results",
                "evidence",
                "no_signal",
                "reason",
                "telemetry",
                "final_result",
            ]
        )
    )
    try:
        output = await ainvoke(state, output_keys=output_keys)
    except TypeError:
        output = await ainvoke(state)
    return output if isinstance(output, dict) else state
