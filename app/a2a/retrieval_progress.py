"""按 must-cover 计算语义检索进展。"""

from __future__ import annotations

from typing import Any

from app.a2a.conflict_detection import detect_evidence_conflicts
from app.a2a.evidence_fusion import (
    evidence_identity,
    fuse_evidence,
    publication_identity,
)
from app.a2a.models import Evidence, MustCoverProgressState, QuestionPlan

_SUPPORT_SCORES = {
    "irrelevant": 0.0,
    "contradicted": 0.0,
    "uncertain": 0.1,
    "partially_supported": 0.5,
    "supported": 1.0,
}


def build_retrieval_progress(
    *,
    question_plan: QuestionPlan,
    evidence_by_item: dict[str, list[tuple[str, Evidence | dict[str, Any]]]],
) -> dict[str, MustCoverProgressState]:
    """生成不依赖 Evidence 总数的逐问题点语义快照。"""
    states: dict[str, MustCoverProgressState] = {}
    for must_cover in question_plan.must_cover:
        fused = fuse_evidence(evidence_by_item.get(must_cover.item_id, [])).evidence
        evidence_by_id = {evidence_identity(item): item for item in fused}
        assessments = [
            assessment
            for assessment in question_plan.claim_evidence_assessments
            if assessment.must_cover_id == must_cover.item_id
            and assessment.evidence_id in evidence_by_id
        ]
        supported_ids = {
            assessment.evidence_id
            for assessment in assessments
            if assessment.support_status
            in must_cover.evidence_policy.required_support_statuses
        }
        supported_evidence = [
            evidence_by_id[evidence_id]
            for evidence_id in supported_ids
            if evidence_id in evidence_by_id
        ]
        publications = {
            publication
            for evidence in supported_evidence
            if (publication := publication_identity(evidence)) is not None
        }
        matched_conditions = {
            condition
            for assessment in assessments
            if assessment.evidence_id in supported_ids
            for condition in assessment.applicability_conditions
        }
        required_conditions = set(
            must_cover.evidence_policy.required_applicability_fields
        )
        applicability_completeness = (
            len(required_conditions.intersection(matched_conditions))
            / len(required_conditions)
            if required_conditions
            else 1.0
        )
        conflicts = detect_evidence_conflicts(supported_evidence)
        states[must_cover.item_id] = MustCoverProgressState(
            item_id=must_cover.item_id,
            support_status=must_cover.support_status,
            supported_evidence_count=len(supported_evidence),
            distinct_publication_count=len(publications),
            applicability_completeness=round(applicability_completeness, 4),
            conflict_status="unresolved" if conflicts else "none",
            best_support_score=max(
                (
                    _SUPPORT_SCORES[assessment.support_status]
                    for assessment in assessments
                ),
                default=0.0,
            ),
        )
    return states


def has_semantic_progress(
    previous: MustCoverProgressState,
    current: MustCoverProgressState,
) -> bool:
    """只把支持能力提升视为进展。"""
    return any(
        (
            current.best_support_score > previous.best_support_score,
            current.distinct_publication_count > previous.distinct_publication_count,
            current.applicability_completeness > previous.applicability_completeness,
            previous.conflict_status == "unresolved"
            and current.conflict_status == "none",
        )
    )


def no_progress_reason(
    previous: MustCoverProgressState,
    current: MustCoverProgressState,
) -> str:
    """解释本轮 Evidence 未提升支持能力的原因。"""
    if current.support_status == "irrelevant":
        return "irrelevant_evidence"
    if current.applicability_completeness < 1.0:
        return "applicability_mismatch"
    if current.conflict_status == "unresolved":
        return "unresolved_conflict"
    if (
        current.supported_evidence_count > previous.supported_evidence_count
        and current.distinct_publication_count <= previous.distinct_publication_count
    ):
        return "same_source_duplicate"
    return "support_not_improved"
