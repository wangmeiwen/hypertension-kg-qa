"""主代理图编排。"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from app.a2a.client.subagent_client import SubAgentClientFactory
from app.a2a.orchestrator.nodes import (
    node_call_subagent_serial,
    node_decide_supplement,
    node_decompose_and_rewrite,
    node_finalize_answer,
    node_merge_evidence,
    node_parse_input,
    node_pick_next_subtask,
)
from app.a2a.orchestrator.state import OrchestratorState

try:
    from langgraph.graph import END, StateGraph

    _LANGGRAPH_AVAILABLE = True
except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError):
    END = "__end__"
    StateGraph = None
    _LANGGRAPH_AVAILABLE = False


class _FallbackGraphRunner:
    def __init__(self, subagent_client: SubAgentClientFactory):
        """初始化 _FallbackGraphRunner 实例。
        
        Args:
            subagent_client: 参数。
        """
        self.subagent_client = subagent_client

    async def ainvoke(self, state: OrchestratorState) -> OrchestratorState:
        """执行 ainvoke。
        
        Args:
            state: 参数。
        
        Returns:
            执行结果。
        """
        state = await node_parse_input(state)
        state = await node_decompose_and_rewrite(state)
        while True:
            state = await node_pick_next_subtask(state)
            current = state.get("current_subtask") if isinstance(state.get("current_subtask"), dict) else {}
            if not current:
                break
            state = await node_call_subagent_serial(state, self.subagent_client)
            state = await node_merge_evidence(state)
            state = await node_decide_supplement(state)
            decision = state.get("decision") if isinstance(state.get("decision"), dict) else {}
            pending = state.get("pending_subtasks") if isinstance(state.get("pending_subtasks"), list) else []
            if not bool(decision.get("need_supplement")) and not pending:
                break
        state = await node_finalize_answer(state)
        return state


def route_after_pick(state: OrchestratorState) -> str:
    """执行 route_after_pick。
    
    Args:
        state: 参数。
    
    Returns:
        执行结果。
    """
    current = state.get("current_subtask") if isinstance(state.get("current_subtask"), dict) else {}
    return "call_subagent_serial" if current else "finalize_answer"


def route_after_supplement_decision(state: OrchestratorState) -> str:
    """执行 route_after_supplement_decision。
    
    Args:
        state: 参数。
    
    Returns:
        执行结果。
    """
    decision = state.get("decision") if isinstance(state.get("decision"), dict) else {}
    pending = state.get("pending_subtasks") if isinstance(state.get("pending_subtasks"), list) else []
    if bool(decision.get("need_supplement")):
        return "pick_next_subtask"
    if pending:
        return "pick_next_subtask"
    return "finalize_answer"


def build_orchestrator_graph(subagent_client: SubAgentClientFactory) -> Any:
    """构建 orchestrator_graph。
    
    Args:
        subagent_client: 参数。
    
    Returns:
        执行结果。
    """
    if not _LANGGRAPH_AVAILABLE:
        return None

    async def _call_subagent_serial(state: OrchestratorState) -> OrchestratorState:
        """执行 _call_subagent_serial 内部逻辑。
        
        Args:
            state: 参数。
        
        Returns:
            执行结果。
        """
        return await node_call_subagent_serial(state, subagent_client)

    builder: StateGraph = StateGraph(OrchestratorState)
    builder.add_node("parse_input", node_parse_input)
    builder.add_node("decompose_and_rewrite", node_decompose_and_rewrite)
    builder.add_node("pick_next_subtask", node_pick_next_subtask)
    builder.add_node("call_subagent_serial", _call_subagent_serial)
    builder.add_node("merge_evidence", node_merge_evidence)
    builder.add_node("decide_supplement", node_decide_supplement)
    builder.add_node("finalize_answer", node_finalize_answer)

    builder.set_entry_point("parse_input")
    builder.add_edge("parse_input", "decompose_and_rewrite")
    builder.add_edge("decompose_and_rewrite", "pick_next_subtask")
    builder.add_conditional_edges(
        "pick_next_subtask",
        route_after_pick,
        {
            "call_subagent_serial": "call_subagent_serial",
            "finalize_answer": "finalize_answer",
        },
    )
    builder.add_edge("call_subagent_serial", "merge_evidence")
    builder.add_edge("merge_evidence", "decide_supplement")
    builder.add_conditional_edges(
        "decide_supplement",
        route_after_supplement_decision,
        {
            "pick_next_subtask": "pick_next_subtask",
            "finalize_answer": "finalize_answer",
        },
    )
    builder.add_edge("finalize_answer", END)
    return builder


def compile_orchestrator_graph(subagent_client: SubAgentClientFactory) -> Any:
    """执行 compile_orchestrator_graph。
    
    Args:
        subagent_client: 参数。
    
    Returns:
        执行结果。
    """
    graph = build_orchestrator_graph(subagent_client)
    if graph is None:
        return _FallbackGraphRunner(subagent_client)
    return graph.compile()


async def run_orchestrator_graph(compiled_graph: Any, state: OrchestratorState) -> OrchestratorState:
    """执行 orchestrator_graph。
    
    Args:
        compiled_graph: 参数。
        state: 参数。
    
    Returns:
        执行结果。
    """
    ainvoke: Callable[[OrchestratorState], Awaitable[OrchestratorState]] = getattr(compiled_graph, "ainvoke")
    output_keys = list(
        dict.fromkeys(
            [
                *list(state.keys()),
                "current_subtask",
                "last_subagent_result",
                "decision",
                "final_output",
            ]
        )
    )
    try:
        output = await ainvoke(state, output_keys=output_keys)
    except TypeError:
        output = await ainvoke(state)
    return output if isinstance(output, dict) else state
