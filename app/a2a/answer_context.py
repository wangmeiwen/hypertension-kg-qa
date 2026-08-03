"""结构化终答证据上下文与后置校验。"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from typing import Any

from pydantic import ValidationError

from app.a2a.evidence_fusion import (
    evidence_identity,
    evidence_identity_index,
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
    StructuredAnswerClaim,
    StructuredNumericClaim,
)
from app.a2a.numeric_claims import (
    extract_numeric_requirements,
    extract_structured_numeric_claims,
    numeric_claim_matches_requirement,
)

_ANSWER_SEGMENT_PATTERN = re.compile(
    r"[^。！？!?\n]+(?:[。！？!?](?:[ \t]*\[[^\]\n]+\])*)?"
)
_MEASUREMENT_CONTEXT_APPLICABILITY = {
    "home": "家庭血压",
    "clinic": "诊室血压",
    "ambulatory": "动态血压",
}


def evidence_citation(evidence: Evidence) -> str:
    """为已校验 Evidence 生成稳定引用标签。"""
    source_id = evidence.source_id.replace("\n", " ").strip()
    page = evidence.source_page if evidence.source_page is not None else evidence.page
    chunk_id = evidence.source_chunk_id or evidence.chunk_id
    locators = []
    if page is not None:
        locators.append(f"p{page}")
    if chunk_id:
        locators.append(f"chunk:{chunk_id}")
    if locators:
        locator = " ".join(locators)
        return f"[{source_id} {locator}]" if source_id else f"[{locator}]"
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
        evidence_by_id = evidence_identity_index(fused)
        supporting_evidence: list[AnswerEvidenceContext] = []
        seen_evidence_ids: set[str] = set()
        for assessment in question_plan.claim_evidence_assessments:
            if (
                assessment.must_cover_id != must_cover.item_id
                or assessment.support_status
                not in must_cover.evidence_policy.required_support_statuses
            ):
                continue
            evidence = evidence_by_id.get(assessment.evidence_id)
            if evidence is None:
                continue
            canonical_evidence_id = evidence_identity(evidence)
            if canonical_evidence_id in seen_evidence_ids:
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
                    evidence_id=canonical_evidence_id,
                    citation=evidence_citation(evidence),
                    publication_id=publication_identity(evidence) or "",
                    source_type=evidence.source_type,
                    support_status=assessment.support_status,
                    support_excerpt=assessment.support_excerpt,
                    applicability_conditions=assessment.applicability_conditions,
                )
            )
            seen_evidence_ids.add(canonical_evidence_id)
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


def extract_structured_answer_claims(
    answer: str, context: AnswerGenerationContext
) -> list[StructuredAnswerClaim]:
    """把终答拆成与 must-cover 和具体 Evidence 局部绑定的结论。"""
    claims: list[StructuredAnswerClaim] = []
    for segment in _ANSWER_SEGMENT_PATTERN.findall(str(answer or "")):
        text = segment.strip()
        if not text:
            continue
        for item in context.must_cover:
            cited_evidence = [
                evidence
                for evidence in item.supporting_evidence
                if evidence.citation and evidence.citation in text
            ]
            if not cited_evidence:
                continue
            conclusion_text = text
            for evidence in cited_evidence:
                conclusion_text = conclusion_text.replace(evidence.citation, "")
            if not conclusion_text.strip(" \t-*#：:。！？!?"):
                continue
            payload = f"{item.item_id}|{text}"
            digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
            claims.append(
                StructuredAnswerClaim(
                    claim_id=f"answer-claim-{digest}",
                    must_cover_id=item.item_id,
                    text=text,
                    citations=list(
                        dict.fromkeys(row.citation for row in cited_evidence)
                    ),
                    evidence_ids=list(
                        dict.fromkeys(row.evidence_id for row in cited_evidence)
                    ),
                    numeric_claims=extract_structured_numeric_claims(text),
                )
            )
    return claims


def _numeric_claims_are_faithful(
    *,
    item: AnswerMustCoverContext,
    answer_claims: list[StructuredAnswerClaim],
    inherit_measurement_context: bool = False,
) -> tuple[bool, list[str]]:
    requirements = extract_numeric_requirements(item.question)
    if not requirements:
        return True, []
    evidence_by_id = {row.evidence_id: row for row in item.supporting_evidence}
    mismatched_claim_ids: list[str] = []
    satisfied_requirements: set[int] = set()
    for index, requirement in enumerate(requirements):

        def scoped_claim(
            claim: StructuredNumericClaim, *, allow_inheritance: bool
        ) -> StructuredNumericClaim:
            if (
                allow_inheritance
                and requirement.measurement_context
                and not claim.measurement_context
            ):
                return claim.model_copy(
                    update={
                        "measurement_context": list(requirement.measurement_context)
                    }
                )
            return claim

        def evidence_allows_inheritance(evidence_id: str) -> bool:
            evidence = evidence_by_id[evidence_id]
            return inherit_measurement_context and all(
                _MEASUREMENT_CONTEXT_APPLICABILITY.get(context_name, "")
                in evidence.applicability_conditions
                for context_name in requirement.measurement_context
            )

        for answer_claim in answer_claims:
            candidates = [
                scoped_claim(claim, allow_inheritance=inherit_measurement_context)
                for claim in answer_claim.numeric_claims
                if numeric_claim_matches_requirement(
                    scoped_claim(claim, allow_inheritance=inherit_measurement_context),
                    requirement,
                )
            ]
            if not candidates:
                continue
            cited_numeric_claims = [
                scoped_claim(
                    claim,
                    allow_inheritance=evidence_allows_inheritance(evidence_id),
                )
                for evidence_id in answer_claim.evidence_ids
                if evidence_id in evidence_by_id
                for claim in extract_structured_numeric_claims(
                    evidence_by_id[evidence_id].support_excerpt
                )
                if numeric_claim_matches_requirement(
                    scoped_claim(
                        claim,
                        allow_inheritance=evidence_allows_inheritance(evidence_id),
                    ),
                    requirement,
                )
            ]
            faithful = all(
                any(
                    candidate.normalized_value == evidence_claim.normalized_value
                    and candidate.comparator == evidence_claim.comparator
                    for evidence_claim in cited_numeric_claims
                )
                for candidate in candidates
            )
            if faithful and cited_numeric_claims:
                satisfied_requirements.add(index)
            else:
                mismatched_claim_ids.append(answer_claim.claim_id)
    return len(satisfied_requirements) == len(requirements), list(
        dict.fromkeys(mismatched_claim_ids)
    )


def validate_answer_generation(
    answer: str, context: AnswerGenerationContext
) -> AnswerValidationResult:
    """检查终答逐项覆盖、引用、数值忠实度、适用条件和冲突。"""
    answer_claims = extract_structured_answer_claims(answer, context)
    covered_ids: list[str] = []
    missing_ids: list[str] = []
    missing_citation_ids: list[str] = []
    missing_applicability: dict[str, list[str]] = {}
    unsupported_claim_ids: list[str] = []
    numeric_mismatch_ids: list[str] = []
    normalized_answer_text = re.sub(r"\s+", "", str(answer or "").casefold())

    for item in context.must_cover:
        item_answer_claims = [
            claim for claim in answer_claims if claim.must_cover_id == item.item_id
        ]
        has_citation = bool(item_answer_claims)
        normalized_claim_text = re.sub(
            r"\s+", "", " ".join(row.text for row in item_answer_claims).casefold()
        )
        applicability_scope_text = normalized_claim_text
        if len(context.must_cover) == 1:
            applicability_scope_text += normalized_answer_text
        missing_conditions = [
            condition
            for condition in item.evidence_policy.required_applicability_fields
            if re.sub(r"\s+", "", condition.casefold()) not in applicability_scope_text
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
        numeric_faithful, mismatched_claims = _numeric_claims_are_faithful(
            item=item,
            answer_claims=item_answer_claims,
            inherit_measurement_context=(
                len(context.must_cover) == 1 and not missing_conditions
            ),
        )
        numeric_mismatch_ids.extend(mismatched_claims)
        if supported and has_citation and not missing_conditions and numeric_faithful:
            covered_ids.append(item.item_id)
            continue
        missing_ids.append(item.item_id)
        if supported and not has_citation:
            missing_citation_ids.append(item.item_id)
        if missing_conditions:
            missing_applicability[item.item_id] = missing_conditions
        if has_citation and not numeric_faithful:
            unsupported_claim_ids.extend(row.claim_id for row in item_answer_claims)

    unresolved_conflict_ids = [
        conflict.conflict_id
        for conflict in context.conflicts
        if conflict.status == "unresolved"
    ]
    return AnswerValidationResult(
        valid=(
            not missing_ids
            and not unresolved_conflict_ids
            and not unsupported_claim_ids
            and not numeric_mismatch_ids
        ),
        covered_must_cover_ids=covered_ids,
        missing_must_cover_ids=missing_ids,
        missing_citation_ids=missing_citation_ids,
        missing_applicability=missing_applicability,
        unresolved_conflict_ids=unresolved_conflict_ids,
        unsupported_claim_ids=list(dict.fromkeys(unsupported_claim_ids)),
        numeric_mismatch_ids=list(dict.fromkeys(numeric_mismatch_ids)),
        answer_claims=answer_claims,
    )
