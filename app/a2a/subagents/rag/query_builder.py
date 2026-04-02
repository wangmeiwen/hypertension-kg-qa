"""RAG 子代理查询语句生成器。"""

from __future__ import annotations

from typing import Any

from app.common.config import Config


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


def _normalize_refine_terms(arguments: dict[str, Any]) -> list[str]:
    if not isinstance(arguments, dict):
        return []
    return _normalize_query_terms(arguments.get("refine_terms") if isinstance(arguments.get("refine_terms"), list) else [])


def _compose_query(question: str, query_terms: list[str], refine_terms: list[str]) -> str:
    base = str(question or "").strip()
    if not base and query_terms:
        base = " ".join(query_terms)
    if refine_terms:
        suffix = " ".join(refine_terms)
        if base:
            return f"{base} {suffix}".strip()
        return suffix
    return base


def build_rag_payload(
    question: str,
    arguments: dict[str, Any] | None = None,
    query_terms: list[str] | None = None,
) -> dict[str, Any]:
    """构建 rag_payload。
    
    Args:
        question: 参数。
        arguments: 参数。
    
    Returns:
        执行结果。
    """
    conf = Config()
    q = str(question or "").strip()
    args = arguments if isinstance(arguments, dict) else {}
    terms = _normalize_query_terms(query_terms)
    refine_terms = _normalize_refine_terms(args)
    top_k = int(args.get("top_k") or conf.RAG_SEARCH_TOP_K_DEFAULT)
    top_k = max(1, min(top_k, conf.RAG_SEARCH_TOP_K_MAX))
    payload = {
        "query": _compose_query(str(args.get("query") or q).strip(), terms, refine_terms),
        "top_k": top_k,
    }
    if "source_filter" in args:
        payload["source_filter"] = args.get("source_filter")
    return payload


def validate_rag_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """校验 rag_payload。
    
    Args:
        payload: 参数。
    
    Returns:
        执行结果。
    """
    data = payload if isinstance(payload, dict) else {}
    query = str(data.get("query") or "").strip()
    if not query:
        raise ValueError("rag_search_requires_query")
    return data
