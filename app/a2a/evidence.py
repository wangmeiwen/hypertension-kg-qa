"""A2A 子代理共享 Evidence 归一化。"""

from __future__ import annotations

import json
from typing import Any, Optional

from app.a2a.models import Evidence


def _optional_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_rag_evidence(
    hit: dict[str, Any],
    *,
    tool_name: str = "rag_search",
) -> dict[str, Any]:
    """将文档检索命中转换为共享 Evidence。"""
    source_id = str(hit.get("source") or hit.get("id") or "").strip()
    chunk_id = str(hit.get("chunk_id") or hit.get("id") or "").strip()
    document_id = str(
        hit.get("document_id") or hit.get("parent_id") or source_id
    ).strip()
    section_parts = [
        str(value).strip()
        for value in (hit.get("parent_index"), hit.get("sub_index"))
        if value not in (None, "")
    ]
    provenance = {
        "tool": tool_name,
        "start_page": hit.get("start_page"),
        "end_page": hit.get("end_page"),
        "timestamp": hit.get("timestamp"),
    }
    return Evidence(
        source_type="document",
        source_id=source_id,
        document_id=document_id,
        chunk_id=chunk_id,
        page=_optional_int(hit.get("page_num") or hit.get("start_page")),
        section="/".join(section_parts),
        content=str(hit.get("text") or "").strip(),
        score=_optional_float(hit.get("score")),
        retrieval_method=str(hit.get("retrieval_method") or tool_name).strip(),
        provenance=provenance,
    ).model_dump()


def build_kg_evidence(
    item: dict[str, Any],
    *,
    tool_name: str,
    evidence_kind: str,
    retrieval_method: Optional[str] = None,
    provenance_context: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """将图谱实体、关系或 Cypher 记录转换为共享 Evidence。"""
    properties_obj = item.get("properties")
    properties = properties_obj if isinstance(properties_obj, dict) else {}
    source_id = str(
        item.get("id")
        or item.get("entity_id")
        or item.get("relationship_id")
        or item.get("doc_id")
        or item.get("source_id")
        or properties.get("id")
        or properties.get("entity_id")
        or properties.get("name")
        or ""
    ).strip()
    content_value = (
        item.get("content")
        or item.get("text")
        or item.get("name")
        or properties.get("content")
        or properties.get("text")
        or properties.get("name")
        or item
    )
    content = (
        str(content_value).strip()
        if isinstance(content_value, str)
        else json.dumps(content_value, ensure_ascii=False, default=str)
    )
    provenance = {
        "tool": tool_name,
        "kind": evidence_kind,
        "raw": dict(item),
    }
    if provenance_context:
        provenance["context"] = dict(provenance_context)
    return Evidence(
        source_type="knowledge_graph",
        source_id=source_id,
        content=content,
        score=_optional_float(item.get("score")),
        retrieval_method=str(retrieval_method or tool_name).strip(),
        provenance=provenance,
    ).model_dump()
