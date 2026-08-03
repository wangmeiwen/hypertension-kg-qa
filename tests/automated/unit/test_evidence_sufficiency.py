"""分级 Evidence 充分性测试。"""

from __future__ import annotations

from app.a2a.evidence_fusion import evidence_identity
from app.a2a.evidence_sufficiency import assess_evidence_sufficiency
from app.a2a.medical_risk import MedicalRiskAssessment
from app.a2a.models import ClaimEvidenceAssessment, Evidence
from app.a2a.orchestrator.controller import UnifiedOrchestratorController
from app.a2a.question_planner import build_question_plan


def _document(source_id: str, content: str) -> Evidence:
    return Evidence(
        source_type="document",
        source_id=f"evidence-{source_id}",
        origin_document_id=source_id,
        content=content,
    )


def _knowledge_graph(source_id: str, content: str) -> Evidence:
    return Evidence(
        source_type="knowledge_graph",
        source_id=source_id,
        origin_document_id="graph-guide",
        content=content,
    )


def _mark_supported(
    plan,
    item,
    evidence: list[Evidence],
    *,
    applicability: list[str] | None = None,
) -> None:
    item.support_status = "supported"
    item.status = "covered"
    task_id = next(
        task.task_id
        for task in plan.retrieval_tasks
        if task.must_cover_id == item.item_id
    )
    plan.claim_evidence_assessments.extend(
        ClaimEvidenceAssessment(
            task_id=task_id,
            must_cover_id=item.item_id,
            evidence_id=evidence_identity(row),
            support_status="supported",
            applicability_conditions=list(applicability or []),
        )
        for row in evidence
    )


def test_fact_claim_accepts_one_direct_source_after_coverage() -> None:
    plan = build_question_plan("高血压如何监测？", MedicalRiskAssessment())
    item = plan.must_cover[0]
    evidence = _document("guide", "规律监测")
    _mark_supported(plan, item, [evidence])

    assessment = assess_evidence_sufficiency(
        question_plan=plan,
        evidence_by_item={item.item_id: [("rag", evidence)]},
    )

    assert assessment.sufficient is True
    assert assessment.sufficiency_ratio == 1.0


def test_evidence_presence_cannot_replace_claim_support() -> None:
    plan = build_question_plan(
        "80 岁以上老年人的收缩压目标是多少？", MedicalRiskAssessment()
    )
    item = plan.must_cover[0]

    assessment = assess_evidence_sufficiency(
        question_plan=plan,
        evidence_by_item={
            item.item_id: [("rag", _document("guide", "老年人应定期监测血压"))]
        },
    )

    assert assessment.sufficient is False
    assert assessment.items[0].support_status == "uncertain"
    assert "claim_not_supported" in assessment.items[0].missing_reasons


def test_numeric_claim_requires_two_distinct_publications() -> None:
    plan = build_question_plan("血压目标是多少？", MedicalRiskAssessment())
    item = plan.must_cover[0]
    guide_a = _document("guide-a", "目标为130 mmHg")
    guide_b = _document("guide-b", "目标为130 mmHg")
    _mark_supported(plan, item, [guide_a, guide_b])
    one_source = {item.item_id: [("rag", guide_a)]}
    two_sources = {
        item.item_id: [
            ("rag", guide_a),
            ("rag", guide_b),
        ]
    }

    insufficient = assess_evidence_sufficiency(
        question_plan=plan,
        evidence_by_item=one_source,
    )
    sufficient = assess_evidence_sufficiency(
        question_plan=plan,
        evidence_by_item=two_sources,
    )

    assert insufficient.sufficient is False
    assert insufficient.items[0].missing_reasons == [
        "supported_evidence",
        "distinct_publications",
    ]
    assert sufficient.sufficient is True


def test_relation_claim_requires_knowledge_graph_evidence() -> None:
    plan = build_question_plan("高血压和肾病有什么关系？", MedicalRiskAssessment())
    item = plan.must_cover[0]
    document = _document("guide", "两者相关")
    graph_evidence = _knowledge_graph("relation-1", "两者相关")
    _mark_supported(plan, item, [document, graph_evidence])

    document_only = assess_evidence_sufficiency(
        question_plan=plan,
        evidence_by_item={item.item_id: [("rag", document)]},
    )
    graph = assess_evidence_sufficiency(
        question_plan=plan,
        evidence_by_item={item.item_id: [("kg", graph_evidence)]},
    )

    assert document_only.items[0].missing_reasons == ["required_retriever:kg"]
    assert graph.sufficient is True


def test_high_risk_gate_raises_source_requirement_for_fact_claim() -> None:
    plan = build_question_plan(
        "孕妇如何监测血压？",
        MedicalRiskAssessment(risk_level="high", action="evidence_required"),
    )
    item = plan.must_cover[0]
    evidence = _document("guide", "规律监测")
    _mark_supported(plan, item, [evidence], applicability=["孕妇"])

    assessment = assess_evidence_sufficiency(
        question_plan=plan,
        evidence_by_item={item.item_id: [("rag", evidence)]},
    )

    assert assessment.sufficient is False
    assert assessment.items[0].required_publication_count == 2
    assert assessment.items[0].missing_reasons == [
        "supported_evidence",
        "distinct_publications",
    ]


def test_high_risk_policy_requires_applicability_match() -> None:
    plan = build_question_plan(
        "孕妇如何监测血压？",
        MedicalRiskAssessment(risk_level="high", action="evidence_required"),
    )
    item = plan.must_cover[0]
    evidence = [
        _document("guide-a", "孕妇应规律监测"),
        _document("guide-b", "孕妇应规律监测"),
    ]
    _mark_supported(plan, item, evidence)

    assessment = assess_evidence_sufficiency(
        question_plan=plan,
        evidence_by_item={item.item_id: [("rag", row) for row in evidence]},
    )

    assert assessment.sufficient is False
    assert assessment.items[0].applicability_complete is False
    assert assessment.items[0].missing_reasons == ["applicability_conditions"]


def test_unknown_kg_origin_cannot_satisfy_publication_requirement() -> None:
    plan = build_question_plan("高血压和肾病有什么关系？", MedicalRiskAssessment())
    item = plan.must_cover[0]
    unknown = Evidence(
        source_type="knowledge_graph",
        source_id="relation-1",
        content="高血压关联慢性肾病",
    )
    _mark_supported(plan, item, [unknown])

    assessment = assess_evidence_sufficiency(
        question_plan=plan,
        evidence_by_item={item.item_id: [("kg", unknown)]},
    )

    assert assessment.sufficient is False
    assert assessment.items[0].distinct_publication_count == 0
    assert assessment.items[0].missing_reasons == ["distinct_publications"]


def test_controller_counts_evidence_per_must_cover_without_cross_contamination() -> (
    None
):
    controller = object.__new__(UnifiedOrchestratorController)
    plan = build_question_plan(
        "血压目标是多少，并说明指南来源", MedicalRiskAssessment()
    )
    numeric_item, source_item = plan.must_cover
    numeric_evidence = [
        _document("guide-a", "血压目标为130 mmHg"),
        _document("guide-b", "血压目标为130 mmHg"),
    ]
    source_evidence = [_document("guide-c", "来源为指南原文")]
    _mark_supported(plan, numeric_item, numeric_evidence)
    _mark_supported(plan, source_item, source_evidence)
    observations = [
        {
            "agent": "rag",
            "must_cover_id": numeric_item.item_id,
            "evidence": [item.model_dump() for item in numeric_evidence],
        },
        {
            "agent": "rag",
            "must_cover_id": source_item.item_id,
            "evidence": [item.model_dump() for item in source_evidence],
        },
    ]

    readiness = controller._assess_answer_readiness(
        observations, MedicalRiskAssessment(), plan
    )

    assert readiness["ready"] is True
    assert readiness["evidence_sufficiency"]["sufficient"] is True
    assert [
        item["evidence_count"] for item in readiness["evidence_sufficiency"]["items"]
    ] == [2, 1]
