"""主代理 LangGraph 节点。"""

from __future__ import annotations

import asyncio
from typing import Any

from app.a2a.client.subagent_client import SubAgentClientFactory
from app.a2a.models import EvidenceBundle, SubTask
from app.a2a.orchestrator.policies import (
    build_final_answer,
    decide_need_supplement,
    decompose_question,
    rewrite_subquestion,
    select_next_subagent,
)
from app.a2a.orchestrator.state import OrchestratorState


def _append_step(state: OrchestratorState, text: str) -> None:
    """执行 _append_step 内部逻辑。
    
    Args:
        state: 参数。
        text: 参数。
    
    Returns:
        执行结果。
    """
    steps_obj = state.get("processing_steps")
    steps = steps_obj if isinstance(steps_obj, list) else []
    steps.append(text)
    state["processing_steps"] = steps


def _append_event(state: OrchestratorState, event: str, detail: str, metadata: dict[str, Any] | None = None) -> None:
    """执行 _append_event 内部逻辑。
    
    Args:
        state: 参数。
        event: 参数。
        detail: 参数。
        metadata: 参数。
    
    Returns:
        执行结果。
    """
    rows_obj = state.get("reflection_events")
    rows = rows_obj if isinstance(rows_obj, list) else []
    rows.append(
        {
            "event": event,
            "detail": detail,
            "metadata": metadata if isinstance(metadata, dict) else {},
        }
    )
    state["reflection_events"] = rows


async def node_parse_input(state: OrchestratorState) -> OrchestratorState:
    """执行 node_parse_input 节点逻辑。
    
    Args:
        state: 参数。
    
    Returns:
        执行结果。
    """
    state["retrieval_round"] = int(state.get("retrieval_round") or 0)
    state["pending_subtasks"] = state.get("pending_subtasks") or []
    state["tool_observations"] = state.get("tool_observations") or []
    state["processing_steps"] = state.get("processing_steps") or []
    state["reflection_events"] = state.get("reflection_events") or []
    state["evidence_bundle"] = state.get("evidence_bundle") or EvidenceBundle().model_dump()
    _append_step(state, "解析输入并初始化主代理状态")
    return state


async def node_decompose_and_rewrite(state: OrchestratorState) -> OrchestratorState:
    """执行 node_decompose_and_rewrite 节点逻辑。
    
    Args:
        state: 参数。
    
    Returns:
        执行结果。
    """
    input_text = str(state.get("input_text") or "").strip()
    if not state.get("pending_subtasks"):
        rows = [rewrite_subquestion(row) for row in decompose_question(input_text)]
        state["pending_subtasks"] = [row.model_dump() for row in rows]
    _append_step(state, f"问题分解完成，待执行子任务 {len(state.get('pending_subtasks') or [])} 个")
    _append_event(state, "decompose_done", "主代理完成问题分解")
    return state


async def node_pick_next_subtask(state: OrchestratorState) -> OrchestratorState:
    """执行 node_pick_next_subtask 节点逻辑。
    
    Args:
        state: 参数。
    
    Returns:
        执行结果。
    """
    pending_obj = state.get("pending_subtasks")
    pending = pending_obj if isinstance(pending_obj, list) else []
    if not pending:
        state["current_subtask"] = {}
        _append_step(state, "无待执行子任务，准备终答")
        return state

    current = pending.pop(0)
    state["pending_subtasks"] = pending
    state["current_subtask"] = current if isinstance(current, dict) else {}
    _append_step(
        state,
        f"选择子任务 subtask_id={state.get('current_subtask', {}).get('subtask_id', '')}",
    )
    return state


async def node_call_subagent_serial(
    state: OrchestratorState,
    subagent_client: SubAgentClientFactory,
) -> OrchestratorState:
    """执行 node_call_subagent_serial 节点逻辑。
    
    Args:
        state: 参数。
        subagent_client: 参数。
    
    Returns:
        执行结果。
    """
    current_obj = state.get("current_subtask")
    current = current_obj if isinstance(current_obj, dict) else {}
    if not current:
        state["last_subagent_result"] = {}
        return state

    task = SubTask.model_validate(current)
    selected = select_next_subagent(
        task,
        EvidenceBundle.model_validate(state.get("evidence_bundle") or {}),
    )
    task.target_agent = "rag" if selected == "rag" else "kg"

    _append_step(state, f"串行调用子代理: {task.target_agent}")
    _append_event(state, "subagent_call_start", f"start:{task.target_agent}", {"subtask_id": task.subtask_id})

    guard_obj = state.get("serial_guard")
    guard = guard_obj if isinstance(guard_obj, asyncio.Semaphore) else asyncio.Semaphore(1)
    state["serial_guard"] = guard

    result = await subagent_client.send_task(task, serial_guard=guard)
    state["last_subagent_result"] = result.model_dump()

    _append_event(
        state,
        "subagent_call_done",
        f"done:{task.target_agent}",
        {
            "subtask_id": task.subtask_id,
            "no_signal": bool(result.no_signal),
            "evidence_count": len(result.evidence),
        },
    )
    return state


async def node_merge_evidence(state: OrchestratorState) -> OrchestratorState:
    """执行 node_merge_evidence 节点逻辑。
    
    Args:
        state: 参数。
    
    Returns:
        执行结果。
    """
    bundle = EvidenceBundle.model_validate(state.get("evidence_bundle") or {})
    result_obj = state.get("last_subagent_result")
    result = result_obj if isinstance(result_obj, dict) else {}
    target_agent = str(result.get("target_agent") or "")
    evidence_obj = result.get("evidence")
    evidence = evidence_obj if isinstance(evidence_obj, list) else []

    if target_agent == "kg":
        bundle.kg_evidence.extend(evidence)
    elif target_agent == "rag":
        bundle.rag_evidence.extend(evidence)

    state["evidence_bundle"] = bundle.model_dump()

    observations_obj = state.get("tool_observations")
    observations = observations_obj if isinstance(observations_obj, list) else []
    tool_rows_obj = result.get("tool_results")
    tool_rows = tool_rows_obj if isinstance(tool_rows_obj, list) else []
    observations.extend(tool_rows)
    state["tool_observations"] = observations

    _append_step(
        state,
        f"证据合并完成，kg={len(bundle.kg_evidence)}, rag={len(bundle.rag_evidence)}",
    )
    return state


async def node_decide_supplement(state: OrchestratorState) -> OrchestratorState:
    """执行 node_decide_supplement 节点逻辑。
    
    Args:
        state: 参数。
    
    Returns:
        执行结果。
    """
    result_obj = state.get("last_subagent_result")
    result = result_obj if isinstance(result_obj, dict) else {}
    current_obj = state.get("current_subtask")
    current = current_obj if isinstance(current_obj, dict) else {}

    round_index = int(state.get("retrieval_round") or 0) + 1
    state["retrieval_round"] = round_index

    decision = decide_need_supplement(
        evidence_bundle=EvidenceBundle.model_validate(state.get("evidence_bundle") or {}),
        round_index=round_index,
        max_rounds=int(state.get("max_turns") or 2),
        last_agent=str(result.get("target_agent") or current.get("target_agent") or "kg"),
        last_no_signal=bool(result.get("no_signal", True)),
        original_question=str(state.get("input_text") or ""),
    )
    state["decision"] = decision.model_dump()

    if decision.need_supplement and decision.next_agent:
        pending_obj = state.get("pending_subtasks")
        pending = pending_obj if isinstance(pending_obj, list) else []
        pending.insert(
            0,
            SubTask(
                subtask_id=int(round_index + 1000),
                target_agent=decision.next_agent,
                question=decision.next_question,
                intent="supplement",
                tool_name="rag_search" if decision.next_agent == "rag" else "kg_entity_relation_search",
                reason=decision.reason,
            ).model_dump(),
        )
        state["pending_subtasks"] = pending
        _append_step(state, f"主代理决策: 需要补检索，下一跳 {decision.next_agent}")
        _append_event(state, "supplement_needed", decision.reason, {"next_agent": decision.next_agent})
    else:
        _append_step(state, "主代理决策: 不需要补检索，进入终答")
        _append_event(state, "supplement_not_needed", decision.reason)

    return state


async def node_finalize_answer(state: OrchestratorState) -> OrchestratorState:
    """执行 node_finalize_answer 节点逻辑。
    
    Args:
        state: 参数。
    
    Returns:
        执行结果。
    """
    final = build_final_answer(
        evidence_bundle=EvidenceBundle.model_validate(state.get("evidence_bundle") or {}),
        question=str(state.get("input_text") or ""),
        trace=[str(item) for item in (state.get("processing_steps") or [])],
    )
    state["final_output"] = final.model_dump()
    _append_step(state, "主代理完成终答组装")
    _append_event(state, "finalized", "主代理已生成最终回答")
    return state
