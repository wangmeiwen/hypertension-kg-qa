"""KG 子代理 LangGraph 节点。"""

from __future__ import annotations

from typing import Any

from app.a2a.mcp.tool_gateway import MCPToolGateway
from app.a2a.models import SubAgentResult
from app.a2a.subagents.kg.query_builder import (
    build_kg_entity_relation_payload,
    build_kg_query_payload,
    validate_generated_payload,
)
from app.a2a.subagents.kg.state import KgSubAgentState


async def node_validate_constraints(state: KgSubAgentState) -> KgSubAgentState:
    """执行 node_validate_constraints 节点逻辑。
    
    Args:
        state: 参数。
    
    Returns:
        执行结果。
    """
    constraints_obj = state.get("constraints")
    constraints = constraints_obj if isinstance(constraints_obj, dict) else {}
    if bool(constraints.get("allow_extra_retrieval")):
        raise PermissionError("kg_subagent_extra_retrieval_not_allowed")
    state["constraints"] = constraints
    return state


def _resolve_tool_plan(state: KgSubAgentState) -> list[dict[str, Any]]:
    """执行 _resolve_tool_plan 内部逻辑。
    
    Args:
        state: 参数。
    
    Returns:
        执行结果。
    """
    tool_name = str(state.get("tool_name") or "kg_entity_relation_search").strip() or "kg_entity_relation_search"
    question = str(state.get("input_question") or "").strip()
    arguments_obj = state.get("arguments")
    arguments = arguments_obj if isinstance(arguments_obj, dict) else {}

    if tool_name in {"kg_query", "kg_entity_relation_search"}:
        return [{"tool_name": tool_name, "arguments": arguments, "question": question}]

    if tool_name in {"kg_composite", "kg_dual_search"}:
        return [
            {"tool_name": "kg_entity_relation_search", "arguments": arguments, "question": question},
            {"tool_name": "kg_query", "arguments": arguments, "question": question},
        ]

    return [{"tool_name": "kg_entity_relation_search", "arguments": arguments, "question": question}]


async def node_generate_query_statements(state: KgSubAgentState) -> KgSubAgentState:
    """执行 node_generate_query_statements 节点逻辑。
    
    Args:
        state: 参数。
    
    Returns:
        执行结果。
    """
    plans = _resolve_tool_plan(state)
    query_statements: list[dict[str, Any]] = []
    tool_calls: list[dict[str, Any]] = []

    for plan in plans:
        tool_name = str(plan.get("tool_name") or "").strip()
        question = str(plan.get("question") or "").strip()
        arguments_obj = plan.get("arguments")
        arguments = arguments_obj if isinstance(arguments_obj, dict) else {}

        if tool_name == "kg_query":
            payload = build_kg_query_payload(question, arguments)
        else:
            payload = build_kg_entity_relation_payload(question, arguments)

        payload = validate_generated_payload(tool_name, payload)
        query_statements.append({"tool_name": tool_name, "payload": payload})
        tool_calls.append({"tool_name": tool_name, "payload": payload})

    state["query_statements"] = query_statements
    state["tool_calls"] = tool_calls
    return state


async def node_execute_tools(state: KgSubAgentState, tool_gateway: MCPToolGateway) -> KgSubAgentState:
    """执行 node_execute_tools 节点逻辑。
    
    Args:
        state: 参数。
        tool_gateway: 参数。
    
    Returns:
        执行结果。
    """
    calls_obj = state.get("tool_calls")
    calls = calls_obj if isinstance(calls_obj, list) else []
    tool_results = await tool_gateway.invoke_batch(calls, allow_parallel=True)
    state["tool_results"] = tool_results
    return state


def _extract_evidence_from_row(row: dict[str, Any]) -> list[dict[str, Any]]:
    """执行 _extract_evidence_from_row 内部逻辑。
    
    Args:
        row: 参数。
    
    Returns:
        执行结果。
    """
    tool_name = str(row.get("tool_name") or "")
    result_obj = row.get("result")
    result = result_obj if isinstance(result_obj, dict) else {}
    if tool_name == "kg_query":
        records_obj = result.get("records")
        records = records_obj if isinstance(records_obj, list) else []
        evidence: list[dict[str, Any]] = []
        for item in records:
            if isinstance(item, dict):
                evidence.append({"tool": tool_name, "record": item})
        return evidence

    entities_obj = result.get("entities")
    entities = entities_obj if isinstance(entities_obj, list) else []
    relations_obj = result.get("relationships")
    relations = relations_obj if isinstance(relations_obj, list) else []
    evidence = []
    for item in entities:
        if isinstance(item, dict):
            evidence.append({"tool": tool_name, "entity": item})
    for item in relations:
        if isinstance(item, dict):
            evidence.append({"tool": tool_name, "relationship": item})
    return evidence


async def node_normalize_results(state: KgSubAgentState) -> KgSubAgentState:
    """执行 node_normalize_results 节点逻辑。
    
    Args:
        state: 参数。
    
    Returns:
        执行结果。
    """
    rows_obj = state.get("tool_results")
    rows = rows_obj if isinstance(rows_obj, list) else []
    evidence: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        evidence.extend(_extract_evidence_from_row(row))

    state["evidence"] = evidence
    state["no_signal"] = len(evidence) == 0
    state["reason"] = "no_signal" if len(evidence) == 0 else "ok"
    state["telemetry"] = {
        "tool_calls": len(rows),
        "signal_count": len(evidence),
        "ok_count": sum(1 for row in rows if isinstance(row, dict) and bool(row.get("ok"))),
    }
    return state


async def node_build_response(state: KgSubAgentState) -> KgSubAgentState:
    """执行 node_build_response 节点逻辑。
    
    Args:
        state: 参数。
    
    Returns:
        执行结果。
    """
    result = SubAgentResult(
        subtask_id=int(state.get("subtask_id") or 1),
        target_agent="kg",
        question=str(state.get("input_question") or ""),
        query_statements=state.get("query_statements") or [],
        tool_results=state.get("tool_results") or [],
        evidence=state.get("evidence") or [],
        no_signal=bool(state.get("no_signal")),
        reason=str(state.get("reason") or ""),
        telemetry=state.get("telemetry") or {},
    )
    state["final_result"] = result.model_dump()
    return state
