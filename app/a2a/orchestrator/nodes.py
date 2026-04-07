"""主控任务驱动节点实现。"""

from __future__ import annotations

import asyncio
from typing import Any

from app.a2a.client.subagent_client import SubAgentClientFactory
from app.a2a.models import EvidenceBundle, SubAgentConstraints, SubTask
from app.a2a.orchestrator.answer_gate import _INSUFFICIENT_MESSAGE, assess_answerability, compose_final_answer
from app.a2a.orchestrator.answer_writer import generate_final_answer_async
from app.a2a.orchestrator.coverage import evaluate_task_coverage
from app.a2a.orchestrator.evidence_store import merge_task_evidence
from app.a2a.orchestrator.gating_adapter import run_gate
from app.a2a.orchestrator.memory_router import route_by_memory_capability
from app.a2a.orchestrator.memory_writer import (
    build_assistant_capability_answer,
    build_generic_direct_answer,
    build_memory_answer_text,
)
from app.a2a.orchestrator.retrieval_planner import plan_task
from app.a2a.orchestrator.router import select_agent
from app.a2a.orchestrator.scheduler import pick_next_task
from app.a2a.orchestrator.state import OrchestratorState
from app.a2a.orchestrator.supplement_decider import decide_supplement
from app.a2a.orchestrator.task_decomposer import build_classification, decompose_to_tasks
from app.backend.services.memory_answer_service import MemoryAnswerService


def _append_step(state: OrchestratorState, text: str) -> None:
    """执行append_step流程并返回结果。
    
    Args:
        state: 编排状态对象。
        text: 输入文本。
    
    Returns:
        None: 无返回值。
    """
    rows_obj = state.get("processing_steps")
    rows = rows_obj if isinstance(rows_obj, list) else []
    rows.append(str(text or ""))
    state["processing_steps"] = rows


def _append_event(state: OrchestratorState, event: str, detail: str, metadata: dict[str, Any] | None = None) -> None:
    """执行append_event流程并返回结果。
    
    Args:
        state: 编排状态对象。
        event: 函数输入参数。
        detail: 函数输入参数。
        metadata: 函数输入参数。
    
    Returns:
        None: 无返回值。
    """
    rows_obj = state.get("reflection_events")
    rows = rows_obj if isinstance(rows_obj, list) else []
    rows.append(
        {
            "event": str(event or ""),
            "detail": str(detail or ""),
            "metadata": metadata if isinstance(metadata, dict) else {},
        }
    )
    state["reflection_events"] = rows


def _unique(rows: list[str]) -> list[str]:
    """执行unique流程并返回结果。
    
    Args:
        rows: 输入数据列表。
    
    Returns:
        list[str]: 处理后的列表结果。
    """
    output: list[str] = []
    seen: set[str] = set()
    for item in rows:
        token = str(item or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        output.append(token)
    return output


_SUPPORTED_INTENTS = {
    "direct_answer",
    "comparison",
    "conditional",
    "multi_outcome",
    "source_locating",
    "evidence_verification",
}


_INTENT_ALIASES = {
    "direct": "direct_answer",
    "qa": "direct_answer",
    "compare": "comparison",
    "comaprison": "comparison",
    "comparision": "comparison",
    "source": "source_locating",
    "verification": "evidence_verification",
    "verify": "evidence_verification",
    "比较": "comparison",
    "对比": "comparison",
    "对照": "comparison",
    "来源": "source_locating",
    "原文": "source_locating",
    "出处": "source_locating",
    "证据": "evidence_verification",
    "验证": "evidence_verification",
    "核实": "evidence_verification",
}


_INTENT_COMPARISON_HINTS = ("compare", "comparison", "vs", "versus", "比较", "对比", "相比", "差异", "优于", "劣于")
_INTENT_SOURCE_HINTS = ("source", "citation", "quote", "原文", "出处", "来源", "文献", "引用")
_INTENT_VERIFY_HINTS = ("verify", "verification", "evidence", "核实", "验证", "求证", "证据")
_ALLOWED_PREFERRED_SOURCES = {"memory", "kg", "rag", "direct", "assistant_capability"}
_ALLOWED_QUESTION_MODES = {
    "repeat_medical_question",
    "memory_lookup",
    "assistant_capability",
    "generic_direct",
    "medical_new_question",
    "medical_gap_followup",
}


def _normalize_intent(intent: str) -> str:
    """规范化输入并返回标准化结果。
    
    Args:
        intent: 函数输入参数。
    
    Returns:
        str: 文本结果。
    """
    token = str(intent or "").strip().lower()
    if token in _SUPPORTED_INTENTS:
        return token
    if token in _INTENT_ALIASES:
        return _INTENT_ALIASES[token]
    if any(hint in token for hint in _INTENT_COMPARISON_HINTS):
        return "comparison"
    if any(hint in token for hint in _INTENT_SOURCE_HINTS):
        return "source_locating"
    if any(hint in token for hint in _INTENT_VERIFY_HINTS):
        return "evidence_verification"
    return "direct_answer"


def _normalize_tool_result_payload(row: dict[str, Any]) -> dict[str, Any]:
    """规范化输入并返回标准化结果。
    
    Args:
        row: 输入数据项。
    
    Returns:
        dict[str, Any]: 结构化结果数据。
    """
    result_obj = row.get("result")
    result = result_obj if isinstance(result_obj, dict) else {}
    meta_obj = row.get("meta")
    meta = meta_obj if isinstance(meta_obj, dict) else {}
    return {
        "ok": bool(row.get("ok")),
        "result": result,
        "error": str(row.get("error") or ""),
        "meta": meta,
    }


def _build_gate_traces_from_tool_results(task: SubTask, tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """构建并返回目标数据。
    
    Args:
        task: 当前处理任务。
        tool_results: 函数输入参数。
    
    Returns:
        list[dict[str, Any]]: 处理后的列表结果。
    """
    traces: list[dict[str, Any]] = []
    for row in tool_results:
        if not isinstance(row, dict):
            continue
        traces.append(
            {
                "action": "subquery_parallel_call",
                "subquery": str(task.question or "").strip(),
                "subquery_id": int(task.subtask_id),
                "turn": max(1, int(task.attempt_count or 1)),
                "tool_name": str(row.get("tool_name") or task.tool_name or "").strip(),
                "agent": str(task.target_agent or "").strip(),
                "result": _normalize_tool_result_payload(row),
            }
        )
    return traces


def _coerce_observations_to_gate_traces(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """执行coerce_observations_to_gate_traces流程并返回结果。
    
    Args:
        observations: 函数输入参数。
    
    Returns:
        list[dict[str, Any]]: 处理后的列表结果。
    """
    traces: list[dict[str, Any]] = []
    for row in observations:
        if not isinstance(row, dict):
            continue
        if isinstance(row.get("result"), dict) and str(row.get("action") or "").strip():
            traces.append(row)
            continue
        if {"ok", "result", "tool_name"}.issubset(set(row.keys())):
            traces.append(
                {
                    "action": "call_tool",
                    "tool_name": str(row.get("tool_name") or "").strip(),
                    "turn": 0,
                    "result": _normalize_tool_result_payload(row),
                }
            )
    return traces


async def node_parse_input(state: OrchestratorState) -> OrchestratorState:
    """执行编排节点逻辑并更新状态。
    
    Args:
        state: 编排状态对象。
    
    Returns:
        OrchestratorState: 执行结果。
    """
    state["retrieval_round"] = int(state.get("retrieval_round") or 0)
    state["pending_subtasks"] = state.get("pending_subtasks") if isinstance(state.get("pending_subtasks"), list) else []
    state["tool_observations"] = state.get("tool_observations") if isinstance(state.get("tool_observations"), list) else []
    state["reflection_events"] = state.get("reflection_events") if isinstance(state.get("reflection_events"), list) else []
    state["processing_steps"] = state.get("processing_steps") if isinstance(state.get("processing_steps"), list) else []
    state["evidence_bundle"] = state.get("evidence_bundle") if isinstance(state.get("evidence_bundle"), dict) else EvidenceBundle().model_dump()
    state["evidence_store"] = state.get("evidence_store") if isinstance(state.get("evidence_store"), dict) else {}
    state["subtask_results"] = state.get("subtask_results") if isinstance(state.get("subtask_results"), dict) else {}
    state["subtask_status"] = state.get("subtask_status") if isinstance(state.get("subtask_status"), dict) else {}
    state["coverage_map"] = state.get("coverage_map") if isinstance(state.get("coverage_map"), dict) else {}
    state["attempt_counters"] = state.get("attempt_counters") if isinstance(state.get("attempt_counters"), dict) else {}
    state["agent_history"] = state.get("agent_history") if isinstance(state.get("agent_history"), list) else []
    state["query_hints"] = state.get("query_hints") if isinstance(state.get("query_hints"), dict) else {}
    state["failed_routes"] = state.get("failed_routes") if isinstance(state.get("failed_routes"), list) else []
    state["no_signal_streak"] = int(state.get("no_signal_streak") or 0)
    state["parallel_groups"] = state.get("parallel_groups") if isinstance(state.get("parallel_groups"), list) else []
    state["dependency_graph"] = state.get("dependency_graph") if isinstance(state.get("dependency_graph"), dict) else {}
    state["answerability"] = state.get("answerability") if isinstance(state.get("answerability"), dict) else {}
    state["gate_result"] = state.get("gate_result") if isinstance(state.get("gate_result"), dict) else {}
    state["evidence_gaps"] = state.get("evidence_gaps") if isinstance(state.get("evidence_gaps"), list) else []
    state["classification"] = state.get("classification") if isinstance(state.get("classification"), dict) else {}
    state["subtask_decisions"] = state.get("subtask_decisions") if isinstance(state.get("subtask_decisions"), dict) else {}
    state["response_mode"] = str(state.get("response_mode") or "").strip() or "medical_retrieval"
    state["memory_supported_points"] = state.get("memory_supported_points") if isinstance(state.get("memory_supported_points"), list) else []
    state["memory_missing_points"] = state.get("memory_missing_points") if isinstance(state.get("memory_missing_points"), list) else []
    state["memory_coverage_ratio"] = float(state.get("memory_coverage_ratio") or 0.0)
    state["memory_route_reason"] = str(state.get("memory_route_reason") or "").strip()

    resolved_query_obj = state.get("resolved_query")
    resolved_query = resolved_query_obj if isinstance(resolved_query_obj, dict) else {}
    standalone_question = str(resolved_query.get("standalone_question") or "").strip()
    if not standalone_question:
        standalone_question = str(state.get("input_text") or "").strip()

    subqueries_obj = resolved_query.get("subqueries")
    subqueries = [str(item or "").strip() for item in subqueries_obj] if isinstance(subqueries_obj, list) else []
    subqueries = [item for item in subqueries if item]
    if not subqueries and standalone_question:
        subqueries = [standalone_question]

    preferred_sources_obj = resolved_query.get("preferred_sources")
    preferred_sources = (
        [str(item or "").strip().lower() for item in preferred_sources_obj]
        if isinstance(preferred_sources_obj, list)
        else []
    )
    preferred_sources = [item for item in preferred_sources if item in _ALLOWED_PREFERRED_SOURCES]
    if not preferred_sources:
        preferred_sources = ["kg"]

    raw_question_mode = str(resolved_query.get("question_mode") or "").strip()
    question_mode = raw_question_mode if raw_question_mode in _ALLOWED_QUESTION_MODES else "medical_new_question"

    raw_intent = str(resolved_query.get("intent") or "direct_answer").strip()
    normalized_intent = _normalize_intent(raw_intent)

    state["resolved_query"] = {
        "standalone_question": standalone_question,
        "intent": normalized_intent,
        "question_mode": question_mode,
        "subqueries": subqueries,
        "must_cover": [str(item or "").strip() for item in (resolved_query.get("must_cover") if isinstance(resolved_query.get("must_cover"), list) else []) if str(item or "").strip()],
        "entities": [str(item or "").strip() for item in (resolved_query.get("entities") if isinstance(resolved_query.get("entities"), list) else []) if str(item or "").strip()],
        "filters": [str(item or "").strip() for item in (resolved_query.get("filters") if isinstance(resolved_query.get("filters"), list) else []) if str(item or "").strip()],
        "preferred_sources": preferred_sources,
        "carry_over_context": [str(item or "").strip() for item in (resolved_query.get("carry_over_context") if isinstance(resolved_query.get("carry_over_context"), list) else []) if str(item or "").strip()],
    }

    memory_context_obj = state.get("memory_context")
    state["memory_context"] = memory_context_obj if isinstance(memory_context_obj, dict) else {}
    state["original_input_text"] = str(state.get("original_input_text") or state.get("input_text") or "").strip()
    if standalone_question:
        state["input_text"] = standalone_question

    if raw_intent and raw_intent != normalized_intent:
        _append_step(state, f"意图归一: {raw_intent} -> {normalized_intent}")
        _append_event(
            state,
            "intent_normalized",
            f"non_standard_intent:{raw_intent}",
            {"raw_intent": raw_intent, "normalized_intent": normalized_intent},
        )

    _append_step(state, "解析输入并初始化任务驱动状态")
    return state


async def node_decompose_and_plan(state: OrchestratorState) -> OrchestratorState:
    """执行编排节点逻辑并更新状态。
    
    Args:
        state: 编排状态对象。
    
    Returns:
        OrchestratorState: 执行结果。
    """
    response_mode = str(state.get("response_mode") or "medical_retrieval").strip() or "medical_retrieval"
    if response_mode not in {"medical_retrieval", "memory_augmented_medical"}:
        return state
    if state.get("pending_subtasks"):
        return state

    resolved_query_obj = state.get("resolved_query")
    resolved_query = resolved_query_obj if isinstance(resolved_query_obj, dict) else {}
    if response_mode == "memory_augmented_medical":
        memory_missing_points = [
            str(item or "").strip()
            for item in (state.get("memory_missing_points") if isinstance(state.get("memory_missing_points"), list) else [])
            if str(item or "").strip()
        ]
        if memory_missing_points:
            standalone_question = str(resolved_query.get("standalone_question") or state.get("input_text") or "").strip()
            resolved_query = {
                **resolved_query,
                "question_mode": "medical_gap_followup",
                "must_cover": memory_missing_points[:5],
                "subqueries": [f"{standalone_question}，补充回答：{point}" for point in memory_missing_points[:3]],
            }
    max_tool_calls = max(1, int(state.get("subagent_max_tool_calls") or 1))
    constraints = SubAgentConstraints(allow_extra_retrieval=False, max_tool_calls=max_tool_calls)

    tasks = decompose_to_tasks(resolved_query, constraints)
    tasks = [plan_task(task) for task in tasks]

    state["pending_subtasks"] = [task.model_dump() for task in tasks]
    state["classification"] = build_classification(tasks)
    state["dependency_graph"] = {
        str(task.subtask_id): [int(dep) for dep in task.dependency_on] for task in tasks
    }
    state["subtask_status"] = {str(task.subtask_id): "pending" for task in tasks}

    _append_step(state, f"任务拆解完成，生成 {len(tasks)} 个标准任务")
    _append_event(state, "task_decompose_done", "主控完成任务语义拆解", {"count": len(tasks)})
    return state


async def node_route_by_memory_capability(state: OrchestratorState) -> OrchestratorState:
    """执行编排节点逻辑并更新状态。
    
    Args:
        state: 编排状态对象。
    
    Returns:
        OrchestratorState: 执行结果。
    """
    resolved_query = state.get("resolved_query") if isinstance(state.get("resolved_query"), dict) else {}
    memory_context = state.get("memory_context") if isinstance(state.get("memory_context"), dict) else {}
    route = route_by_memory_capability(
        question=str(state.get("input_text") or ""),
        resolved_query=resolved_query,
        memory_context=memory_context,
    )
    state["response_mode"] = str(route.get("response_mode") or "medical_retrieval")
    state["memory_supported_points"] = route.get("memory_supported_points") if isinstance(route.get("memory_supported_points"), list) else []
    state["memory_missing_points"] = route.get("memory_missing_points") if isinstance(route.get("memory_missing_points"), list) else []
    state["memory_coverage_ratio"] = float(route.get("memory_coverage_ratio") or 0.0)
    state["memory_route_reason"] = str(route.get("reason") or "")

    classification = state.get("classification") if isinstance(state.get("classification"), dict) else {}
    state["classification"] = {
        **classification,
        "response_mode": state["response_mode"],
        "memory_coverage_ratio": state["memory_coverage_ratio"],
    }

    _append_step(
        state,
        f"记忆优先路由完成 mode={state['response_mode']} reason={state['memory_route_reason']}",
    )
    _append_event(
        state,
        "memory_route_done",
        str(state["memory_route_reason"] or ""),
        {
            "response_mode": state["response_mode"],
            "memory_coverage_ratio": state["memory_coverage_ratio"],
        },
    )
    return state


async def node_pick_next_subtask(state: OrchestratorState) -> OrchestratorState:
    """执行编排节点逻辑并更新状态。
    
    Args:
        state: 编排状态对象。
    
    Returns:
        OrchestratorState: 执行结果。
    """
    pending_raw = state.get("pending_subtasks")
    pending_rows = pending_raw if isinstance(pending_raw, list) else []
    pending_tasks: list[SubTask] = []
    for row in pending_rows:
        if not isinstance(row, dict):
            continue
        pending_tasks.append(SubTask.model_validate(row))

    current, remaining = pick_next_task(pending_tasks, state.get("subtask_status") if isinstance(state.get("subtask_status"), dict) else {})
    state["pending_subtasks"] = [task.model_dump() for task in remaining]

    if current is None:
        state["current_subtask"] = {}
        _append_step(state, "无可执行任务，准备进行门控终答")
        return state

    state["current_subtask"] = current.model_dump()
    subtask_status = state.get("subtask_status") if isinstance(state.get("subtask_status"), dict) else {}
    subtask_status[str(current.subtask_id)] = "running"
    state["subtask_status"] = subtask_status

    _append_step(state, f"调度选中任务 subtask_id={current.subtask_id} priority={current.priority}")
    return state


async def node_call_subagent_serial(
    state: OrchestratorState,
    subagent_client: SubAgentClientFactory,
) -> OrchestratorState:
    """执行编排节点逻辑并更新状态。
    
    Args:
        state: 编排状态对象。
        subagent_client: 函数输入参数。
    
    Returns:
        OrchestratorState: 执行结果。
    """
    current_raw = state.get("current_subtask")
    if not isinstance(current_raw, dict) or not current_raw:
        state["last_subagent_result"] = {}
        return state

    task = SubTask.model_validate(current_raw)
    selected_agent = select_agent(
        task,
        state.get("coverage_map") if isinstance(state.get("coverage_map"), dict) else {},
        state.get("failed_routes") if isinstance(state.get("failed_routes"), list) else [],
        state.get("last_subagent_result") if isinstance(state.get("last_subagent_result"), dict) else {},
    )
    task.target_agent = "rag" if selected_agent == "rag" else "kg"
    task.tool_name = "rag_search" if task.target_agent == "rag" else "kg_entity_relation_search"

    attempts = state.get("attempt_counters") if isinstance(state.get("attempt_counters"), dict) else {}
    key = str(task.subtask_id)
    attempts[key] = int(attempts.get(key) or 0) + 1
    state["attempt_counters"] = attempts
    task.attempt_count = int(attempts[key])
    task.retry_context = {**(task.retry_context or {}), "attempt": task.attempt_count}
    state["current_subtask"] = task.model_dump()

    query_hints = state.get("query_hints") if isinstance(state.get("query_hints"), dict) else {}
    if isinstance(query_hints.get(key), dict):
        hint = query_hints[key]
        if hint.get("missing_points") and isinstance(hint.get("missing_points"), list):
            refine_terms = _unique([str(x) for x in hint.get("missing_points")])
            task.query_terms = _unique([*task.query_terms, *refine_terms])
            task.arguments = {
                **(task.arguments if isinstance(task.arguments, dict) else {}),
                "refine_terms": refine_terms,
            }

    state["current_subtask"] = task.model_dump()

    guard_raw = state.get("serial_guard")
    guard = guard_raw if isinstance(guard_raw, asyncio.Semaphore) else asyncio.Semaphore(1)
    state["serial_guard"] = guard

    _append_step(state, f"串行调用子代理 {task.target_agent} 执行任务 {task.subtask_id}")
    _append_event(state, "subagent_call_start", "开始执行任务", {"subtask_id": task.subtask_id, "agent": task.target_agent})

    result = await subagent_client.send_task(task, serial_guard=guard)
    state["last_subagent_result"] = result.model_dump()

    history_rows = state.get("agent_history") if isinstance(state.get("agent_history"), list) else []
    history_rows.append({"subtask_id": task.subtask_id, "agent": task.target_agent, "attempt": task.attempt_count})
    state["agent_history"] = history_rows

    if bool(result.no_signal):
        failed_rows = state.get("failed_routes") if isinstance(state.get("failed_routes"), list) else []
        failed_rows.append({"subtask_id": task.subtask_id, "agent": task.target_agent, "reason": "no_signal"})
        state["failed_routes"] = failed_rows

    _append_event(
        state,
        "subagent_call_done",
        "任务执行完成",
        {
            "subtask_id": task.subtask_id,
            "agent": task.target_agent,
            "signal": result.signal,
            "no_signal": bool(result.no_signal),
            "evidence_count": len(result.evidence),
        },
    )
    return state


async def node_merge_evidence(state: OrchestratorState) -> OrchestratorState:
    """执行编排节点逻辑并更新状态。
    
    Args:
        state: 编排状态对象。
    
    Returns:
        OrchestratorState: 执行结果。
    """
    current_raw = state.get("current_subtask")
    result_raw = state.get("last_subagent_result")
    if not isinstance(current_raw, dict) or not isinstance(result_raw, dict):
        return state

    task = SubTask.model_validate(current_raw)
    evidence_obj = result_raw.get("evidence")
    evidence_rows = evidence_obj if isinstance(evidence_obj, list) else []

    bundle = EvidenceBundle.model_validate(state.get("evidence_bundle") if isinstance(state.get("evidence_bundle"), dict) else {})
    if task.target_agent == "kg":
        bundle.kg_evidence.extend(evidence_rows)
    else:
        bundle.rag_evidence.extend(evidence_rows)
    state["evidence_bundle"] = bundle.model_dump()

    coverage = evaluate_task_coverage(task, evidence_rows)
    coverage["binding_insufficient"] = bool(coverage.get("binding_insufficient"))
    coverage["high_evidence_count"] = int(coverage.get("high_evidence_count") or 0)
    coverage["low_signal"] = bool(coverage.get("low_signal"))
    coverage_map = state.get("coverage_map") if isinstance(state.get("coverage_map"), dict) else {}
    coverage_map[str(task.subtask_id)] = coverage
    state["coverage_map"] = coverage_map

    subtask_results = state.get("subtask_results") if isinstance(state.get("subtask_results"), dict) else {}
    subtask_results[str(task.subtask_id)] = {
        "task": task.model_dump(),
        "result": result_raw,
        "coverage": coverage,
        "signal_flags": {
            "binding_insufficient": bool(coverage.get("binding_insufficient")),
            "high_evidence_count": int(coverage.get("high_evidence_count") or 0),
            "low_signal": bool(coverage.get("low_signal")),
        },
    }
    state["subtask_results"] = subtask_results

    store = merge_task_evidence(
        state.get("evidence_store") if isinstance(state.get("evidence_store"), dict) else {},
        subtask_id=task.subtask_id,
        agent_type=task.target_agent,
        evidence_rows=evidence_rows,
        coverage=coverage,
    )
    state["evidence_store"] = store

    status_map = state.get("subtask_status") if isinstance(state.get("subtask_status"), dict) else {}
    status_map[str(task.subtask_id)] = "done"
    state["subtask_status"] = status_map

    tool_results_obj = result_raw.get("tool_results")
    tool_results = tool_results_obj if isinstance(tool_results_obj, list) else []
    observations = state.get("tool_observations") if isinstance(state.get("tool_observations"), list) else []
    traces = _build_gate_traces_from_tool_results(task, tool_results)
    observations.extend(traces)
    state["tool_observations"] = _coerce_observations_to_gate_traces(observations)

    if bool(result_raw.get("no_signal")):
        state["no_signal_streak"] = int(state.get("no_signal_streak") or 0) + 1
    else:
        state["no_signal_streak"] = 0

    gaps = state.get("evidence_gaps") if isinstance(state.get("evidence_gaps"), list) else []
    missing_points = coverage.get("missing_points") if isinstance(coverage.get("missing_points"), list) else []
    state["evidence_gaps"] = _unique([*gaps, *[str(item) for item in missing_points]])

    _append_step(
        state,
        f"证据归档完成 subtask_id={task.subtask_id} status={coverage.get('coverage_status')} evidence={len(evidence_rows)}",
    )
    return state


async def node_decide_supplement(state: OrchestratorState) -> OrchestratorState:
    """执行编排节点逻辑并更新状态。
    
    Args:
        state: 编排状态对象。
    
    Returns:
        OrchestratorState: 执行结果。
    """
    current_raw = state.get("current_subtask")
    result_raw = state.get("last_subagent_result")
    if not isinstance(current_raw, dict) or not isinstance(result_raw, dict):
        return state

    task = SubTask.model_validate(current_raw)

    round_index = int(state.get("retrieval_round") or 0) + 1
    state["retrieval_round"] = round_index

    coverage_map = state.get("coverage_map") if isinstance(state.get("coverage_map"), dict) else {}
    coverage = coverage_map.get(str(task.subtask_id)) if isinstance(coverage_map.get(str(task.subtask_id)), dict) else {}

    decision = decide_supplement(
        task=task,
        round_index=max(1, int(task.attempt_count)),
        max_rounds=max(1, int(state.get("supplement_max_rounds") or state.get("max_turns") or 1)),
        coverage=coverage,
        last_agent=str(result_raw.get("target_agent") or task.target_agent),
        last_no_signal=bool(result_raw.get("no_signal")),
    )

    decision_rows = state.get("subtask_decisions") if isinstance(state.get("subtask_decisions"), dict) else {}
    key = str(task.subtask_id)
    decision_rows[key] = {
        "need_supplement": bool(decision.need_supplement),
        "next_agent": str(decision.next_agent or ""),
        "query_hint": decision.query_hint if isinstance(decision.query_hint, dict) else {},
        "subtask_stop_reason": str(decision.reason or ""),
    }
    state["subtask_decisions"] = decision_rows

    if decision.need_supplement:
        next_task = task.model_copy(deep=True)
        next_task.target_agent = decision.next_agent or next_task.target_agent
        next_task.question = decision.next_question or next_task.question
        next_task.reason = str(decision.reason or "supplement")
        next_task.agent_role = "supplement"
        next_task.priority = max(1, int(next_task.priority) - 5)

        pending_rows = state.get("pending_subtasks") if isinstance(state.get("pending_subtasks"), list) else []
        pending_rows.insert(0, next_task.model_dump())
        state["pending_subtasks"] = pending_rows

        hints = state.get("query_hints") if isinstance(state.get("query_hints"), dict) else {}
        hints[str(task.subtask_id)] = decision.query_hint if isinstance(decision.query_hint, dict) else {}
        state["query_hints"] = hints

        status_map = state.get("subtask_status") if isinstance(state.get("subtask_status"), dict) else {}
        status_map[str(task.subtask_id)] = "retrying"
        state["subtask_status"] = status_map

        _append_step(state, f"补检索触发 subtask_id={task.subtask_id} agent={next_task.target_agent} reason={decision.reason}")
        _append_event(state, "supplement_needed", str(decision.reason or ""), {"subtask_id": task.subtask_id})
    else:
        status_map = state.get("subtask_status") if isinstance(state.get("subtask_status"), dict) else {}
        status_map[str(task.subtask_id)] = "done"
        state["subtask_status"] = status_map

        _append_step(state, f"补检索未触发 subtask_id={task.subtask_id} reason={decision.reason}")
        _append_event(state, "supplement_skipped", str(decision.reason or ""), {"subtask_id": task.subtask_id})

    return state


async def node_assess_answerability(state: OrchestratorState) -> OrchestratorState:
    """执行编排节点逻辑并更新状态。
    
    Args:
        state: 编排状态对象。
    
    Returns:
        OrchestratorState: 执行结果。
    """
    response_mode = str(state.get("response_mode") or "medical_retrieval").strip() or "medical_retrieval"
    if response_mode in {"memory_answer", "assistant_capability", "generic_direct"}:
        state["gate_result"] = {
            "memory_supported_points": state.get("memory_supported_points") if isinstance(state.get("memory_supported_points"), list) else [],
            "memory_missing_points": state.get("memory_missing_points") if isinstance(state.get("memory_missing_points"), list) else [],
            "coverage_intervention": float(state.get("memory_coverage_ratio") or 0.0),
        }
        state["answerability"] = {
            "answer_mode": "direct",
            "gate_passed": True,
            "should_backtrack": False,
            "confidence_cap": 0.86 if response_mode == "memory_answer" else 0.78,
            "missing_points": state.get("memory_missing_points") if isinstance(state.get("memory_missing_points"), list) else [],
            "direct_support": True,
            "evidence_count": 0,
            "final_evidence_count": 0,
            "reason": f"{response_mode}_short_circuit",
        }
        _append_step(state, f"记忆优先终答直出 mode={response_mode}")
        return state

    observations = state.get("tool_observations") if isinstance(state.get("tool_observations"), list) else []
    traces = _coerce_observations_to_gate_traces(observations)
    state["tool_observations"] = traces
    classification = state.get("classification") if isinstance(state.get("classification"), dict) else {}
    evidence_bundle = state.get("evidence_bundle") if isinstance(state.get("evidence_bundle"), dict) else {}

    kg_rows = evidence_bundle.get("kg_evidence") if isinstance(evidence_bundle.get("kg_evidence"), list) else []
    rag_rows = evidence_bundle.get("rag_evidence") if isinstance(evidence_bundle.get("rag_evidence"), list) else []
    is_degraded = (len(kg_rows) + len(rag_rows)) == 0

    gate_result = run_gate(
        traces=traces,
        classification=classification,
        backtrack_count=int(state.get("retrieval_round") or 0),
        max_backtrack=max(1, int(state.get("max_turns") or 1)),
        is_degraded=is_degraded,
    )
    if response_mode == "memory_augmented_medical":
        gate_result["memory_supported_points"] = (
            state.get("memory_supported_points") if isinstance(state.get("memory_supported_points"), list) else []
        )
        gate_result["memory_missing_points"] = (
            state.get("memory_missing_points") if isinstance(state.get("memory_missing_points"), list) else []
        )
    state["gate_result"] = gate_result

    answerability = assess_answerability(
        evidence_bundle=evidence_bundle,
        gate_result=gate_result,
        coverage_map=state.get("coverage_map") if isinstance(state.get("coverage_map"), dict) else {},
    )
    state["answerability"] = answerability

    _append_step(state, f"终答门控评估完成 mode={answerability.get('answer_mode')} gate_passed={answerability.get('gate_passed')}")
    return state


async def node_compose_final_answer(state: OrchestratorState) -> OrchestratorState:
    """执行编排节点逻辑并更新状态。
    
    Args:
        state: 编排状态对象。
    
    Returns:
        OrchestratorState: 执行结果。
    """
    response_mode = str(state.get("response_mode") or "medical_retrieval").strip() or "medical_retrieval"
    answerability = state.get("answerability") if isinstance(state.get("answerability"), dict) else {}
    answer_mode = str(answerability.get("answer_mode") or "none").strip() or "none"

    processing_steps = [
        str(item)
        for item in (state.get("processing_steps") if isinstance(state.get("processing_steps"), list) else [])
    ]

    if response_mode == "memory_answer":
        memory_answer = MemoryAnswerService().answer(
            question=str(state.get("input_text") or ""),
            resolved_query=state.get("resolved_query") if isinstance(state.get("resolved_query"), dict) else {},
            memory_context=state.get("memory_context") if isinstance(state.get("memory_context"), dict) else {},
        )
        answer_text = str(memory_answer.get("answer_text") or "").strip()
        if not answer_text:
            answer_text = "当前会话记忆里还没有足够信息直接回答这个问题。"
        writer_result = {
            "answer_text": build_memory_answer_text(
                answer_text,
                support_type=str(memory_answer.get("support_type") or ""),
                confidence=float(memory_answer.get("confidence") or 0.0),
            ),
            "writer_metadata": {
                "writer_used": "memory",
                "writer_fallback_reason": "",
                "prompt_version": "memory_v1",
                "mode": "memory_answer",
            },
            "token_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }
    elif response_mode == "assistant_capability":
        writer_result = {
            "answer_text": build_assistant_capability_answer(),
            "writer_metadata": {
                "writer_used": "direct",
                "writer_fallback_reason": "",
                "prompt_version": "memory_v1",
                "mode": "assistant_capability",
            },
            "token_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }
    elif response_mode == "generic_direct":
        writer_result = {
            "answer_text": build_generic_direct_answer(
                str(state.get("input_text") or ""),
                state.get("memory_context") if isinstance(state.get("memory_context"), dict) else {},
            ),
            "writer_metadata": {
                "writer_used": "direct",
                "writer_fallback_reason": "",
                "prompt_version": "memory_v1",
                "mode": "generic_direct",
            },
            "token_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }
    elif answer_mode == "none":
        writer_result = {
            "answer_text": _INSUFFICIENT_MESSAGE,
            "writer_metadata": {
                "writer_used": "template",
                "writer_fallback_reason": "",
                "prompt_version": "none",
                "mode": "none",
            },
            "token_usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
        }
    else:
        writer_result = await generate_final_answer_async(
            question=str(state.get("input_text") or ""),
            answer_mode=answer_mode,
            evidence_bundle=state.get("evidence_bundle") if isinstance(state.get("evidence_bundle"), dict) else {},
            gate_result=state.get("gate_result") if isinstance(state.get("gate_result"), dict) else {},
            coverage_map=state.get("coverage_map") if isinstance(state.get("coverage_map"), dict) else {},
            resolved_query=state.get("resolved_query") if isinstance(state.get("resolved_query"), dict) else {},
            processing_steps=processing_steps,
        )

    final = compose_final_answer(
        question=str(state.get("input_text") or ""),
        evidence_bundle=state.get("evidence_bundle") if isinstance(state.get("evidence_bundle"), dict) else {},
        gate_result=state.get("gate_result") if isinstance(state.get("gate_result"), dict) else {},
        answerability=answerability,
        trace=processing_steps,
        answer_text=str(writer_result.get("answer_text") or ""),
        writer_metadata=writer_result.get("writer_metadata") if isinstance(writer_result.get("writer_metadata"), dict) else {},
        writer_token_usage=writer_result.get("token_usage") if isinstance(writer_result.get("token_usage"), dict) else {},
    )
    state["final_output"] = final.model_dump()
    _append_step(state, "主控完成终答组装")
    _append_event(state, "finalized", "主控已生成最终回答")
    return state
