"""跨代理 Evidence 去重、来源归一化与支持度融合。"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Iterable

from pydantic import ValidationError

from app.a2a.models import Evidence


@dataclass(frozen=True)
class EvidenceFusionResult:
    """一次证据融合的确定性结果。"""

    evidence: list[Evidence]
    raw_evidence_count: int
    duplicate_count: int
    distinct_source_count: int
    corroborated_claim_count: int


def _normalized_text(value: str) -> str:
    """生成用于比对的稳定文本。"""
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", value.casefold())


def _content_fingerprint(evidence: Evidence) -> str:
    """生成证据内容指纹。"""
    normalized = _normalized_text(evidence.content)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def canonical_source_id(evidence: Evidence) -> str:
    """按文档、来源或内容生成规范化来源标识。"""
    source_type = _normalized_text(evidence.source_type) or "unknown"
    document_id = _normalized_text(evidence.document_id)
    source_id = _normalized_text(evidence.source_id)
    if document_id:
        return f"origin:{document_id}"
    if source_id:
        return f"origin:{source_id}"
    return f"{source_type}:content:{_content_fingerprint(evidence)}"


def _deduplication_key(evidence: Evidence) -> tuple[str, int | None, str, str]:
    """构建同一来源内的精确去重键。"""
    return (
        canonical_source_id(evidence),
        evidence.page,
        _normalized_text(evidence.section),
        _content_fingerprint(evidence),
    )


def _validated_rows(
    rows: Iterable[tuple[str, Evidence | dict[str, Any]]],
) -> list[tuple[str, Evidence]]:
    """过滤不满足共享 Evidence 契约的输入。"""
    validated: list[tuple[str, Evidence]] = []
    for agent, row in rows:
        try:
            evidence = (
                row if isinstance(row, Evidence) else Evidence.model_validate(row)
            )
        except ValidationError:
            continue
        if not evidence.content.strip():
            continue
        validated.append((str(agent).strip() or "unknown", evidence))
    return validated


def fuse_evidence(
    rows: Iterable[tuple[str, Evidence | dict[str, Any]]],
) -> EvidenceFusionResult:
    """融合跨 KG、RAG、历史读取的 Evidence。"""
    validated = _validated_rows(rows)
    grouped: dict[tuple[str, int | None, str, str], list[tuple[str, Evidence]]] = {}
    for agent, evidence in validated:
        grouped.setdefault(_deduplication_key(evidence), []).append((agent, evidence))

    claim_sources: dict[str, set[str]] = {}
    for _, evidence in validated:
        claim_sources.setdefault(_content_fingerprint(evidence), set()).add(
            canonical_source_id(evidence)
        )

    fused: list[Evidence] = []
    for duplicates in grouped.values():
        agents = sorted({agent for agent, _ in duplicates})
        evidence_rows = [evidence for _, evidence in duplicates]
        representative = evidence_rows[0].model_copy(deep=True)
        scores = [item.score for item in evidence_rows if item.score is not None]
        if scores:
            representative.score = max(scores)
        methods = sorted(
            {
                item.retrieval_method
                for item in evidence_rows
                if item.retrieval_method.strip()
            }
        )
        fingerprint = _content_fingerprint(representative)
        source_ids = sorted(claim_sources[fingerprint])
        representative.provenance = {
            **representative.provenance,
            "canonical_source_id": canonical_source_id(representative),
            "supporting_agents": agents,
            "retrieval_methods": methods,
            "duplicate_count": len(evidence_rows) - 1,
            "supporting_source_ids": source_ids,
            "independent_source_count": len(source_ids),
        }
        fused.append(representative)

    distinct_sources = {canonical_source_id(item) for item in fused}
    corroborated_claims = sum(
        1 for source_ids in claim_sources.values() if len(source_ids) >= 2
    )
    return EvidenceFusionResult(
        evidence=fused,
        raw_evidence_count=len(validated),
        duplicate_count=len(validated) - len(fused),
        distinct_source_count=len(distinct_sources),
        corroborated_claim_count=corroborated_claims,
    )
