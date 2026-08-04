"""按结论类型和医疗风险评估 Evidence 充分性。"""

from __future__ import annotations

from typing import Any, Sequence
from pydantic import ValidationError

from app.a2a.applicability import canonical_applicability_conditions
from app.a2a.conflict_detection import detect_evidence_conflicts
from app.a2a.evidence_fusion import (
    evidence_identity,
    evidence_identity_index,
    fuse_evidence,
)
from app.a2a.models import (
    AgentType,
    Evidence,
    EvidenceSufficiencyAssessment,
    MustCoverSufficiency,
    QuestionPlan,
)


def _available_evidence_types(
    rows: Sequence[tuple[str, Evidence | dict[str, Any]]],
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


def _supported_rows(
    *,
    rows: list[tuple[str, Evidence | dict[str, Any]]],
    supported_evidence_ids: set[str],
) -> list[tuple[str, Evidence]]:
    """仅保留有显式支持判定且通过 Evidence 契约的记录。"""
    validated: list[tuple[str, Evidence]] = []
    for agent, raw_evidence in rows:
        try:
            evidence = (
                raw_evidence
                if isinstance(raw_evidence, Evidence)
                else Evidence.model_validate(raw_evidence)
            )
        except (ValidationError, ValueError, TypeError):
            continue
        validated.append((agent, evidence))
    identity_index = evidence_identity_index([item for _, item in validated])
    selected_canonical_ids = {
        evidence_identity(identity_index[identity])
        for identity in supported_evidence_ids
        if identity in identity_index
    }
    return [
        (agent, evidence)
        for agent, evidence in validated
        if evidence_identity(evidence) in selected_canonical_ids
    ]


def assess_evidence_sufficiency(
    *,
    question_plan: QuestionPlan,
    evidence_by_item: dict[str, list[tuple[str, Evidence | dict[str, Any]]]],
) -> EvidenceSufficiencyAssessment:
    """按每个 must-cover 的 MinimumEvidencePolicy 评估充分性。"""
    item_results: list[MustCoverSufficiency] = []
    for item in question_plan.must_cover:
        rows = evidence_by_item.get(item.item_id, [])
        fusion = fuse_evidence(rows)
        policy = item.evidence_policy
        accepted_assessments = [
            assessment
            for assessment in question_plan.claim_evidence_assessments
            if assessment.must_cover_id == item.item_id
            and assessment.support_status in policy.required_support_statuses
        ]
        supported_evidence_ids = {
            assessment.evidence_id for assessment in accepted_assessments
        }
        supported_rows = _supported_rows(
            rows=rows,
            supported_evidence_ids=supported_evidence_ids,
        )
        supported_fusion = fuse_evidence(supported_rows)
        available_types = _available_evidence_types(
            supported_rows, supported_fusion.evidence
        )
        conflicts = detect_evidence_conflicts(supported_fusion.evidence)
        observed_applicability = set(
            canonical_applicability_conditions(
                condition
                for assessment in accepted_assessments
                for condition in assessment.applicability_conditions
            )
        )
        required_applicability = set(
            canonical_applicability_conditions(policy.required_applicability_fields)
        )
        applicability_complete = required_applicability.issubset(observed_applicability)
        missing_reasons: list[str] = []
        if item.support_status not in policy.required_support_statuses:
            missing_reasons.append("claim_not_supported")
        if len(supported_fusion.evidence) < policy.minimum_supported_evidence:
            missing_reasons.append("supported_evidence")
        if (
            supported_fusion.distinct_publication_count
            < policy.minimum_distinct_publications
        ):
            missing_reasons.append("distinct_publications")
        for required_retriever in policy.required_retrievers:
            if required_retriever not in available_types:
                missing_reasons.append(f"required_retriever:{required_retriever}")
        if not applicability_complete:
            missing_reasons.append("applicability_conditions")
        if conflicts and policy.conflict_policy == "block_unresolved":
            missing_reasons.append("unresolved_conflict")
        item_results.append(
            MustCoverSufficiency(
                item_id=item.item_id,
                claim_type=item.claim_type,
                support_status=item.support_status,
                sufficient=not missing_reasons,
                evidence_count=len(fusion.evidence),
                supported_evidence_count=len(supported_fusion.evidence),
                distinct_publication_count=(
                    supported_fusion.distinct_publication_count
                ),
                required_publication_count=policy.minimum_distinct_publications,
                available_evidence_types=available_types,
                applicability_complete=applicability_complete,
                unresolved_conflict_count=len(conflicts),
                policy=policy,
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
