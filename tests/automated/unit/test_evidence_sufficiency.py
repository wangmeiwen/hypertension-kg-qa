"""分级 Evidence 充分性测试。"""

from __future__ import annotations

from app.a2a.evidence_sufficiency import assess_evidence_sufficiency
from app.a2a.medical_risk import MedicalRiskAssessment
from app.a2a.models import Evidence
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


def test_fact_claim_accepts_one_direct_source_after_coverage() -> None:
    plan = build_question_plan("高血压如何监测？", MedicalRiskAssessment())
    item = plan.must_cover[0]
    item.status = "covered"

    assessment = assess_evidence_sufficiency(
        question_plan=plan,
        evidence_by_item={item.item_id: [("rag", _document("guide", "规律监测"))]},
        minimum_distinct_publications=1,
    )

    assert assessment.sufficient is True
    assert assessment.sufficiency_ratio == 1.0


def test_numeric_claim_requires_two_distinct_publications() -> None:
    plan = build_question_plan("血压目标是多少？", MedicalRiskAssessment())
    item = plan.must_cover[0]
    item.status = "covered"
    one_source = {item.item_id: [("rag", _document("guide-a", "目标为130 mmHg"))]}
    two_sources = {
        item.item_id: [
            ("rag", _document("guide-a", "目标为130 mmHg")),
            ("rag", _document("guide-b", "目标为130 mmHg")),
        ]
    }

    insufficient = assess_evidence_sufficiency(
        question_plan=plan,
        evidence_by_item=one_source,
        minimum_distinct_publications=1,
    )
    sufficient = assess_evidence_sufficiency(
        question_plan=plan,
        evidence_by_item=two_sources,
        minimum_distinct_publications=1,
    )

    assert insufficient.sufficient is False
    assert insufficient.items[0].missing_reasons == ["distinct_publications"]
    assert sufficient.sufficient is True


def test_relation_claim_requires_knowledge_graph_evidence() -> None:
    plan = build_question_plan("高血压和肾病有什么关系？", MedicalRiskAssessment())
    item = plan.must_cover[0]
    item.status = "covered"

    document_only = assess_evidence_sufficiency(
        question_plan=plan,
        evidence_by_item={item.item_id: [("rag", _document("guide", "两者相关"))]},
        minimum_distinct_publications=1,
    )
    graph = assess_evidence_sufficiency(
        question_plan=plan,
        evidence_by_item={
            item.item_id: [("kg", _knowledge_graph("relation-1", "两者相关"))]
        },
        minimum_distinct_publications=1,
    )

    assert document_only.items[0].missing_reasons == ["knowledge_graph_evidence"]
    assert graph.sufficient is True


def test_high_risk_gate_raises_source_requirement_for_fact_claim() -> None:
    plan = build_question_plan(
        "孕妇如何监测血压？",
        MedicalRiskAssessment(risk_level="high", action="evidence_required"),
    )
    item = plan.must_cover[0]
    item.status = "covered"

    assessment = assess_evidence_sufficiency(
        question_plan=plan,
        evidence_by_item={item.item_id: [("rag", _document("guide", "规律监测"))]},
        minimum_distinct_publications=2,
    )

    assert assessment.sufficient is False
    assert assessment.items[0].required_publication_count == 2
    assert assessment.items[0].missing_reasons == ["distinct_publications"]


def test_unknown_kg_origin_cannot_satisfy_publication_requirement() -> None:
    plan = build_question_plan("高血压和肾病有什么关系？", MedicalRiskAssessment())
    item = plan.must_cover[0]
    item.status = "covered"
    unknown = Evidence(
        source_type="knowledge_graph",
        source_id="relation-1",
        content="高血压关联慢性肾病",
    )

    assessment = assess_evidence_sufficiency(
        question_plan=plan,
        evidence_by_item={item.item_id: [("kg", unknown)]},
        minimum_distinct_publications=1,
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
    for item in plan.must_cover:
        item.status = "covered"
    numeric_item, source_item = plan.must_cover
    observations = [
        {
            "agent": "rag",
            "must_cover_id": numeric_item.item_id,
            "evidence": [
                _document("guide-a", "血压目标为130 mmHg").model_dump(),
                _document("guide-b", "血压目标为130 mmHg").model_dump(),
            ],
        },
        {
            "agent": "rag",
            "must_cover_id": source_item.item_id,
            "evidence": [_document("guide-c", "来源为指南原文").model_dump()],
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
