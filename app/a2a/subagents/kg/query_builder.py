"""KG 子代理查询语句生成器。"""

from __future__ import annotations

from typing import Any

from app.common.config import Config


def _clean_question(text: str) -> str:
    """执行 _clean_question 内部逻辑。
    
    Args:
        text: 参数。
    
    Returns:
        执行结果。
    """
    return str(text or "").strip()


def _normalize_query_terms(query_terms: list[str] | None) -> list[str]:
    if not isinstance(query_terms, list):
        return []
    seen: set[str] = set()
    rows: list[str] = []
    for item in query_terms:
        token = str(item or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        rows.append(token)
    return rows


def _fallback_query_from_terms(question: str, query_terms: list[str]) -> str:
    if question:
        return question
    if query_terms:
        return " ".join(query_terms)
    return ""


def _normalize_refine_terms(arguments: dict[str, Any]) -> list[str]:
    if not isinstance(arguments, dict):
        return []
    return _normalize_query_terms(arguments.get("refine_terms") if isinstance(arguments.get("refine_terms"), list) else [])


def _append_refine_terms(base_query: str, refine_terms: list[str]) -> str:
    if not refine_terms:
        return str(base_query or "").strip()
    base = str(base_query or "").strip()
    suffix = " ".join(refine_terms)
    if not base:
        return suffix
    return f"{base} {suffix}".strip()


def build_kg_query_payload(
    question: str,
    arguments: dict[str, Any] | None = None,
    query_terms: list[str] | None = None,
) -> dict[str, Any]:
    """构建 kg_query_payload。
    
    Args:
        question: 参数。
        arguments: 参数。
    
    Returns:
        执行结果。
    """
    conf = Config()
    q = _clean_question(question)
    args = arguments if isinstance(arguments, dict) else {}
    terms = _normalize_query_terms(query_terms)
    refine_terms = _normalize_refine_terms(args)
    fallback_query = _fallback_query_from_terms(q, terms)
    cypher = str(args.get("cypher") or "").strip()
    keyword = _append_refine_terms(str(args.get("keyword") or fallback_query).strip(), refine_terms)
    list_all_nodes = bool(args.get("list_all_nodes"))
    limit = int(args.get("limit") or conf.KG_RETRIEVAL_TOP_K_DEFAULT)
    limit = max(1, min(limit, conf.KG_RETRIEVAL_TOP_K_MAX))
    payload = {
        "keyword": keyword,
        "list_all_nodes": list_all_nodes,
        "limit": limit,
    }
    if cypher:
        payload["cypher"] = cypher
    return payload


def build_kg_entity_relation_payload(
    question: str,
    arguments: dict[str, Any] | None = None,
    query_terms: list[str] | None = None,
) -> dict[str, Any]:
    """构建 kg_entity_relation_payload。
    
    Args:
        question: 参数。
        arguments: 参数。
    
    Returns:
        执行结果。
    """
    conf = Config()
    q = _clean_question(question)
    args = arguments if isinstance(arguments, dict) else {}
    terms = _normalize_query_terms(query_terms)
    refine_terms = _normalize_refine_terms(args)
    fallback_query = _fallback_query_from_terms(q, terms)
    top_k = int(args.get("top_k") or args.get("limit") or conf.KG_RETRIEVAL_TOP_K_DEFAULT)
    top_k = max(1, min(top_k, conf.KG_RETRIEVAL_TOP_K_MAX))
    payload = {
        "query": _append_refine_terms(str(args.get("query") or fallback_query).strip(), refine_terms),
        "top_k": top_k,
    }
    optional_fields = [
        "lexical_weight",
        "expansion_limit",
        "enable_disambiguation",
        "enable_community_detection",
        "community_top_k",
        "disambiguation_margin",
    ]
    for field in optional_fields:
        if field in args:
            payload[field] = args.get(field)
    return payload


def validate_generated_payload(tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """校验 generated_payload。
    
    Args:
        tool_name: 参数。
        payload: 参数。
    
    Returns:
        执行结果。
    """
    safe_tool = str(tool_name or "").strip()
    if safe_tool not in {"kg_query", "kg_entity_relation_search"}:
        raise ValueError(f"unsupported_kg_tool:{safe_tool}")
    data = payload if isinstance(payload, dict) else {}
    if safe_tool == "kg_query":
        if not any(k in data for k in ("keyword", "cypher", "list_all_nodes")):
            raise ValueError("kg_query_requires_keyword_or_cypher")
        return data
    if not str(data.get("query") or "").strip():
        raise ValueError("kg_entity_relation_search_requires_query")
    return data
