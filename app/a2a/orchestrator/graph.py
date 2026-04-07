"""主控任务驱动编排图。"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from app.a2a.client.subagent_client import SubAgentClientFactory
from app.a2a.orchestrator.nodes import (
    node_assess_answerability,
    node_call_subagent_serial,
    node_compose_final_answer,
    node_decide_supplement,
    node_decompose_and_plan,
    node_merge_evidence,
    node_parse_input,
    node_pick_next_subtask,
    node_route_by_memory_capability,
)
from app.a2a.orchestrator.state import OrchestratorState

logger = logging.getLogger(__name__)

try:
    from langgraph.graph import END, StateGraph

    _LANGGRAPH_AVAILABLE = True
except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
    logger.warning(
        "LangGraph import failed for orchestrator graph, fallback runner enabled: error_type=%s error=%s",
        type(exc).__name__,
        exc,
        exc_info=True,
    )
    END = "__end__"
    StateGraph = None
    _LANGGRAPH_AVAILABLE = False


class _FallbackGraphRunner:
    """在 LangGraph 不可用时串行执行主控编排节点。"""

    def __init__(self, subagent_client: SubAgentClientFactory):
        """初始化实例并注入运行依赖。
        
        Args:
            subagent_client: 函数输入参数。
        
        Returns:
            None: 无返回值。
        """
        self.subagent_client = subagent_client

    async def ainvoke(self, state: OrchestratorState) -> OrchestratorState:
        """执行ainvoke流程并返回结果。
        
        Args:
            state: 编排状态对象。
        
        Returns:
            OrchestratorState: 执行结果。
        """
        state = await node_parse_input(state)
        state = await node_route_by_memory_capability(state)
        if str(state.get("response_mode") or "retrieval_required") == "retrieval_required":
            state = await node_decompose_and_plan(state)
        while True:
            if str(state.get("response_mode") or "retrieval_required") != "retrieval_required":
                break
            state = await node_pick_next_subtask(state)
            current = state.get("current_subtask") if isinstance(state.get("current_subtask"), dict) else {}
            if not current:
                break
            state = await node_call_subagent_serial(state, self.subagent_client)
            state = await node_merge_evidence(state)
            state = await node_decide_supplement(state)

            decision = _get_current_subtask_decision(state)
            pending = state.get("pending_subtasks") if isinstance(state.get("pending_subtasks"), list) else []
            if not bool(decision.get("need_supplement")) and not pending:
                break

        state = await node_assess_answerability(state)
        state = await node_compose_final_answer(state)
        return state


def route_after_pick(state: OrchestratorState) -> str:
    """根据当前状态计算下一步路由。
    
    Args:
        state: 编排状态对象。
    
    Returns:
        str: 文本结果。
    """
    current = state.get("current_subtask") if isinstance(state.get("current_subtask"), dict) else {}
    return "call_subagent_serial" if current else "assess_answerability"


def route_after_memory_route(state: OrchestratorState) -> str:
    """根据当前状态计算下一步路由。
    
    Args:
        state: 编排状态对象。
    
    Returns:
        str: 文本结果。
    """
    response_mode = str(state.get("response_mode") or "retrieval_required").strip() or "retrieval_required"
    if response_mode == "retrieval_required":
        return "decompose_and_plan"
    return "assess_answerability"


def _get_current_subtask_decision(state: OrchestratorState) -> dict[str, Any]:
    """获取并返回目标数据。
    
    Args:
        state: 编排状态对象。
    
    Returns:
        dict[str, Any]: 结构化结果数据。
    """
    current = state.get("current_subtask") if isinstance(state.get("current_subtask"), dict) else {}
    subtask_id = str(current.get("subtask_id") or "").strip()
    rows = state.get("subtask_decisions") if isinstance(state.get("subtask_decisions"), dict) else {}
    decision = rows.get(subtask_id) if isinstance(rows.get(subtask_id), dict) else {}
    return decision


def route_after_supplement_decision(state: OrchestratorState) -> str:
    """根据当前状态计算下一步路由。
    
    Args:
        state: 编排状态对象。
    
    Returns:
        str: 文本结果。
    """
    decision = _get_current_subtask_decision(state)
    pending = state.get("pending_subtasks") if isinstance(state.get("pending_subtasks"), list) else []
    if bool(decision.get("need_supplement")):
        return "pick_next_subtask"
    if pending:
        return "pick_next_subtask"
    return "assess_answerability"


def build_orchestrator_graph(subagent_client: SubAgentClientFactory) -> Any:
    """构建orchestrator_graph相关信息并返回结果。
    
    Args:
        subagent_client: 函数输入参数。
    
    Returns:
        Any: 执行结果。
    """
    if not _LANGGRAPH_AVAILABLE:
        return None

    async def _call_subagent_serial(state: OrchestratorState) -> OrchestratorState:
        """执行call_subagent_serial流程并返回结果。
        
        Args:
            state: 编排状态对象。
        
        Returns:
            OrchestratorState: 执行结果。
        """
        return await node_call_subagent_serial(state, subagent_client)

    builder = StateGraph(OrchestratorState)
    builder.add_node("parse_input", node_parse_input)
    builder.add_node("route_by_memory_capability", node_route_by_memory_capability)
    builder.add_node("decompose_and_plan", node_decompose_and_plan)
    builder.add_node("pick_next_subtask", node_pick_next_subtask)
    builder.add_node("call_subagent_serial", _call_subagent_serial)
    builder.add_node("merge_evidence", node_merge_evidence)
    builder.add_node("decide_supplement", node_decide_supplement)
    builder.add_node("assess_answerability", node_assess_answerability)
    builder.add_node("compose_final_answer", node_compose_final_answer)

    builder.set_entry_point("parse_input")
    builder.add_edge("parse_input", "route_by_memory_capability")
    builder.add_conditional_edges(
        "route_by_memory_capability",
        route_after_memory_route,
        {
            "decompose_and_plan": "decompose_and_plan",
            "assess_answerability": "assess_answerability",
        },
    )
    builder.add_edge("decompose_and_plan", "pick_next_subtask")

    builder.add_conditional_edges(
        "pick_next_subtask",
        route_after_pick,
        {
            "call_subagent_serial": "call_subagent_serial",
            "assess_answerability": "assess_answerability",
        },
    )
    builder.add_edge("call_subagent_serial", "merge_evidence")
    builder.add_edge("merge_evidence", "decide_supplement")

    builder.add_conditional_edges(
        "decide_supplement",
        route_after_supplement_decision,
        {
            "pick_next_subtask": "pick_next_subtask",
            "assess_answerability": "assess_answerability",
        },
    )

    builder.add_edge("assess_answerability", "compose_final_answer")
    builder.add_edge("compose_final_answer", END)
    return builder


def compile_orchestrator_graph(subagent_client: SubAgentClientFactory) -> Any:
    """执行compile_orchestrator_graph流程并返回结果。
    
    Args:
        subagent_client: 函数输入参数。
    
    Returns:
        Any: 执行结果。
    """
    graph = build_orchestrator_graph(subagent_client)
    if graph is None:
        return _FallbackGraphRunner(subagent_client)
    return graph.compile()


async def run_orchestrator_graph(compiled_graph: Any, state: OrchestratorState) -> OrchestratorState:
    """运行orchestrator_graph相关信息并返回结果。
    
    Args:
        compiled_graph: 函数输入参数。
        state: 编排状态对象。
    
    Returns:
        OrchestratorState: 执行结果。
    """
    ainvoke: Callable[[OrchestratorState], Awaitable[OrchestratorState]] = getattr(compiled_graph, "ainvoke")
    output_keys = list(
        dict.fromkeys(
            [
                *list(state.keys()),
                "current_subtask",
                "last_subagent_result",
                "subtask_decisions",
                "gate_result",
                "answerability",
                "final_output",
                "direct_reason",
                "retrieval_reason",
                "answer_source",
            ]
        )
    )
    try:
        output = await ainvoke(state, output_keys=output_keys)
    except TypeError:
        output = await ainvoke(state)
    return output if isinstance(output, dict) else state
