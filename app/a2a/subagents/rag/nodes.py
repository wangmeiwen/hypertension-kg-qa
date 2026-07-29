"""RAG 子代理 LangGraph 节点。"""

from __future__ import annotations

from typing import Any

from app.a2a.models import Evidence, SubAgentResult
from app.a2a.subagents.rag.query_builder import build_rag_payload, validate_rag_payload
from app.a2a.subagents.retry_utils import (
    MAX_SUBAGENT_TOOL_CALLS,
    build_query_variants,
    query_variant_for_attempt,
)
from app.a2a.subagents.rag.state import RagSubAgentState
from app.a2a.tool_gateway import MCPToolGateway


def _is_statistical_query(query: str) -> bool:
    """判断查询是否属于需要保留精简百分比证据的统计类问题。"""
    text = str(query or "").replace(" ", "").strip()
    if not text:
        return False
    keywords = ("患病率", "加权", "成人", "18岁", "知晓率", "治疗率", "控制率")
    return any(char.isdigit() for char in text) and any(token in text for token in keywords)


def _trim_hit_snippet(query: str, snippet: str) -> str:
    """根据查询类型裁剪 RAG 命中文本，避免长片段淹没关键证据。"""
    text = str(snippet or "").replace("\n", " ").strip()
    if not text:
        return ""
    if _is_statistical_query(query):
        percent_index = text.find("%")
        if percent_index > 0:
            return text[: percent_index + 1]
        return text[:120]
    return text[:280]


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
        evidence_obj = result.get("evidence")
        evidence_rows = evidence_obj if isinstance(evidence_obj, list) else []
        content_parts: list[str] = []
        citations: list[str] = []
        top_evidence = (
            evidence_rows[0]
            if evidence_rows and isinstance(evidence_rows[0], dict)
            else {}
        )
        provenance_obj = top_evidence.get("provenance")
        top_provenance = provenance_obj if isinstance(provenance_obj, dict) else {}
        page_num = top_evidence.get("page")
        start_page = top_provenance.get("start_page")
        end_page = top_provenance.get("end_page")
        chunk_id = str(top_evidence.get("chunk_id") or "").strip()
        selected_evidence = (
            evidence_rows[:1]
            if _is_statistical_query(query)
            else evidence_rows[:3]
        )
        for item in selected_evidence:
            if not isinstance(item, dict):
                continue
            snippet = _trim_hit_snippet(query, item.get("content") or "")
            if snippet:
                content_parts.append(snippet)
            source = str(item.get("source_id") or "").strip()
            page_label = item.get("page")
            item_provenance_obj = item.get("provenance")
            item_provenance = (
                item_provenance_obj
                if isinstance(item_provenance_obj, dict)
                else {}
            )
            start_label = item_provenance.get("start_page")
            end_label = item_provenance.get("end_page")
            citation = source
            if page_label not in (None, ""):
                citation = f"{source}@p{page_label}" if source else f"p{page_label}"
            elif start_label not in (None, ""):
                citation = f"{source}@p{start_label}-{end_label}" if source else f"p{start_label}-{end_label}"
            if citation and citation not in citations:
                citations.append(citation)
        record = {
            "agent": "rag",
            "query": query,
            "result_content": " | ".join(content_parts)[:1600],
            "captured_at": "",
            "citations": citations[:6],
            "has_signal": bool(content_parts),
        }
        if page_num not in (None, ""):
            record["page_num"] = page_num
        if start_page not in (None, ""):
            record["start_page"] = start_page
        if end_page not in (None, ""):
            record["end_page"] = end_page
        if chunk_id:
            record["chunk_id"] = chunk_id
        records.append(record)
    return records


async def node_validate_constraints(state: RagSubAgentState) -> RagSubAgentState:
    """校验 RAG 子代理执行约束并收敛最大工具调用次数。

    Args:
        state: RAG 子代理当前状态。

    Returns:
        写入规范化 constraints 后的状态。
    """
    constraints_obj = state.get("constraints")
    constraints = constraints_obj if isinstance(constraints_obj, dict) else {}
    if bool(constraints.get("allow_extra_retrieval")):
        raise PermissionError("rag_subagent_extra_retrieval_not_allowed")
    max_tool_calls = int(constraints.get("max_tool_calls") or MAX_SUBAGENT_TOOL_CALLS)
    constraints["max_tool_calls"] = max(1, min(max_tool_calls, MAX_SUBAGENT_TOOL_CALLS))
    state["constraints"] = constraints
    return state


def _extract_retry_terms_from_evidence(state: RagSubAgentState) -> list[str]:
    """从已有 RAG 命中中抽取下一轮检索可用的来源或短词。"""
    evidence_obj = state.get("evidence")
    evidence = evidence_obj if isinstance(evidence_obj, list) else []
    terms: list[str] = []
    for row in evidence[:3]:
        item = row if isinstance(row, dict) else {}
        for field in ("source_id", "section"):
            value = str(item.get(field) or "").strip()
            if value:
                terms.append(value)
    return terms


async def node_generate_query_statement(state: RagSubAgentState) -> RagSubAgentState:
    """根据输入问题和参数生成 rag_search 查询载荷。

    Args:
        state: RAG 子代理当前状态。

    Returns:
        写入 query_statements 与 tool_calls 后的状态。
    """
    question = str(state.get("input_question") or "").strip()
    arguments_obj = state.get("arguments")
    arguments = arguments_obj if isinstance(arguments_obj, dict) else {}
    query_terms_obj = state.get("query_terms")
    query_terms = [str(item or "").strip() for item in query_terms_obj] if isinstance(query_terms_obj, list) else []
    query_terms = [item for item in query_terms if item]

    constraints_obj = state.get("constraints")
    constraints = constraints_obj if isinstance(constraints_obj, dict) else {}
    max_tool_calls = max(1, min(int(constraints.get("max_tool_calls") or MAX_SUBAGENT_TOOL_CALLS), MAX_SUBAGENT_TOOL_CALLS))
    tool_results_obj = state.get("tool_results")
    tool_results = tool_results_obj if isinstance(tool_results_obj, list) else []
    if len(tool_results) >= max_tool_calls:
        state["tool_calls"] = []
        return state

    attempt_index = int(state.get("attempt_count") or 0) + 1
    state["attempt_count"] = attempt_index
    retry_arguments = dict(arguments)
    if attempt_index > 1:
        variants = build_query_variants(
            question=question,
            arguments=arguments,
            query_terms=query_terms,
            evidence_terms=_extract_retry_terms_from_evidence(state),
        )
        retry_arguments["query"] = query_variant_for_attempt(variants, attempt_index)

    payload = validate_rag_payload(build_rag_payload(question, retry_arguments, query_terms=query_terms))
    statement = {"tool_name": "rag_search", "payload": payload}
    existing_statements_obj = state.get("query_statements")
    query_statements = existing_statements_obj if isinstance(existing_statements_obj, list) else []
    query_statements.append(statement)
    state["query_statements"] = query_statements
    state["tool_calls"] = [statement]
    return state


async def node_execute_rag_search(state: RagSubAgentState, tool_gateway: MCPToolGateway) -> RagSubAgentState:
    """调用 MCP 网关执行 RAG 检索工具。

    Args:
        state: RAG 子代理当前状态。
        tool_gateway: 负责白名单校验和 MCP 调用的工具网关。

    Returns:
        写入 tool_results 后的状态。
    """
    calls_obj = state.get("tool_calls")
    calls = calls_obj if isinstance(calls_obj, list) else []
    rows = await tool_gateway.invoke_batch(calls, allow_parallel=True)
    previous_obj = state.get("tool_results")
    previous = previous_obj if isinstance(previous_obj, list) else []
    state["tool_results"] = [*previous, *rows]
    return state


async def node_normalize_results(state: RagSubAgentState) -> RagSubAgentState:
    """归一化 RAG 命中结果并计算信号强度基础统计。

    Args:
        state: RAG 子代理当前状态。

    Returns:
        写入 evidence、no_signal、reason 和 telemetry 后的状态。
    """
    rows_obj = state.get("tool_results")
    rows = rows_obj if isinstance(rows_obj, list) else []
    evidence = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        result_obj = row.get("result")
        result = result_obj if isinstance(result_obj, dict) else {}
        evidence_obj = result.get("evidence")
        evidence_rows = evidence_obj if isinstance(evidence_obj, list) else []
        for item in evidence_rows:
            if isinstance(item, dict):
                evidence.append(Evidence.model_validate(item).model_dump())

    state["evidence"] = evidence
    state["no_signal"] = len(evidence) == 0
    state["reason"] = "no_signal" if len(evidence) == 0 else "ok"
    constraints_obj = state.get("constraints")
    constraints = constraints_obj if isinstance(constraints_obj, dict) else {}
    state["telemetry"] = {
        "tool_calls": len(rows),
        "query_rounds": int(state.get("attempt_count") or 0),
        "max_tool_calls": int(
            constraints.get("max_tool_calls") or MAX_SUBAGENT_TOOL_CALLS
        ),
        "signal_count": len(evidence),
        "ok_count": sum(1 for row in rows if isinstance(row, dict) and bool(row.get("ok"))),
    }
    return state


async def node_build_response(state: RagSubAgentState) -> RagSubAgentState:
    """构建 RAG 子代理最终 SubAgentResult 响应。

    Args:
        state: RAG 子代理当前状态。

    Returns:
        写入 final_result 后的状态。
    """
    evidence_rows_obj = state.get("evidence")
    evidence_rows = evidence_rows_obj if isinstance(evidence_rows_obj, list) else []
    evidence_models = [
        Evidence.model_validate(row) for row in evidence_rows if isinstance(row, dict)
    ]
    evidence_count = len(evidence_models)

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
        evidence=evidence_models,
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
