"""RAG 子代理 LangGraph 节点。"""

from __future__ import annotations

from typing import Any

from app.a2a.mcp.tool_gateway import MCPToolGateway
from app.a2a.models import SubAgentResult
from app.a2a.subagents.rag.query_builder import build_rag_payload, validate_rag_payload
from app.a2a.subagents.rag.state import RagSubAgentState


def _build_query_records(
    query_statements: list[dict[str, Any]],
    tool_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """将 RAG 工具命中结果整理成统一 query_records 结构。"""
    records: list[dict[str, Any]] = []
    for index, statement in enumerate(query_statements):
        if not isinstance(statement, dict):
            continue
        payload_obj = statement.get("payload")
        payload = payload_obj if isinstance(payload_obj, dict) else {}
        query = str(payload.get("query") or "").strip()
        if not query:
            continue
        row = tool_results[index] if index < len(tool_results) and isinstance(tool_results[index], dict) else {}
        result_obj = row.get("result")
        result = result_obj if isinstance(result_obj, dict) else {}
        hits_obj = result.get("hits")
        hits = hits_obj if isinstance(hits_obj, list) else []
        content_parts: list[str] = []
        citations: list[str] = []
        for hit in hits[:3]:
            if not isinstance(hit, dict):
                continue
            snippet = str(hit.get("text") or hit.get("parent_content") or "").replace("\n", " ").strip()
            if snippet:
                content_parts.append(snippet[:280])
            citation = str(hit.get("source") or hit.get("id") or "").strip()
            if citation and citation not in citations:
                citations.append(citation)
        records.append(
            {
                "agent": "rag",
                "query": query,
                "result_content": " | ".join(content_parts)[:1600],
                "captured_at": "",
                "citations": citations[:6],
                "has_signal": bool(content_parts),
            }
        )
    return records


async def node_validate_constraints(state: RagSubAgentState) -> RagSubAgentState:
    """执行 node_validate_constraints 节点逻辑。
    
    Args:
        state: 参数。
    
    Returns:
        执行结果。
    """
    constraints_obj = state.get("constraints")
    constraints = constraints_obj if isinstance(constraints_obj, dict) else {}
    if bool(constraints.get("allow_extra_retrieval")):
        raise PermissionError("rag_subagent_extra_retrieval_not_allowed")
    max_tool_calls = int(constraints.get("max_tool_calls") or 1)
    constraints["max_tool_calls"] = max(1, min(max_tool_calls, 8))
    state["constraints"] = constraints
    return state


async def node_generate_query_statement(state: RagSubAgentState) -> RagSubAgentState:
    """执行 node_generate_query_statement 节点逻辑。
    
    Args:
        state: 参数。
    
    Returns:
        执行结果。
    """
    question = str(state.get("input_question") or "").strip()
    arguments_obj = state.get("arguments")
    arguments = arguments_obj if isinstance(arguments_obj, dict) else {}
    query_terms_obj = state.get("query_terms")
    query_terms = [str(item or "").strip() for item in query_terms_obj] if isinstance(query_terms_obj, list) else []
    query_terms = [item for item in query_terms if item]
    payload = validate_rag_payload(build_rag_payload(question, arguments, query_terms=query_terms))
    statement = {"tool_name": "rag_search", "payload": payload}
    state["query_statements"] = [statement]
    state["tool_calls"] = [statement]
    return state


async def node_execute_rag_search(state: RagSubAgentState, tool_gateway: MCPToolGateway) -> RagSubAgentState:
    """执行 node_execute_rag_search 节点逻辑。
    
    Args:
        state: 参数。
        tool_gateway: 参数。
    
    Returns:
        执行结果。
    """
    calls_obj = state.get("tool_calls")
    calls = calls_obj if isinstance(calls_obj, list) else []
    rows = await tool_gateway.invoke_batch(calls, allow_parallel=True)
    state["tool_results"] = rows
    return state


async def node_normalize_results(state: RagSubAgentState) -> RagSubAgentState:
    """执行 node_normalize_results 节点逻辑。
    
    Args:
        state: 参数。
    
    Returns:
        执行结果。
    """
    rows_obj = state.get("tool_results")
    rows = rows_obj if isinstance(rows_obj, list) else []
    evidence = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        result_obj = row.get("result")
        result = result_obj if isinstance(result_obj, dict) else {}
        hits_obj = result.get("hits")
        hits = hits_obj if isinstance(hits_obj, list) else []
        for hit in hits:
            if isinstance(hit, dict):
                evidence.append({"tool": "rag_search", "hit": hit})

    state["evidence"] = evidence
    state["no_signal"] = len(evidence) == 0
    state["reason"] = "no_signal" if len(evidence) == 0 else "ok"
    state["telemetry"] = {
        "tool_calls": len(rows),
        "signal_count": len(evidence),
        "ok_count": sum(1 for row in rows if isinstance(row, dict) and bool(row.get("ok"))),
    }
    return state


async def node_build_response(state: RagSubAgentState) -> RagSubAgentState:
    """执行 node_build_response 节点逻辑。
    
    Args:
        state: 参数。
    
    Returns:
        执行结果。
    """
    evidence_rows_obj = state.get("evidence")
    evidence_rows = evidence_rows_obj if isinstance(evidence_rows_obj, list) else []
    evidence_count = len(evidence_rows)

    query_statements_obj = state.get("query_statements")
    query_statements = query_statements_obj if isinstance(query_statements_obj, list) else []
    tool_results_obj = state.get("tool_results")
    tool_results = tool_results_obj if isinstance(tool_results_obj, list) else []
    queries_used: list[str] = []
    for statement in query_statements:
        if not isinstance(statement, dict):
            continue
        payload_obj = statement.get("payload")
        payload = payload_obj if isinstance(payload_obj, dict) else {}
        query = str(payload.get("query") or "").strip()
        if query:
            queries_used.append(query)
    query_records = _build_query_records(query_statements, tool_results)

    if evidence_count == 0:
        signal = "none"
    elif evidence_count >= 3:
        signal = "strong"
    else:
        signal = "weak"

    result = SubAgentResult(
        subtask_id=int(state.get("subtask_id") or 1),
        target_agent="rag",
        question=str(state.get("input_question") or ""),
        query_statements=query_statements,
        queries_used=queries_used,
        query_records=query_records,
        tool_results=tool_results,
        evidence=evidence_rows,
        signal=signal,
        coverage={
            "coverage_status": "none" if evidence_count == 0 else "partial",
            "coverage_ratio": 0.0 if evidence_count == 0 else min(1.0, round(float(evidence_count) / 3.0, 4)),
            "evidence_count": evidence_count,
        },
        missing_points=[],
        direct_support=evidence_count > 0,
        suggested_next_route="kg" if evidence_count == 0 else "rag",
        no_signal=bool(state.get("no_signal")),
        reason=str(state.get("reason") or ""),
        telemetry=state.get("telemetry") or {},
    )
    state["final_result"] = result.model_dump()
    return state
