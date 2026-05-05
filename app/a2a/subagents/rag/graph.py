"""graph 模块。

提供 RAG 子代理 LangGraph 拓扑构建、编译和降级运行能力。
"""


from __future__ import annotations

import logging
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

logger = logging.getLogger(__name__)

try:
    from langgraph.graph import END, StateGraph

    _LANGGRAPH_AVAILABLE = True
except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
    logger.warning(
        "LangGraph import failed for RAG subagent, fallback runner enabled: error_type=%s error=%s",
        type(exc).__name__,
        exc,
        exc_info=True,
    )
    END = "__end__"
    StateGraph = None
    _LANGGRAPH_AVAILABLE = False


class _FallbackGraphRunner:
    """在 LangGraph 不可用时串行执行 RAG 子图节点。"""

    def __init__(self, tool_gateway: MCPToolGateway):
        """初始化串行降级执行器。

        Args:
            tool_gateway: 执行 RAG 检索节点时使用的 MCP 网关。
        """
        self.tool_gateway = tool_gateway

    async def ainvoke(self, state: RagSubAgentState) -> RagSubAgentState:
        """按 LangGraph 节点顺序串行执行 RAG 子代理状态机。

        Args:
            state: RAG 子代理当前状态。

        Returns:
            完成校验、查询生成、检索调用、结果归一化后的状态。
        """
        state = await node_validate_constraints(state)
        state = await node_generate_query_statement(state)
        state = await node_execute_rag_search(state, self.tool_gateway)
        state = await node_normalize_results(state)
        state = await node_build_response(state)
        return state


def build_rag_graph(tool_gateway: MCPToolGateway) -> Any:
    """构建 RAG 子代理 LangGraph 拓扑。

    Args:
        tool_gateway: 检索调用节点使用的 MCP 网关。

    Returns:
        未编译的 StateGraph；LangGraph 不可用时返回 None。
    """
    if not _LANGGRAPH_AVAILABLE:
        return None

    async def _execute_rag_search(state: RagSubAgentState) -> RagSubAgentState:
        """闭包绑定 MCP 网关后执行 RAG 检索节点。

        Args:
            state: RAG 子代理当前状态。

        Returns:
            写入检索结果后的状态。
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
    """编译 RAG 子代理执行图，必要时返回降级执行器。

    Args:
        tool_gateway: 检索调用节点使用的 MCP 网关。

    Returns:
        可通过 ainvoke 运行的图执行对象。
    """
    graph = build_rag_graph(tool_gateway)
    if graph is None:
        return _FallbackGraphRunner(tool_gateway)
    return graph.compile()


async def run_rag_graph(compiled_graph: Any, state: RagSubAgentState) -> RagSubAgentState:
    """运行已编译的 RAG 子代理执行图。

    Args:
        compiled_graph: compile_rag_graph 返回的图执行对象。
        state: RAG 子代理初始状态。

    Returns:
        图执行后的最终状态；异常兼容分支下至少返回原始状态。
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
