"""逐 must-cover 语义检索进展测试。"""

from __future__ import annotations

from app.a2a.evidence_fusion import evidence_identity
from app.a2a.medical_risk import MedicalRiskAssessment
from app.a2a.models import ClaimEvidenceAssessment, Evidence
from app.a2a.orchestrator.controller import UnifiedOrchestratorController
from app.a2a.question_planner import build_question_plan
from app.a2a.retrieval_progress import (
    build_retrieval_progress,
    has_semantic_progress,
    no_progress_reason,
)


def _observation(item_id: str, evidence: list[Evidence]) -> dict[str, object]:
    return {
        "agent": "rag",
        "must_cover_id": item_id,
        "evidence": [item.model_dump() for item in evidence],
    }


def test_irrelevant_evidence_does_not_count_as_progress() -> None:
    plan = build_question_plan("高血压患者如何监测？", MedicalRiskAssessment())
    item = plan.must_cover[0]
    task = plan.retrieval_tasks[0]
    previous = build_retrieval_progress(
        question_plan=plan, evidence_by_item={item.item_id: []}
    )[item.item_id]
    evidence = Evidence(
        source_type="document",
        source_id="diet-1",
        origin_document_id="guide-a",
        content="老年人应保持均衡饮食。",
    )
    item.support_status = "irrelevant"
    plan.claim_evidence_assessments.append(
        ClaimEvidenceAssessment(
            task_id=task.task_id,
            must_cover_id=item.item_id,
            evidence_id=evidence_identity(evidence),
            support_status="irrelevant",
        )
    )
    current = build_retrieval_progress(
        question_plan=plan,
        evidence_by_item={item.item_id: [("rag", evidence)]},
    )[item.item_id]

    assert has_semantic_progress(previous, current) is False
    assert no_progress_reason(previous, current) == "irrelevant_evidence"


def test_same_publication_addition_does_not_count_as_progress() -> None:
    plan = build_question_plan("高血压患者如何监测？", MedicalRiskAssessment())
    item = plan.must_cover[0]
    task = plan.retrieval_tasks[0]
    first = Evidence(
        source_type="document",
        source_id="chunk-a",
        origin_document_id="guide-a",
        source_page=8,
        content="高血压患者应规律监测血压。",
    )
    second = Evidence(
        source_type="document",
        source_id="chunk-b",
        origin_document_id="guide-a",
        source_page=9,
        content="高血压患者监测时还应记录测量时间。",
    )
    item.support_status = "supported"
    plan.claim_evidence_assessments.append(
        ClaimEvidenceAssessment(
            task_id=task.task_id,
            must_cover_id=item.item_id,
            evidence_id=evidence_identity(first),
            support_status="supported",
        )
    )
    previous = build_retrieval_progress(
        question_plan=plan,
        evidence_by_item={item.item_id: [("rag", first)]},
    )[item.item_id]
    plan.claim_evidence_assessments.append(
        ClaimEvidenceAssessment(
            task_id=task.task_id,
            must_cover_id=item.item_id,
            evidence_id=evidence_identity(second),
            support_status="supported",
        )
    )
    current = build_retrieval_progress(
        question_plan=plan,
        evidence_by_item={item.item_id: [("rag", first), ("rag", second)]},
    )[item.item_id]

    assert current.supported_evidence_count == 2
    assert current.distinct_publication_count == 1
    assert has_semantic_progress(previous, current) is False
    assert no_progress_reason(previous, current) == "same_source_duplicate"


def test_new_independent_publication_counts_as_progress() -> None:
    plan = build_question_plan("高血压患者如何监测？", MedicalRiskAssessment())
    item = plan.must_cover[0]
    task = plan.retrieval_tasks[0]
    first = Evidence(
        source_type="document",
        source_id="guide-a",
        origin_document_id="guide-a",
        content="高血压患者应规律监测血压。",
    )
    second = first.model_copy(
        update={"source_id": "guide-b", "origin_document_id": "guide-b"}
    )
    item.support_status = "supported"
    for evidence in (first, second):
        plan.claim_evidence_assessments.append(
            ClaimEvidenceAssessment(
                task_id=task.task_id,
                must_cover_id=item.item_id,
                evidence_id=evidence_identity(evidence),
                support_status="supported",
            )
        )
    previous = build_retrieval_progress(
        question_plan=plan,
        evidence_by_item={item.item_id: [("rag", first)]},
    )[item.item_id]
    current = build_retrieval_progress(
        question_plan=plan,
        evidence_by_item={item.item_id: [("rag", first), ("rag", second)]},
    )[item.item_id]

    assert current.distinct_publication_count == 2
    assert has_semantic_progress(previous, current) is True


def test_applicability_completion_counts_as_progress() -> None:
    plan = build_question_plan("老年人的收缩压目标是多少？", MedicalRiskAssessment())
    item = plan.must_cover[0]
    task = plan.retrieval_tasks[0]
    partial = Evidence(
        source_type="document",
        source_id="general-target",
        origin_document_id="guide-a",
        content="收缩压目标为 130 mmHg。",
    )
    matched = Evidence(
        source_type="document",
        source_id="older-target",
        origin_document_id="guide-b",
        content="老年人的收缩压目标为 130 mmHg。",
    )
    item.support_status = "partially_supported"
    plan.claim_evidence_assessments.append(
        ClaimEvidenceAssessment(
            task_id=task.task_id,
            must_cover_id=item.item_id,
            evidence_id=evidence_identity(partial),
            support_status="partially_supported",
        )
    )
    previous = build_retrieval_progress(
        question_plan=plan,
        evidence_by_item={item.item_id: [("rag", partial)]},
    )[item.item_id]
    assert no_progress_reason(previous, previous) == "applicability_mismatch"

    item.support_status = "supported"
    plan.claim_evidence_assessments.append(
        ClaimEvidenceAssessment(
            task_id=task.task_id,
            must_cover_id=item.item_id,
            evidence_id=evidence_identity(matched),
            support_status="supported",
            applicability_conditions=["老年人"],
        )
    )
    current = build_retrieval_progress(
        question_plan=plan,
        evidence_by_item={item.item_id: [("rag", partial), ("rag", matched)]},
    )[item.item_id]

    assert previous.applicability_completeness == 0.0
    assert current.applicability_completeness == 1.0
    assert has_semantic_progress(previous, current) is True


def test_controller_tracks_no_progress_only_for_attempted_item() -> None:
    controller = object.__new__(UnifiedOrchestratorController)
    plan = build_question_plan("如何监测血压，并说明指南来源", MedicalRiskAssessment())
    previous = controller._retrieval_progress_state([], plan)
    counters = {item.item_id: 0 for item in plan.must_cover}
    attempted_id = plan.must_cover[0].item_id

    current, counters, reasons = controller._advance_retrieval_progress(
        tool_observations=[],
        question_plan=plan,
        previous_state=previous,
        no_progress_rounds=counters,
        no_progress_reasons={},
        attempted_must_cover_ids={attempted_id},
    )

    assert current == previous
    assert counters[attempted_id] == 1
    assert counters[plan.must_cover[1].item_id] == 0
    assert reasons == {attempted_id: "support_not_improved"}
    assert controller._all_unresolved_items_stalled(plan, counters) is False

    stalled = {item.item_id: 2 for item in plan.must_cover}
    assert controller._all_unresolved_items_stalled(plan, stalled) is True
