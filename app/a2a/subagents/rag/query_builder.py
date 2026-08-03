"""RAG 子代理查询语句生成器。"""

from __future__ import annotations

from typing import Any

from app.core.config import Config


def _normalize_query_terms(query_terms: list[str] | None) -> list[str]:
    """标准化 query_terms，去空并去重。"""
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
    """从参数中提取 refine_terms 并转为规范列表。"""
    if not isinstance(arguments, dict):
        return []
    return _normalize_query_terms(
        arguments.get("refine_terms")
        if isinstance(arguments.get("refine_terms"), list)
        else []
    )


def _compose_query(
    question: str, query_terms: list[str], refine_terms: list[str]
) -> str:
    """组合最终 RAG 查询文本，支持问句、扩展词和细化词。"""
    base = str(question or "").strip()
    parts = [base, *query_terms, *refine_terms]
    return " ".join(dict.fromkeys(part for part in parts if part)).strip()


def build_rag_payload(
    question: str,
    arguments: dict[str, Any] | None = None,
    query_terms: list[str] | None = None,
) -> dict[str, Any]:
    """构建 rag_search 工具调用载荷。

    Args:
        question: 主控下发的语义检索问题。
        arguments: 主控指定的检索参数，可包含 query、source_filter、refine_terms。
        query_terms: 主控抽取的查询词，用于问题为空时回退。

    Returns:
        经过 top_k 保护和可选过滤字段透传后的 RAG 检索 payload。
    """
    conf = Config()
    q = str(question or "").strip()
    args = arguments if isinstance(arguments, dict) else {}
    terms = _normalize_query_terms(query_terms)
    refine_terms = _normalize_refine_terms(args)
    # 主控策略固定使用保守 top_k，避免外部参数放大单轮召回。
    top_k = int(conf.RAG_SEARCH_TOP_K_DEFAULT)
    top_k = max(1, min(top_k, conf.RAG_SEARCH_TOP_K_MAX))
    payload = {
        "query": _compose_query(
            str(args.get("query") or q).strip(), terms, refine_terms
        ),
        "top_k": top_k,
    }
    if "source_filter" in args:
        payload["source_filter"] = args.get("source_filter")
    return payload


def validate_rag_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """校验 RAG 检索载荷是否包含可执行查询文本。

    Args:
        payload: 待校验的 rag_search 调用载荷。

    Returns:
        校验通过的载荷字典。
    """
    data = payload if isinstance(payload, dict) else {}
    query = str(data.get("query") or "").strip()
    if not query:
        raise ValueError("rag_search_requires_query")
    return data
