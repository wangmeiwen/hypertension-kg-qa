"""证据置信度等级测试。"""

from __future__ import annotations

from app.a2a.evidence_confidence import assess_evidence_confidence
from app.a2a.models import (
    AnswerEvidenceContext,
    AnswerGenerationContext,
    AnswerMustCoverContext,
    AnswerValidationResult,
    EvidenceSufficiencyAssessment,
    MinimumEvidencePolicy,
    MustCoverSufficiency,
)


def _sufficiency(
    *, publications: int, conflicts: int = 0, evidence_count: int | None = None
) -> EvidenceSufficiencyAssessment:
    return EvidenceSufficiencyAssessment(
        sufficient=conflicts == 0,
        sufficiency_ratio=1.0 if conflicts == 0 else 0.0,
        items=[
            MustCoverSufficiency(
                item_id="mc-1",
                claim_type="fact",
                support_status="supported",
                sufficient=conflicts == 0,
                evidence_count=(
                    evidence_count if evidence_count is not None else publications
                ),
                supported_evidence_count=publications,
                distinct_publication_count=publications,
                unresolved_conflict_count=conflicts,
            )
        ],
    )


def _answer_context(*publication_ids: str) -> AnswerGenerationContext:
    return AnswerGenerationContext(
        question="高血压如何治疗？",
        plan_id="plan-1",
        must_cover=[
            AnswerMustCoverContext(
                item_id="mc-1",
                question="治疗建议",
                claim_type="fact",
                support_status="supported",
                evidence_policy=MinimumEvidencePolicy(),
                supporting_evidence=[
                    AnswerEvidenceContext(
                        task_id=f"task-{index}",
                        must_cover_id="mc-1",
                        evidence_id=f"evidence-{index}",
                        citation=f"[guide-{index}]",
                        publication_id=publication_id,
                        source_type="document",
                        support_status="supported",
                        support_excerpt="推荐规范治疗",
                    )
                    for index, publication_id in enumerate(publication_ids, start=1)
                ],
            )
        ],
    )


def _valid_answer() -> AnswerValidationResult:
    return AnswerValidationResult(valid=True, covered_must_cover_ids=["mc-1"])


def test_two_independent_publications_produce_high_confidence() -> None:
    result = assess_evidence_confidence(
        sufficiency=_sufficiency(publications=2),
        answer_context=_answer_context("publication-a", "publication-b"),
        answer_validation=_valid_answer(),
    )

    assert result.level == "high"
    assert result.publication_diversity_rate == 1.0
    assert result.distinct_publication_count == 2
    assert result.reasons == []


def test_one_publication_produces_medium_confidence() -> None:
    result = assess_evidence_confidence(
        sufficiency=_sufficiency(publications=1),
        answer_context=_answer_context("publication-a"),
        answer_validation=_valid_answer(),
    )

    assert result.level == "medium"
    assert result.publication_diversity_rate == 0.5
    assert result.reasons == ["limited_publication_diversity"]


def test_same_source_duplicates_do_not_raise_confidence() -> None:
    result = assess_evidence_confidence(
        sufficiency=_sufficiency(publications=1),
        answer_context=_answer_context("publication-a", "publication-a"),
        answer_validation=_valid_answer(),
    )

    assert result.level == "medium"
    assert result.distinct_publication_count == 1


def test_unvalidated_evidence_volume_does_not_raise_confidence() -> None:
    result = assess_evidence_confidence(
        sufficiency=_sufficiency(publications=1, evidence_count=20),
        answer_context=_answer_context("publication-a"),
        answer_validation=_valid_answer(),
    )

    assert result.level == "medium"
    assert result.publication_diversity_rate == 0.5


def test_unresolved_conflict_forces_low_confidence() -> None:
    result = assess_evidence_confidence(
        sufficiency=_sufficiency(publications=2, conflicts=1),
        answer_context=_answer_context("publication-a", "publication-b"),
        answer_validation=_valid_answer(),
    )

    assert result.level == "low"
    assert result.unresolved_conflict_count == 1
    assert "unresolved_conflict" in result.reasons


def test_missing_claim_citation_forces_low_confidence() -> None:
    result = assess_evidence_confidence(
        sufficiency=_sufficiency(publications=2),
        answer_context=_answer_context("publication-a", "publication-b"),
        answer_validation=AnswerValidationResult(
            valid=False,
            missing_must_cover_ids=["mc-1"],
            missing_citation_ids=["mc-1"],
        ),
    )

    assert result.level == "low"
    assert result.citation_support_rate == 0.0
    assert "citation_support_incomplete" in result.reasons


def test_missing_sufficiency_returns_explained_low_confidence() -> None:
    result = assess_evidence_confidence(sufficiency=None)

    assert result.level == "low"
    assert result.reasons == ["missing_evidence_sufficiency"]
