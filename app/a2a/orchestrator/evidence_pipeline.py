"""检索结果到 Claim-Evidence Assessment 的证据管线。"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.a2a.claim_support import verify_claim_evidence
from app.a2a.evidence_fusion import evidence_identity
from app.a2a.models import ClaimEvidenceAssessment, Evidence, QuestionPlan
from app.a2a.telemetry import empty_token_usage


class EvidencePipeline:
    """只验证当前任务返回的 Evidence，不修改调度或终答状态。"""

    def __init__(self, *, llm_client: Any | None, timeout: float) -> None:
        self._llm_client = llm_client
        self._timeout = timeout

    async def verify_task_evidence(
        self,
        *,
        question_plan: QuestionPlan,
        task_id: str,
        must_cover_id: str,
        evidence_rows: Any,
        existing_rows: Any,
    ) -> tuple[list[ClaimEvidenceAssessment], dict[str, int]]:
        retrieval_task = next(
            (
                item
                for item in question_plan.retrieval_tasks
                if item.task_id == task_id and item.must_cover_id == must_cover_id
            ),
            None,
        )
        must_cover_item = next(
            (
                item
                for item in question_plan.must_cover
                if item.item_id == must_cover_id
            ),
            None,
        )
        if retrieval_task is None or must_cover_item is None:
            return [], empty_token_usage()
        evidence: list[Evidence] = []
        for row in evidence_rows if isinstance(evidence_rows, list) else []:
            try:
                evidence.append(
                    row if isinstance(row, Evidence) else Evidence.model_validate(row)
                )
            except (ValidationError, ValueError, TypeError):
                continue
        allowed_ids = {evidence_identity(item) for item in evidence}
        existing: list[ClaimEvidenceAssessment] = []
        reusable_versions = {
            "claim_support.deterministic.v1",
            "claim_support.semantic.v1",
        }
        for row in existing_rows if isinstance(existing_rows, list) else []:
            try:
                assessment = ClaimEvidenceAssessment.model_validate(row)
            except (ValidationError, ValueError, TypeError):
                continue
            evidence_item = next(
                (
                    item
                    for item in evidence
                    if evidence_identity(item) == assessment.evidence_id
                ),
                None,
            )
            grounded_excerpt = assessment.support_status in {
                "irrelevant",
                "uncertain",
            } or bool(
                evidence_item
                and assessment.support_excerpt
                and assessment.support_excerpt in evidence_item.content
            )
            if (
                assessment.task_id == task_id
                and assessment.must_cover_id == must_cover_id
                and assessment.evidence_id in allowed_ids
                and assessment.verifier_version in reusable_versions
                and grounded_excerpt
            ):
                existing.append(assessment)
        assessed_ids = {item.evidence_id for item in existing}
        missing_evidence = [
            item for item in evidence if evidence_identity(item) not in assessed_ids
        ]
        if not missing_evidence:
            return existing, empty_token_usage()
        verified, usage = await verify_claim_evidence(
            task=retrieval_task,
            must_cover=must_cover_item,
            evidence=missing_evidence,
            llm_client=self._llm_client,
            timeout=self._timeout,
        )
        return [*existing, *verified], usage
