"""结构化问题计划测试。"""

from __future__ import annotations

from app.a2a.medical_risk import MedicalRiskAssessment
from app.a2a.models import Evidence, SubAgentResult
from app.a2a.orchestrator.controller import UnifiedOrchestratorController
from app.a2a.question_planner import build_question_plan


def test_question_plan_is_stable_and_splits_must_cover_items() -> None:
    question = "比较诊室血压和家庭血压的目标，并说明指南来源"
    risk = MedicalRiskAssessment(risk_level="high", action="evidence_required")

    first = build_question_plan(question, risk)
    second = build_question_plan(question, risk)

    assert first.model_dump() == second.model_dump()
    assert first.medical_risk_level == "high"
    assert len(first.must_cover) == 2
    assert first.must_cover[0].claim_type == "comparison"
    assert first.must_cover[1].claim_type == "source"
    assert first.must_cover[1].required_evidence_types == ["rag"]
    assert "诊室血压" in first.must_cover[0].applicability_conditions
    assert "家庭血压" in first.must_cover[0].applicability_conditions


def test_question_plan_routes_relation_questions_to_kg() -> None:
    plan = build_question_plan(
        "高血压和慢性肾病之间有什么关系路径？", MedicalRiskAssessment()
    )

    assert plan.must_cover[0].claim_type == "relation"
    assert plan.must_cover[0].required_evidence_types == ["kg"]
    assert [task.target_agent for task in plan.retrieval_tasks] == ["kg"]


def test_subtask_uses_plan_fields_and_updates_coverage() -> None:
    controller = object.__new__(UnifiedOrchestratorController)
    plan = build_question_plan("高血压患者如何监测？", MedicalRiskAssessment())
    kg_task = controller._build_subtask(
        action={"action": "call_kg", "question": "偏离计划的问题"},
        default_question="默认问题",
        max_tool_calls=3,
        question_plan=plan,
    )

    assert kg_task.question == plan.must_cover[0].question
    assert kg_task.evidence_type_required == ["kg", "rag"]
    assert kg_task.must_cover == [plan.must_cover[0].question]
    assert kg_task.coverage_status == "pending"

    controller._update_question_plan(
        plan,
        kg_task,
        SubAgentResult(
            target_agent="kg",
            direct_support=True,
            evidence=[Evidence(source_id="kg-1", content="图谱证据")],
        ),
    )
    assert plan.must_cover[0].status == "pending"

    rag_task = controller._build_subtask(
        action={"action": "call_rag"},
        default_question="默认问题",
        max_tool_calls=3,
        question_plan=plan,
    )
    controller._update_question_plan(
        plan,
        rag_task,
        SubAgentResult(
            target_agent="rag",
            direct_support=True,
            evidence=[Evidence(source_id="doc-1", content="文档证据")],
        ),
    )

    assert plan.must_cover[0].status == "covered"
    assert {task.status for task in plan.retrieval_tasks} == {"completed"}


def test_controller_prompt_exposes_question_plan() -> None:
    controller = object.__new__(UnifiedOrchestratorController)
    prompt = controller._build_user_prompt(
        question="如何监测血压？",
        session_id="session-1",
        qa_memory_text="无",
        retrieval_index_text="无",
        question_plan_text='{"plan_id":"plan-1"}',
        tool_observations=[],
        turn_index=1,
        max_turns=3,
    )

    assert "结构化问题计划" in prompt
    assert '"plan_id":"plan-1"' in prompt
