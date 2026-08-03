"""结构化问题计划测试。"""

from __future__ import annotations

import pytest

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
    planned_kg_task = plan.retrieval_tasks[0]
    kg_task = controller._build_subtask(
        action={
            "action": "call_kg",
            "task_id": planned_kg_task.task_id,
            "must_cover_id": planned_kg_task.must_cover_id,
            "question": "偏离计划的问题",
        },
        max_tool_calls=3,
        question_plan=plan,
    )

    assert kg_task.task_id == planned_kg_task.task_id
    assert kg_task.must_cover_id == planned_kg_task.must_cover_id
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

    planned_rag_task = plan.retrieval_tasks[1]
    rag_task = controller._build_subtask(
        action={
            "action": "call_rag",
            "task_id": planned_rag_task.task_id,
            "must_cover_id": planned_rag_task.must_cover_id,
        },
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


def test_plan_action_requires_exact_task_binding() -> None:
    controller = object.__new__(UnifiedOrchestratorController)
    plan = build_question_plan("高血压患者如何监测？", MedicalRiskAssessment())
    kg_task, rag_task = plan.retrieval_tasks

    controller._validate_plan_action(
        {
            "action": "call_kg",
            "task_id": kg_task.task_id,
            "must_cover_id": kg_task.must_cover_id,
        },
        plan,
    )

    with pytest.raises(ValueError, match="action_task_agent_mismatch"):
        controller._validate_plan_action(
            {
                "action": "call_rag",
                "task_id": kg_task.task_id,
                "must_cover_id": kg_task.must_cover_id,
            },
            plan,
        )
    multi_plan = build_question_plan(
        "如何监测血压，并说明指南来源", MedicalRiskAssessment()
    )
    first_task = multi_plan.retrieval_tasks[0]
    second_item = multi_plan.must_cover[1]
    with pytest.raises(ValueError, match="action_task_must_cover_mismatch"):
        controller._validate_plan_action(
            {
                "action": f"call_{first_task.target_agent}",
                "task_id": first_task.task_id,
                "must_cover_id": second_item.item_id,
            },
            multi_plan,
        )

    rag_task.status = "completed"
    with pytest.raises(ValueError, match="action_task_not_actionable"):
        controller._validate_plan_action(
            {
                "action": "call_rag",
                "task_id": rag_task.task_id,
                "must_cover_id": rag_task.must_cover_id,
            },
            plan,
        )


def test_question_plan_updates_only_the_selected_task() -> None:
    controller = object.__new__(UnifiedOrchestratorController)
    plan = build_question_plan("如何监测血压，并说明指南来源", MedicalRiskAssessment())
    selected = plan.retrieval_tasks[1]
    task = controller._build_subtask(
        action={
            "action": f"call_{selected.target_agent}",
            "task_id": selected.task_id,
            "must_cover_id": selected.must_cover_id,
        },
        max_tool_calls=3,
        question_plan=plan,
    )

    controller._update_question_plan(
        plan,
        task,
        SubAgentResult(
            task_id=task.task_id,
            must_cover_id=task.must_cover_id,
            target_agent=task.target_agent,
            direct_support=True,
            evidence=[Evidence(source_id="evidence-1", content="直接证据")],
        ),
    )

    statuses = {item.task_id: item.status for item in plan.retrieval_tasks}
    assert statuses[selected.task_id] == "completed"
    assert all(
        status == "pending"
        for task_id, status in statuses.items()
        if task_id != selected.task_id
    )


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
