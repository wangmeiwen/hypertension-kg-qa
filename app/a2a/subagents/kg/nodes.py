"""KG 子代理 LangGraph 节点。"""

from __future__ import annotations

from typing import Any

from app.a2a.tool_gateway import MCPToolGateway
from app.a2a.models import SubAgentResult
from app.a2a.subagents.kg.query_builder import (
    build_kg_entity_relation_payload,
    build_kg_query_payload,
    validate_generated_payload,
)
from app.a2a.subagents.retry_utils import (
    MAX_SUBAGENT_TOOL_CALLS,
    build_query_variants,
    query_variant_for_attempt,
)
from app.a2a.subagents.kg.state import KgSubAgentState


_KG_QUERY_HINTS = (
    "列出",
    "所有节点",
    "全部节点",
    "节点详情",
    "属性",
    "标签",
    "id",
    "cypher",
    "match",
)

_KG_COMPLEX_RELATION_HINTS = (
    "合并",
    "并发",
    "蛋白尿",
    "ckd",
    "慢性肾",
    "肾病",
    "药物类别",
    "代表药",
    "治疗",
    "推荐",
    "机制",
    "关系",
    "路径",
    "风险",
    "禁忌",
    "联用",
    "作用",
)


def _select_kg_tool_name(question: str, arguments: dict[str, Any]) -> str:
    """由 KG 子代理根据问题语义选择底层图谱工具。"""
    text = f"{str(question or '').strip()} {' '.join(str(item or '') for item in arguments.values())}".lower()
    if any(key in arguments for key in ("cypher", "list_all_nodes", "keyword")):
        return "kg_query"
    if any(hint in text for hint in _KG_COMPLEX_RELATION_HINTS):
        return "kg_dual_search"
    if any(hint in text for hint in _KG_QUERY_HINTS):
        return "kg_query"
    return "kg_entity_relation_search"


def _build_query_records(
    query_statements: list[dict[str, Any]],
    tool_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """将工具原始结果整理为统一 query_records 结构，供主控复用。"""
    records: list[dict[str, Any]] = []
    for index, statement in enumerate(query_statements):
        if not isinstance(statement, dict):
            continue
        tool_name = str(statement.get("tool_name") or "").strip() or "kg_entity_relation_search"
        payload_obj = statement.get("payload")
        payload = payload_obj if isinstance(payload_obj, dict) else {}
        query = str(payload.get("query") or payload.get("keyword") or payload.get("cypher") or "").strip()
        if not query:
            continue
        row = tool_results[index] if index < len(tool_results) and isinstance(tool_results[index], dict) else {}
        result_obj = row.get("result")
        result = result_obj if isinstance(result_obj, dict) else {}
        content_parts: list[str] = []
        citations: list[str] = []

        records_obj = result.get("records")
        result_records = records_obj if isinstance(records_obj, list) else []
        for item in result_records[:3]:
            if isinstance(item, dict):
                content_parts.append(str(item).replace("\n", " ").strip()[:280])

        entities_obj = result.get("entities")
        entities = entities_obj if isinstance(entities_obj, list) else []
        for entity in entities[:3]:
            if not isinstance(entity, dict):
                continue
            content_parts.append(str(entity).replace("\n", " ").strip()[:280])
            citation = str(entity.get("id") or entity.get("name") or "").strip()
            if citation and citation not in citations:
                citations.append(citation)

        relationships_obj = result.get("relationships")
        relationships = relationships_obj if isinstance(relationships_obj, list) else []
        for relation in relationships[:3]:
            if not isinstance(relation, dict):
                continue
            content_parts.append(str(relation).replace("\n", " ").strip()[:280])
            citation = str(relation.get("id") or relation.get("relation") or relation.get("type") or "").strip()
            if citation and citation not in citations:
                citations.append(citation)

        records.append(
            {
                "agent": "kg",
                "query": query,
                "result_content": " | ".join(content_parts)[:1600],
                "captured_at": "",
                "citations": citations[:6],
                "has_signal": bool(content_parts),
                "tool_name": tool_name,
            }
        )
    return records


async def node_validate_constraints(state: KgSubAgentState) -> KgSubAgentState:
    """校验 KG 子代理执行约束并收敛最大工具调用次数。

    Args:
        state: KG 子代理当前状态。

    Returns:
        写入规范化 constraints 后的状态。
    """
    constraints_obj = state.get("constraints")
    constraints = constraints_obj if isinstance(constraints_obj, dict) else {}
    if bool(constraints.get("allow_extra_retrieval")):
        raise PermissionError("kg_subagent_extra_retrieval_not_allowed")
    max_tool_calls = int(constraints.get("max_tool_calls") or MAX_SUBAGENT_TOOL_CALLS)
    constraints["max_tool_calls"] = max(1, min(max_tool_calls, MAX_SUBAGENT_TOOL_CALLS))
    state["constraints"] = constraints
    return state


def _resolve_tool_plan(state: KgSubAgentState) -> list[dict[str, Any]]:
    """根据请求工具名和参数生成实际要执行的 KG 工具计划。

    Args:
        state: KG 子代理当前状态。

    Returns:
        工具调用计划列表，每项包含工具名、问题、参数和查询词。
    """
    question = str(state.get("input_question") or "").strip()
    arguments_obj = state.get("arguments")
    arguments = arguments_obj if isinstance(arguments_obj, dict) else {}
    tool_name = str(state.get("tool_name") or "").strip()
    if tool_name in {"", "auto", "kg_auto"}:
        tool_name = _select_kg_tool_name(question, arguments)
    query_terms_obj = state.get("query_terms")
    query_terms = [str(item or "").strip() for item in query_terms_obj] if isinstance(query_terms_obj, list) else []
    query_terms = [item for item in query_terms if item]

    if tool_name in {"kg_query", "kg_entity_relation_search"}:
        return [{"tool_name": tool_name, "arguments": arguments, "question": question, "query_terms": query_terms}]

    if tool_name in {"kg_composite", "kg_dual_search"}:
        return [
            {"tool_name": "kg_entity_relation_search", "arguments": arguments, "question": question, "query_terms": query_terms},
            {"tool_name": "kg_query", "arguments": arguments, "question": question, "query_terms": query_terms},
        ]

    return [{"tool_name": "kg_entity_relation_search", "arguments": arguments, "question": question, "query_terms": query_terms}]


def _extract_retry_terms_from_evidence(state: KgSubAgentState) -> list[str]:
    """从已有 KG 证据中抽取下一轮检索可用的实体/关系词。"""
    evidence_obj = state.get("evidence")
    evidence = evidence_obj if isinstance(evidence_obj, list) else []
    terms: list[str] = []
    for row in evidence:
        item = row if isinstance(row, dict) else {}
        for key in ("entity", "relationship", "record"):
            payload_obj = item.get(key)
            payload = payload_obj if isinstance(payload_obj, dict) else {}
            properties_obj = payload.get("properties")
            properties = properties_obj if isinstance(properties_obj, dict) else payload
            for field in ("name", "canonical_name", "alias", "relation", "type"):
                value = str(properties.get(field) or "").strip()
                if value:
                    terms.append(value)
    return terms


def _arguments_for_attempt(state: KgSubAgentState, tool_name: str, attempt_index: int) -> dict[str, Any]:
    """根据当前轮次生成 KG 工具参数。"""
    arguments_obj = state.get("arguments")
    arguments = dict(arguments_obj) if isinstance(arguments_obj, dict) else {}
    question = str(state.get("input_question") or "").strip()
    query_terms_obj = state.get("query_terms")
    query_terms = [str(item or "").strip() for item in query_terms_obj] if isinstance(query_terms_obj, list) else []
    variants = build_query_variants(
        question=question,
        arguments=arguments,
        query_terms=query_terms,
        evidence_terms=_extract_retry_terms_from_evidence(state),
    )
    selected_query = query_variant_for_attempt(variants, attempt_index)
    if attempt_index <= 1:
        return arguments
    retry_arguments = dict(arguments)
    retry_arguments.pop("cypher", None)
    retry_arguments["keyword" if tool_name == "kg_query" else "query"] = selected_query
    return retry_arguments


async def node_generate_query_statements(state: KgSubAgentState) -> KgSubAgentState:
    """根据工具计划生成 KG 查询语句和工具调用载荷。

    Args:
        state: KG 子代理当前状态。

    Returns:
        写入 query_statements 与 tool_calls 后的状态。
    """
    constraints = state.get("constraints") if isinstance(state.get("constraints"), dict) else {}
    max_tool_calls = max(1, min(int(constraints.get("max_tool_calls") or MAX_SUBAGENT_TOOL_CALLS), MAX_SUBAGENT_TOOL_CALLS))
    tool_results_obj = state.get("tool_results")
    tool_results = tool_results_obj if isinstance(tool_results_obj, list) else []
    remaining_calls = max_tool_calls - len(tool_results)
    if remaining_calls <= 0:
        state["tool_calls"] = []
        return state

    attempt_index = int(state.get("attempt_count") or 0) + 1
    state["attempt_count"] = attempt_index
    plans = _resolve_tool_plan(state)[:remaining_calls]

    existing_statements_obj = state.get("query_statements")
    query_statements = existing_statements_obj if isinstance(existing_statements_obj, list) else []
    tool_calls: list[dict[str, Any]] = []

    for plan in plans:
        tool_name = str(plan.get("tool_name") or "").strip()
        question = str(plan.get("question") or "").strip()
        arguments = _arguments_for_attempt(state, tool_name, attempt_index)
        query_terms_obj = plan.get("query_terms")
        query_terms = [str(item or "").strip() for item in query_terms_obj] if isinstance(query_terms_obj, list) else []
        query_terms = [item for item in query_terms if item]

        if tool_name == "kg_query":
            payload = build_kg_query_payload(question, arguments, query_terms=query_terms)
        else:
            payload = build_kg_entity_relation_payload(question, arguments, query_terms=query_terms)

        payload = validate_generated_payload(tool_name, payload)
        query_statements.append({"tool_name": tool_name, "payload": payload})
        tool_calls.append({"tool_name": tool_name, "payload": payload})

    state["query_statements"] = query_statements
    state["tool_calls"] = tool_calls
    return state


async def node_execute_tools(state: KgSubAgentState, tool_gateway: MCPToolGateway) -> KgSubAgentState:
    """调用 MCP 网关执行 KG 工具调用列表。

    Args:
        state: KG 子代理当前状态。
        tool_gateway: 负责白名单校验和 MCP 调用的工具网关。

    Returns:
        写入 tool_results 后的状态。
    """
    calls_obj = state.get("tool_calls")
    calls = calls_obj if isinstance(calls_obj, list) else []
    tool_results = await tool_gateway.invoke_batch(calls, allow_parallel=True)
    previous_obj = state.get("tool_results")
    previous = previous_obj if isinstance(previous_obj, list) else []
    state["tool_results"] = [*previous, *tool_results]
    return state


def _extract_evidence_from_row(row: dict[str, Any]) -> list[dict[str, Any]]:
    """从单条 KG 工具结果中抽取统一证据列表。

    Args:
        row: 标准化后的工具调用结果。

    Returns:
        包含实体、关系或 Cypher 记录的证据项列表。
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
    """归一化 KG 工具结果并计算信号强度基础统计。

    Args:
        state: KG 子代理当前状态。

    Returns:
        写入 evidence、no_signal、reason 和 telemetry 后的状态。
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
        "query_rounds": int(state.get("attempt_count") or 0),
        "max_tool_calls": int((state.get("constraints") if isinstance(state.get("constraints"), dict) else {}).get("max_tool_calls") or MAX_SUBAGENT_TOOL_CALLS),
        "signal_count": len(evidence),
        "ok_count": sum(1 for row in rows if isinstance(row, dict) and bool(row.get("ok"))),
    }
    return state


async def node_build_response(state: KgSubAgentState) -> KgSubAgentState:
    """构建 KG 子代理最终 SubAgentResult 响应。

    Args:
        state: KG 子代理当前状态。

    Returns:
        写入 final_result 后的状态。
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
        query = str(payload.get("query") or payload.get("keyword") or payload.get("cypher") or "").strip()
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
        target_agent="kg",
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
        suggested_next_route="rag" if evidence_count == 0 else "kg",
        no_signal=bool(state.get("no_signal")),
        reason=str(state.get("reason") or ""),
        telemetry=state.get("telemetry") or {},
    )
    state["final_result"] = result.model_dump()
    return state
