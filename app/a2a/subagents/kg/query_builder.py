"""KG 子代理查询语句生成器。"""

from __future__ import annotations

from typing import Any

from app.common.config import Config


def _clean_question(text: str) -> str:
    """清理主控传入的图谱检索问题文本。

    Args:
        text: 原始问题文本。

    Returns:
        去除首尾空白后的问题文本。
    """
    return str(text or "").strip()


def _normalize_query_terms(query_terms: list[str] | None) -> list[str]:
    """标准化 query_terms，去空、去重并保持原顺序。"""
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
    """在问题为空时使用 query_terms 回退构造查询文本。"""
    if question:
        return question
    if query_terms:
        return " ".join(query_terms)
    return ""


def _normalize_refine_terms(arguments: dict[str, Any]) -> list[str]:
    """从 arguments 中提取并标准化 refine_terms。"""
    if not isinstance(arguments, dict):
        return []
    return _normalize_query_terms(arguments.get("refine_terms") if isinstance(arguments.get("refine_terms"), list) else [])


def _append_refine_terms(base_query: str, refine_terms: list[str]) -> str:
    """将 refine_terms 追加到基础查询文本。"""
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
    """构建 kg_query 工具调用载荷。

    Args:
        question: 主控下发的图谱检索问题。
        arguments: 主控指定的工具参数，可包含 keyword、cypher、refine_terms 等字段。
        query_terms: 主控抽取的查询词，用于问题为空时回退。

    Returns:
        经过 top_k/limit 保护和字段归一化后的 kg_query payload。
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
    # 主控策略固定使用保守 limit，避免外部参数放大单轮召回。
    limit = int(conf.KG_RETRIEVAL_TOP_K_DEFAULT)
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
    """构建 kg_entity_relation_search 工具调用载荷。

    Args:
        question: 主控下发的图谱检索问题。
        arguments: 主控指定的工具参数，可覆盖 query 并补充检索调参字段。
        query_terms: 主控抽取的查询词，用于问题为空时回退。

    Returns:
        经过 top_k 保护和可选字段透传后的实体关系检索 payload。
    """
    conf = Config()
    q = _clean_question(question)
    args = arguments if isinstance(arguments, dict) else {}
    terms = _normalize_query_terms(query_terms)
    refine_terms = _normalize_refine_terms(args)
    fallback_query = _fallback_query_from_terms(q, terms)
    # 主控策略固定使用保守 top_k，避免外部参数放大单轮召回。
    top_k = int(conf.KG_RETRIEVAL_TOP_K_DEFAULT)
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
    """校验生成的 KG 工具载荷是否满足目标工具的最低要求。

    Args:
        tool_name: 目标 KG 工具名。
        payload: 待校验的工具调用载荷。

    Returns:
        校验通过的载荷字典。
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
