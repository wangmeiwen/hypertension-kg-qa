"""graph 模块。

提供知识图谱子代理 LangGraph 拓扑构建、编译和降级运行能力。
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from app.a2a.tool_gateway import MCPToolGateway
from app.a2a.subagents.kg.nodes import (
    node_build_response,
    node_execute_tools,
    node_generate_query_statements,
    node_normalize_results,
    node_validate_constraints,
)
from app.a2a.subagents.retry_utils import should_continue_querying
from app.a2a.subagents.kg.state import KgSubAgentState

logger = logging.getLogger(__name__)

try:
    from langgraph.graph import END as _IMPORTED_LANGGRAPH_END
    from langgraph.graph import StateGraph as _ImportedLangGraphStateGraph

    _LANGGRAPH_END: Any = _IMPORTED_LANGGRAPH_END
    _state_graph_factory: Any = _ImportedLangGraphStateGraph
    _LANGGRAPH_AVAILABLE = True
except (
    RuntimeError,
    ValueError,
    TypeError,
    KeyError,
    AttributeError,
    OSError,
    ImportError,
) as exc:
    logger.warning(
        "LangGraph import failed for KG subagent, fallback runner enabled: error_type=%s error=%s",
        type(exc).__name__,
        exc,
        exc_info=True,
    )
    _LANGGRAPH_END = "__end__"
    _state_graph_factory = None
    _LANGGRAPH_AVAILABLE = False


class _FallbackGraphRunner:
    """在 LangGraph 不可用时串行执行 KG 子图节点。"""

    def __init__(self, tool_gateway: MCPToolGateway):
        """初始化串行降级执行器。

        Args:
            tool_gateway: 执行工具节点时使用的 MCP 网关。
        """
        self.tool_gateway = tool_gateway

    async def ainvoke(self, state: KgSubAgentState) -> KgSubAgentState:
        """按 LangGraph 节点顺序串行执行 KG 子代理状态机。

        Args:
            state: KG 子代理当前状态。

        Returns:
            完成校验、查询生成、工具调用、结果归一化后的状态。
        """
        state = await node_validate_constraints(state)
        while True:
            state = await node_generate_query_statements(state)
            state = await node_execute_tools(state, self.tool_gateway)
            state = await node_normalize_results(state)
            if not should_continue_querying(state):
                break
        state = await node_build_response(state)
        return state


def build_kg_graph(tool_gateway: MCPToolGateway) -> Any:
    """构建 KG 子代理 LangGraph 拓扑。

    Args:
        tool_gateway: 工具调用节点使用的 MCP 网关。

    Returns:
        未编译的 StateGraph；LangGraph 不可用时返回 None。
    """
    if not _LANGGRAPH_AVAILABLE:
        return None

    async def _execute_tools(state: KgSubAgentState) -> KgSubAgentState:
        """闭包绑定 MCP 网关后执行工具调用节点。

        Args:
            state: KG 子代理当前状态。

        Returns:
            写入工具调用结果后的状态。
        """
        return await node_execute_tools(state, tool_gateway)

    builder = _state_graph_factory(KgSubAgentState)
    builder.add_node("validate_constraints", node_validate_constraints)
    builder.add_node("generate_query_statements", node_generate_query_statements)
    builder.add_node("execute_tools", _execute_tools)
    builder.add_node("normalize_results", node_normalize_results)
    builder.add_node("build_response", node_build_response)

    builder.set_entry_point("validate_constraints")
    builder.add_edge("validate_constraints", "generate_query_statements")
    builder.add_edge("generate_query_statements", "execute_tools")
    builder.add_edge("execute_tools", "normalize_results")
    builder.add_conditional_edges(
        "normalize_results",
        lambda state: "continue" if should_continue_querying(state) else "done",
        {
            "continue": "generate_query_statements",
            "done": "build_response",
        },
    )
    builder.add_edge("build_response", _LANGGRAPH_END)
    return builder


def compile_kg_graph(tool_gateway: MCPToolGateway) -> Any:
    """编译 KG 子代理执行图，必要时返回降级执行器。

    Args:
        tool_gateway: 工具调用节点使用的 MCP 网关。

    Returns:
        可通过 ainvoke 运行的图执行对象。
    """
    graph = build_kg_graph(tool_gateway)
    if graph is None:
        return _FallbackGraphRunner(tool_gateway)
    return graph.compile()


async def run_kg_graph(compiled_graph: Any, state: KgSubAgentState) -> KgSubAgentState:
    """运行已编译的 KG 子代理执行图。

    Args:
        compiled_graph: compile_kg_graph 返回的图执行对象。
        state: KG 子代理初始状态。

    Returns:
        图执行后的最终状态；异常兼容分支下至少返回原始状态。
    """
    ainvoke: Callable[[KgSubAgentState], Awaitable[KgSubAgentState]] = getattr(
        compiled_graph, "ainvoke"
    )
    output = await ainvoke(state)
    return output if isinstance(output, dict) else state
