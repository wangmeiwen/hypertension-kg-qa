"""最终 Evidence Bundle 测试。"""

from __future__ import annotations

from app.a2a.evidence_bundle import build_evidence_bundle
from app.a2a.medical_risk import MedicalRiskAssessment
from app.a2a.models import Evidence
from app.a2a.orchestrator.controller import UnifiedOrchestratorController
from app.a2a.question_planner import build_question_plan


def _document(source_id: str, content: str) -> Evidence:
    return Evidence(
        source_type="document",
        source_id=source_id,
        origin_document_id=source_id,
        page=8,
        content=content,
        retrieval_method="dense",
    )


def test_bundle_is_stable_and_groups_fused_evidence_by_origin() -> None:
    rag = _document("guide-a", "高血压患者应规律监测血压。")
    kg = Evidence(
        source_type="knowledge_graph",
        source_id="relation-1",
        origin_document_id="graph-guide",
        content="高血压关联家庭血压监测。",
        retrieval_method="kg_relation",
    )
    rows = [("rag", rag), ("history", rag.model_copy()), ("kg", kg)]

    first = build_evidence_bundle(
        question="如何监测血压？",
        plan_id="plan-1",
        rows=rows,
        coverage={"complete": True, "coverage_ratio": 1.0},
    )
    second = build_evidence_bundle(
        question="如何监测血压？",
        plan_id="plan-1",
        rows=rows,
        coverage={"complete": True, "coverage_ratio": 1.0},
    )

    assert first == second
    assert len(first.evidence) == 2
    assert len(first.rag_evidence) == 1
    assert len(first.kg_evidence) == 1
    assert len(first.history_evidence) == 1
    assert first.citations == ["[guide-a p8]", "[relation-1]"]
    assert first.distinct_publication_count == 2


def test_bundle_contains_structured_conflicts() -> None:
    bundle = build_evidence_bundle(
        question="治疗能降低多少风险？",
        plan_id="plan-2",
        rows=[
            ("rag", _document("guide-a", "治疗后风险下降 10%。")),
            ("rag", _document("guide-b", "治疗后风险下降 20%。")),
        ],
        coverage={"complete": False},
    )

    assert len(bundle.conflicts) == 1
    assert bundle.conflicts[0].values == ["10%", "20%"]


def test_controller_attaches_bundle_and_telemetry_to_terminal_result() -> None:
    controller = object.__new__(UnifiedOrchestratorController)
    plan = build_question_plan("如何监测血压？", MedicalRiskAssessment())
    plan.must_cover[0].status = "covered"
    evidence = _document("guide-a", "高血压患者应规律监测血压。").model_dump()
    result = controller._attach_evidence_bundle(
        {"telemetry": {}},
        question=plan.original_question,
        question_plan=plan,
        tool_observations=[{"agent": "rag", "evidence": [evidence]}],
    )

    assert result["evidence_bundle"]["plan_id"] == plan.plan_id
    assert result["evidence_bundle"]["coverage"]["complete"] is True
    assert result["telemetry"]["evidence_bundle"]["evidence_count"] == 1
