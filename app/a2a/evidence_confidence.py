"""根据已验证证据状态输出离散置信度等级。"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.a2a.models import (
    AnswerGenerationContext,
    AnswerValidationResult,
    ConfidenceBreakdown,
    ConfidenceLevel,
    EvidenceSufficiencyAssessment,
)


def _validated_sufficiency(
    value: EvidenceSufficiencyAssessment | dict[str, Any] | None,
) -> EvidenceSufficiencyAssessment | None:
    if value is None:
        return None
    try:
        return EvidenceSufficiencyAssessment.model_validate(value)
    except (ValidationError, ValueError, TypeError):
        return None


def assess_evidence_confidence(
    *,
    sufficiency: EvidenceSufficiencyAssessment | dict[str, Any] | None,
    answer_context: AnswerGenerationContext | None = None,
    answer_validation: AnswerValidationResult | None = None,
) -> ConfidenceBreakdown:
    """只使用支持状态、出版来源、适用条件、冲突和引用计算等级。"""
    validated = _validated_sufficiency(sufficiency)
    if validated is None or not validated.items:
        return ConfidenceBreakdown(reasons=["missing_evidence_sufficiency"])

    total = len(validated.items)
    supported = sum(item.support_status == "supported" for item in validated.items)
    applicability_matched = sum(item.applicability_complete for item in validated.items)
    publication_ids = {
        evidence.publication_id
        for must_cover in (answer_context.must_cover if answer_context else [])
        for evidence in must_cover.supporting_evidence
        if evidence.publication_id
    }
    publications_by_item = {
        must_cover.item_id: {
            evidence.publication_id
            for evidence in must_cover.supporting_evidence
            if evidence.publication_id
        }
        for must_cover in (answer_context.must_cover if answer_context else [])
    }
    diversity_rate = (
        sum(
            min(
                (
                    len(publications_by_item[item.item_id])
                    if item.item_id in publications_by_item
                    else item.distinct_publication_count
                )
                / 2,
                1.0,
            )
            for item in validated.items
        )
        / total
    )
    unresolved_conflicts = sum(
        item.unresolved_conflict_count for item in validated.items
    )
    if answer_context is not None:
        unresolved_conflicts = max(
            unresolved_conflicts,
            sum(
                conflict.status == "unresolved" for conflict in answer_context.conflicts
            ),
        )

    covered = (
        len(set(answer_validation.covered_must_cover_ids))
        if answer_validation is not None
        else 0
    )
    support_rate = supported / total
    applicability_rate = applicability_matched / total
    citation_rate = min(covered / total, 1.0)
    reasons: list[str] = []
    if not validated.sufficient:
        reasons.append("evidence_policy_not_satisfied")
    if support_rate < 1:
        reasons.append("must_cover_not_fully_supported")
    if diversity_rate < 1:
        reasons.append("limited_publication_diversity")
    if applicability_rate < 1:
        reasons.append("applicability_incomplete")
    if unresolved_conflicts:
        reasons.append("unresolved_conflict")
    if citation_rate < 1:
        reasons.append("citation_support_incomplete")

    eligible = (
        validated.sufficient
        and support_rate == 1
        and applicability_rate == 1
        and unresolved_conflicts == 0
        and citation_rate == 1
    )
    level: ConfidenceLevel = (
        "high" if eligible and diversity_rate == 1 else "medium" if eligible else "low"
    )
    return ConfidenceBreakdown(
        level=level,
        must_cover_support_rate=round(support_rate, 4),
        publication_diversity_rate=round(diversity_rate, 4),
        applicability_match_rate=round(applicability_rate, 4),
        citation_support_rate=round(citation_rate, 4),
        unresolved_conflict_count=unresolved_conflicts,
        supported_must_cover_count=supported,
        total_must_cover_count=total,
        distinct_publication_count=len(publication_ids),
        reasons=reasons,
    )
