"""KG 子代理图编排。"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from app.a2a.mcp.tool_gateway import MCPToolGateway
from app.a2a.subagents.kg.nodes import (
    node_build_response,
    node_execute_tools,
    node_generate_query_statements,
    node_normalize_results,
    node_validate_constraints,
)
from app.a2a.subagents.kg.state import KgSubAgentState

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

    async def ainvoke(self, state: KgSubAgentState) -> KgSubAgentState:
        """执行 ainvoke。
        
        Args:
            state: 参数。
        
        Returns:
            执行结果。
        """
        state = await node_validate_constraints(state)
        state = await node_generate_query_statements(state)
        state = await node_execute_tools(state, self.tool_gateway)
        state = await node_normalize_results(state)
        state = await node_build_response(state)
        return state


def build_kg_graph(tool_gateway: MCPToolGateway) -> Any:
    """构建 kg_graph。
    
    Args:
        tool_gateway: 参数。
    
    Returns:
        执行结果。
    """
    if not _LANGGRAPH_AVAILABLE:
        return None

    async def _execute_tools(state: KgSubAgentState) -> KgSubAgentState:
        """执行 _execute_tools 内部逻辑。
        
        Args:
            state: 参数。
        
        Returns:
            执行结果。
        """
        return await node_execute_tools(state, tool_gateway)

    builder: StateGraph = StateGraph(KgSubAgentState)
    builder.add_node("validate_constraints", node_validate_constraints)
    builder.add_node("generate_query_statements", node_generate_query_statements)
    builder.add_node("execute_tools", _execute_tools)
    builder.add_node("normalize_results", node_normalize_results)
    builder.add_node("build_response", node_build_response)

    builder.set_entry_point("validate_constraints")
    builder.add_edge("validate_constraints", "generate_query_statements")
    builder.add_edge("generate_query_statements", "execute_tools")
    builder.add_edge("execute_tools", "normalize_results")
    builder.add_edge("normalize_results", "build_response")
    builder.add_edge("build_response", END)
    return builder


def compile_kg_graph(tool_gateway: MCPToolGateway) -> Any:
    """执行 compile_kg_graph。
    
    Args:
        tool_gateway: 参数。
    
    Returns:
        执行结果。
    """
    graph = build_kg_graph(tool_gateway)
    if graph is None:
        return _FallbackGraphRunner(tool_gateway)
    return graph.compile()


async def run_kg_graph(compiled_graph: Any, state: KgSubAgentState) -> KgSubAgentState:
    """执行 kg_graph。
    
    Args:
        compiled_graph: 参数。
        state: 参数。
    
    Returns:
        执行结果。
    """
    ainvoke: Callable[[KgSubAgentState], Awaitable[KgSubAgentState]] = getattr(compiled_graph, "ainvoke")
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
