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


def build_kg_query_payload(question: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
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
    cypher = str(args.get("cypher") or "").strip()
    keyword = str(args.get("keyword") or q).strip()
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
    top_k = int(args.get("top_k") or args.get("limit") or conf.KG_RETRIEVAL_TOP_K_DEFAULT)
    top_k = max(1, min(top_k, conf.KG_RETRIEVAL_TOP_K_MAX))
    payload = {
        "query": str(args.get("query") or q).strip(),
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
