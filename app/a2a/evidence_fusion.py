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
    distinct_publication_count: int
    corroborated_claim_count: int


def _normalized_text(value: str) -> str:
    """生成用于比对的稳定文本。"""
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", value.casefold())


def _content_fingerprint(evidence: Evidence) -> str:
    """生成证据内容指纹。"""
    normalized = _normalized_text(evidence.content)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def evidence_identity(evidence: Evidence) -> str:
    """生成单条 Evidence 的稳定对象身份。"""
    source_type = _normalized_text(evidence.source_type) or "unknown"
    source_id = _normalized_text(evidence.source_id)
    if source_id:
        return f"evidence:{source_type}:{source_id}"
    return f"evidence:{source_type}:content:{_content_fingerprint(evidence)}"


def publication_identity(evidence: Evidence) -> str | None:
    """仅使用可信来源字段生成出版物身份。"""
    candidates = (
        ("guideline", evidence.origin_guideline_id),
        ("document", evidence.origin_document_id),
        ("hash", evidence.source_hash),
    )
    for prefix, value in candidates:
        normalized = _normalized_text(value)
        if normalized:
            return f"publication:{prefix}:{normalized}"
    return None


def _deduplication_key(evidence: Evidence) -> tuple[str, int | None, str, str]:
    """构建同一来源内的精确去重键。"""
    return (
        publication_identity(evidence) or evidence_identity(evidence),
        evidence.source_page,
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

    claim_publications: dict[str, set[str]] = {}
    for _, evidence in validated:
        publication = publication_identity(evidence)
        if publication is not None:
            claim_publications.setdefault(_content_fingerprint(evidence), set()).add(
                publication
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
        publication_ids = sorted(claim_publications.get(fingerprint, set()))
        representative.provenance = {
            **representative.provenance,
            "evidence_identity": evidence_identity(representative),
            "publication_identity": publication_identity(representative),
            "supporting_agents": agents,
            "retrieval_methods": methods,
            "duplicate_count": len(evidence_rows) - 1,
            "supporting_publication_ids": publication_ids,
            "distinct_publication_count": len(publication_ids),
            "unknown_origin": not publication_ids,
        }
        fused.append(representative)

    distinct_publications = {
        identity
        for item in fused
        if (identity := publication_identity(item)) is not None
    }
    corroborated_claims = sum(
        1
        for publication_ids in claim_publications.values()
        if len(publication_ids) >= 2
    )
    return EvidenceFusionResult(
        evidence=fused,
        raw_evidence_count=len(validated),
        duplicate_count=len(validated) - len(fused),
        distinct_publication_count=len(distinct_publications),
        corroborated_claim_count=corroborated_claims,
    )
