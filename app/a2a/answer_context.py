"""结构化终答证据上下文与后置校验。"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from pydantic import ValidationError

from app.a2a.evidence_fusion import (
    evidence_identity,
    fuse_evidence,
    publication_identity,
)
from app.a2a.models import (
    AnswerEvidenceContext,
    AnswerGenerationContext,
    AnswerMustCoverContext,
    AnswerValidationResult,
    Evidence,
    EvidenceConflict,
    QuestionPlan,
)


def evidence_citation(evidence: Evidence) -> str:
    """为已校验 Evidence 生成稳定引用标签。"""
    source_id = evidence.source_id.replace("\n", " ").strip()
    page = evidence.source_page if evidence.source_page is not None else evidence.page
    if page is not None:
        return f"[{source_id} p{page}]" if source_id else f"[p{page}]"
    return f"[{source_id}]" if source_id else ""


def build_answer_generation_context(
    *,
    question_plan: QuestionPlan,
    evidence_by_item: dict[str, list[tuple[str, Evidence | dict[str, Any]]]],
    conflicts: Iterable[EvidenceConflict | dict[str, Any]],
) -> AnswerGenerationContext:
    """只把通过支持校验的 Evidence 绑定到对应 must-cover。"""
    must_cover_contexts: list[AnswerMustCoverContext] = []
    for must_cover in question_plan.must_cover:
        fused = fuse_evidence(evidence_by_item.get(must_cover.item_id, [])).evidence
        evidence_by_id = {evidence_identity(item): item for item in fused}
        supporting_evidence: list[AnswerEvidenceContext] = []
        seen_evidence_ids: set[str] = set()
        for assessment in question_plan.claim_evidence_assessments:
            if (
                assessment.must_cover_id != must_cover.item_id
                or assessment.support_status
                not in must_cover.evidence_policy.required_support_statuses
                or assessment.evidence_id in seen_evidence_ids
            ):
                continue
            evidence = evidence_by_id.get(assessment.evidence_id)
            if evidence is None:
                continue
            if (
                not assessment.support_excerpt
                or assessment.support_excerpt not in evidence.content
            ):
                continue
            supporting_evidence.append(
                AnswerEvidenceContext(
                    task_id=assessment.task_id,
                    must_cover_id=must_cover.item_id,
                    evidence_id=assessment.evidence_id,
                    citation=evidence_citation(evidence),
                    publication_id=publication_identity(evidence) or "",
                    source_type=evidence.source_type,
                    support_status=assessment.support_status,
                    support_excerpt=assessment.support_excerpt,
                    applicability_conditions=assessment.applicability_conditions,
                )
            )
            seen_evidence_ids.add(assessment.evidence_id)
        must_cover_contexts.append(
            AnswerMustCoverContext(
                item_id=must_cover.item_id,
                question=must_cover.question,
                claim_type=must_cover.claim_type,
                support_status=must_cover.support_status,
                evidence_policy=must_cover.evidence_policy,
                applicability_conditions=must_cover.applicability_conditions,
                supporting_evidence=supporting_evidence,
            )
        )

    validated_conflicts: list[EvidenceConflict] = []
    for conflict in conflicts:
        try:
            validated_conflicts.append(EvidenceConflict.model_validate(conflict))
        except (ValidationError, ValueError, TypeError):
            continue
    return AnswerGenerationContext(
        question=question_plan.original_question,
        plan_id=question_plan.plan_id,
        must_cover=must_cover_contexts,
        conflicts=validated_conflicts,
        answer_constraints=question_plan.answer_constraints,
    )


def validate_answer_generation(
    answer: str, context: AnswerGenerationContext
) -> AnswerValidationResult:
    """检查终答逐项覆盖、引用、适用条件和冲突约束。"""
    normalized_answer = re.sub(r"\s+", "", answer.casefold())
    covered_ids: list[str] = []
    missing_ids: list[str] = []
    missing_citation_ids: list[str] = []
    missing_applicability: dict[str, list[str]] = {}

    for item in context.must_cover:
        citations = [
            evidence.citation
            for evidence in item.supporting_evidence
            if evidence.citation
        ]
        has_citation = any(citation in answer for citation in citations)
        missing_conditions = [
            condition
            for condition in item.evidence_policy.required_applicability_fields
            if re.sub(r"\s+", "", condition.casefold()) not in normalized_answer
        ]
        evidence_conditions = {
            condition
            for evidence in item.supporting_evidence
            for condition in evidence.applicability_conditions
        }
        evidence_applicability_complete = set(
            item.evidence_policy.required_applicability_fields
        ).issubset(evidence_conditions)
        supported_evidence_count = len(item.supporting_evidence)
        distinct_publications = {
            evidence.publication_id
            for evidence in item.supporting_evidence
            if evidence.publication_id
        }
        policy_satisfied = (
            supported_evidence_count >= item.evidence_policy.minimum_supported_evidence
            and len(distinct_publications)
            >= item.evidence_policy.minimum_distinct_publications
            and evidence_applicability_complete
        )
        supported = (
            item.support_status == "supported"
            and bool(item.supporting_evidence)
            and policy_satisfied
        )
        if supported and has_citation and not missing_conditions:
            covered_ids.append(item.item_id)
            continue
        missing_ids.append(item.item_id)
        if supported and not has_citation:
            missing_citation_ids.append(item.item_id)
        if missing_conditions:
            missing_applicability[item.item_id] = missing_conditions

    unresolved_conflict_ids = [
        conflict.conflict_id
        for conflict in context.conflicts
        if conflict.status == "unresolved"
    ]
    return AnswerValidationResult(
        valid=not missing_ids and not unresolved_conflict_ids,
        covered_must_cover_ids=covered_ids,
        missing_must_cover_ids=missing_ids,
        missing_citation_ids=missing_citation_ids,
        missing_applicability=missing_applicability,
        unresolved_conflict_ids=unresolved_conflict_ids,
    )
