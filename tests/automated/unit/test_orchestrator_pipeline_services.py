"""主控四条职责管线的边界测试。"""

from __future__ import annotations

import asyncio

from app.a2a.medical_risk import MedicalRiskAssessment
from app.a2a.models import AnswerGenerationContext, Evidence
from app.a2a.orchestrator.answer_pipeline import AnswerPipeline
from app.a2a.orchestrator.evidence_pipeline import EvidencePipeline
from app.a2a.orchestrator.planning_service import PlanningService
from app.a2a.orchestrator.retrieval_scheduler import RetrievalScheduler
from app.a2a.question_planner import build_question_plan
from app.a2a.subagents.rag.query_builder import build_rag_payload


def test_planning_service_binds_action_to_exact_plan_task() -> None:
    plan = build_question_plan("高血压患者如何监测？", MedicalRiskAssessment())
    planned = next(task for task in plan.retrieval_tasks if task.status == "pending")

    subtask = PlanningService().build_subtask(
        action={
            "action": f"call_{planned.target_agent}",
            "task_id": planned.task_id,
            "must_cover_id": planned.must_cover_id,
        },
        max_tool_calls=3,
        question_plan=plan,
    )

    assert subtask.task_id == planned.task_id
    assert subtask.must_cover_id == planned.must_cover_id
    assert subtask.question == plan.must_cover[0].question


def test_planning_service_enriches_home_monitoring_retrieval_query() -> None:
    plan = build_question_plan(
        "家庭测血压一般连续几天、每天测几次？", MedicalRiskAssessment()
    )
    planned = next(task for task in plan.retrieval_tasks if task.status == "pending")

    subtask = PlanningService().build_subtask(
        action={
            "action": f"call_{planned.target_agent}",
            "task_id": planned.task_id,
            "must_cover_id": planned.must_cover_id,
        },
        max_tool_calls=3,
        question_plan=plan,
    )
    payload = build_rag_payload(subtask.question, query_terms=subtask.query_terms)

    assert subtask.query_terms == [
        "家庭血压",
        "HBPM",
        "连续测量",
        "天数",
        "早晚",
        "测量次数",
        "读数",
    ]
    assert payload["query"] == (
        "家庭测血压一般连续几天、每天测几次 家庭血压 HBPM 连续测量 "
        "天数 早晚 测量次数 读数"
    )


def test_retrieval_scheduler_selects_ready_primary_wave_without_execution() -> None:
    plan = build_question_plan("如何监测血压，并说明指南来源", MedicalRiskAssessment())
    planned = next(task for task in plan.retrieval_tasks if task.status == "pending")
    scheduler = RetrievalScheduler(
        subagent_client=None,
        planning_service=PlanningService(),
        total_concurrency=4,
        agent_concurrency=2,
        task_timeout_seconds=30,
        max_attempts=2,
    )

    tasks = scheduler.build_ready_subtasks(
        action={
            "action": f"call_{planned.target_agent}",
            "task_id": planned.task_id,
            "must_cover_id": planned.must_cover_id,
        },
        max_tool_calls=3,
        question_plan=plan,
    )

    assert len(tasks) == 2
    assert {task.target_agent for task in tasks} == {"rag"}
    assert {task.must_cover_id for task in tasks} == {
        item.item_id for item in plan.must_cover
    }


def test_evidence_pipeline_returns_typed_claim_assessment() -> None:
    plan = build_question_plan("血压目标是多少？", MedicalRiskAssessment())
    task = next(row for row in plan.retrieval_tasks if row.status == "pending")
    evidence = Evidence(
        source_type="document",
        source_id="guide",
        origin_document_id="guide",
        content="血压目标为130 mmHg。",
    )

    assessments, usage = asyncio.run(
        EvidencePipeline(llm_client=None, timeout=0).verify_task_evidence(
            question_plan=plan,
            task_id=task.task_id,
            must_cover_id=task.must_cover_id,
            evidence_rows=[evidence],
            existing_rows=[],
        )
    )

    assert len(assessments) == 1
    assert assessments[0].support_status == "supported"
    assert assessments[0].must_cover_id == task.must_cover_id
    assert usage == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def test_answer_pipeline_accepts_only_structured_answer_context() -> None:
    context = AnswerGenerationContext(question="如何监测血压？", plan_id="plan-1")

    prompt = AnswerPipeline.user_prompt(
        question=context.question,
        answer_context=context,
    )

    assert "AnswerGenerationContext" in prompt
    assert '"plan_id": "plan-1"' in prompt
    assert "tool_observations" not in prompt
