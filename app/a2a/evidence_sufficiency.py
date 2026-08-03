"""按结论类型和医疗风险评估 Evidence 充分性。"""

from __future__ import annotations

from typing import Any

from app.a2a.conflict_detection import detect_evidence_conflicts
from app.a2a.evidence_fusion import fuse_evidence
from app.a2a.models import (
    AgentType,
    Evidence,
    EvidenceSufficiencyAssessment,
    MustCoverSufficiency,
    QuestionPlan,
)

_MULTI_SOURCE_CLAIM_TYPES = {"numeric", "comparison"}


def _available_evidence_types(
    rows: list[tuple[str, Evidence | dict[str, Any]]],
    evidence: list[Evidence],
) -> list[AgentType]:
    """根据代理来源和 Evidence 类型确定已取得的证据类型。"""
    available: set[AgentType] = set()
    for agent, _ in rows:
        if agent == "kg":
            available.add("kg")
        elif agent == "rag":
            available.add("rag")
    for item in evidence:
        if item.source_type == "knowledge_graph":
            available.add("kg")
        if item.source_type == "document":
            available.add("rag")
    return sorted(available)


def assess_evidence_sufficiency(
    *,
    question_plan: QuestionPlan,
    evidence_by_item: dict[str, list[tuple[str, Evidence | dict[str, Any]]]],
    minimum_distinct_publications: int,
) -> EvidenceSufficiencyAssessment:
    """逐个 must-cover 评估证据类型、来源数、冲突和覆盖状态。"""
    item_results: list[MustCoverSufficiency] = []
    for item in question_plan.must_cover:
        rows = evidence_by_item.get(item.item_id, [])
        fusion = fuse_evidence(rows)
        available_types = _available_evidence_types(rows, fusion.evidence)
        required_sources = max(
            minimum_distinct_publications,
            2 if item.claim_type in _MULTI_SOURCE_CLAIM_TYPES else 1,
        )
        missing_reasons: list[str] = []
        if item.support_status != "supported":
            missing_reasons.append("claim_not_supported")
        if not fusion.evidence:
            missing_reasons.append("direct_evidence")
        if fusion.evidence and fusion.distinct_publication_count < required_sources:
            missing_reasons.append("distinct_publications")
        if item.claim_type == "relation" and "kg" not in available_types:
            missing_reasons.append("knowledge_graph_evidence")
        if item.claim_type == "source" and "rag" not in available_types:
            missing_reasons.append("document_evidence")
        if detect_evidence_conflicts(fusion.evidence):
            missing_reasons.append("unresolved_conflict")
        item_results.append(
            MustCoverSufficiency(
                item_id=item.item_id,
                claim_type=item.claim_type,
                support_status=item.support_status,
                sufficient=not missing_reasons,
                evidence_count=len(fusion.evidence),
                distinct_publication_count=fusion.distinct_publication_count,
                required_publication_count=required_sources,
                available_evidence_types=available_types,
                missing_reasons=missing_reasons,
            )
        )

    sufficient_count = sum(1 for item in item_results if item.sufficient)
    total = len(item_results)
    return EvidenceSufficiencyAssessment(
        sufficient=bool(total) and sufficient_count == total,
        sufficiency_ratio=round(sufficient_count / total, 4) if total else 0.0,
        items=item_results,
    )
