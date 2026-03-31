"""RAG 子代理查询语句生成器。"""

from __future__ import annotations

from typing import Any

from app.common.config import Config


def build_rag_payload(question: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
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
    top_k = int(args.get("top_k") or conf.RAG_SEARCH_TOP_K_DEFAULT)
    top_k = max(1, min(top_k, conf.RAG_SEARCH_TOP_K_MAX))
    payload = {
        "query": str(args.get("query") or q).strip(),
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
