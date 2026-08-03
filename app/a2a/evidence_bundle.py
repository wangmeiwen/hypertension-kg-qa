"""统一 Evidence Bundle 构建。"""

from __future__ import annotations

import hashlib
from typing import Any, Iterable

from app.a2a.conflict_detection import detect_evidence_conflicts
from app.a2a.evidence_fusion import (
    evidence_identity,
    fuse_evidence,
    publication_identity,
)
from app.a2a.models import Evidence, EvidenceBundle


def _citation(evidence: Evidence) -> str:
    """生成 Evidence Bundle 引用标签。"""
    if evidence.page is not None:
        return f"[{evidence.source_id} p{evidence.page}]"
    return f"[{evidence.source_id}]" if evidence.source_id else ""


def _supporting_agents(evidence: Evidence) -> set[str]:
    """读取融合阶段记录的支持代理。"""
    agents = evidence.provenance.get("supporting_agents")
    if not isinstance(agents, list):
        return set()
    return {str(agent).strip() for agent in agents if str(agent).strip()}


def build_evidence_bundle(
    *,
    question: str,
    plan_id: str,
    rows: Iterable[tuple[str, Evidence | dict[str, Any]]],
    coverage: dict[str, Any],
    sufficiency: dict[str, Any] | None = None,
) -> EvidenceBundle:
    """构建稳定、去重、可追踪的最终 Evidence Bundle。"""
    fusion = fuse_evidence(rows)
    evidence = fusion.evidence
    kg_evidence = [
        item
        for item in evidence
        if item.source_type == "knowledge_graph" or "kg" in _supporting_agents(item)
    ]
    rag_evidence = [
        item
        for item in evidence
        if item.source_type == "document" or "rag" in _supporting_agents(item)
    ]
    history_evidence = [
        item for item in evidence if "history" in _supporting_agents(item)
    ]
    citations = list(
        dict.fromkeys(filter(None, (_citation(item) for item in evidence)))
    )
    conflicts = detect_evidence_conflicts(evidence)
    evidence_identity_payload = "|".join(
        sorted(
            f"{evidence_identity(item)}:{publication_identity(item)}:{item.source_page}:{item.content}"
            for item in evidence
        )
    )
    digest = hashlib.sha256(
        f"{question}|{plan_id}|{evidence_identity_payload}".encode("utf-8")
    ).hexdigest()[:12]
    return EvidenceBundle(
        bundle_id=f"bundle-{digest}",
        plan_id=plan_id,
        question=question,
        evidence=evidence,
        kg_evidence=kg_evidence,
        rag_evidence=rag_evidence,
        history_evidence=history_evidence,
        citations=citations,
        conflicts=conflicts,
        coverage=dict(coverage),
        sufficiency=dict(sufficiency) if sufficiency is not None else {},
        distinct_publication_count=fusion.distinct_publication_count,
    )
