"""主控 must-cover 覆盖与语义停止测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from app.a2a.evidence_fusion import evidence_identity
from app.a2a.medical_risk import MedicalRiskAssessment
from app.a2a.models import ClaimEvidenceAssessment, Evidence, SubAgentResult, SubTask
from app.a2a.orchestrator.controller import UnifiedOrchestratorController
from app.a2a.question_planner import build_question_plan


def _evidence() -> dict[str, Any]:
    return Evidence(
        source_type="document",
        source_id="guide",
        origin_document_id="guide",
        content="高血压患者应规律监测血压。",
    ).model_dump()


def test_answer_readiness_requires_all_must_cover_items() -> None:
    controller = object.__new__(UnifiedOrchestratorController)
    plan = build_question_plan("如何监测血压？", MedicalRiskAssessment())
    observations = [
        {
            "agent": "rag",
            "must_cover_id": plan.must_cover[0].item_id,
            "evidence": [_evidence()],
        }
    ]

    pending = controller._assess_answer_readiness(
        observations, MedicalRiskAssessment(), plan
    )
    evidence = Evidence.model_validate(observations[0]["evidence"][0])
    plan.claim_evidence_assessments.append(
        ClaimEvidenceAssessment(
            task_id=plan.retrieval_tasks[0].task_id,
            must_cover_id=plan.must_cover[0].item_id,
            evidence_id=evidence_identity(evidence),
            support_status="supported",
        )
    )
    plan.must_cover[0].support_status = "supported"
    plan.must_cover[0].status = "covered"
    covered = controller._assess_answer_readiness(
        observations, MedicalRiskAssessment(), plan
    )

    assert pending["ready"] is False
    assert pending["plan_coverage"]["coverage_ratio"] == 0.0
    assert pending["missing_points"] == [f"must_cover:{plan.must_cover[0].item_id}"]
    assert covered["ready"] is True
    assert covered["plan_coverage"]["coverage_ratio"] == 1.0


def test_history_evidence_covers_only_unambiguous_plan_item() -> None:
    single = build_question_plan("如何监测血压？", MedicalRiskAssessment())
    single_evidence = _evidence()
    single_task = single.retrieval_tasks[0]
    UnifiedOrchestratorController._update_question_plan_from_history(
        single,
        {
            "action": "call_history_read",
            "history_id": "history-1",
            "task_id": single_task.task_id,
            "must_cover_id": single.must_cover[0].item_id,
        },
        {
            "ok": True,
            "result": {
                "matched": True,
                "evidence": [single_evidence],
                "claim_assessments": [
                    {
                        "task_id": single_task.task_id,
                        "must_cover_id": single.must_cover[0].item_id,
                        "evidence_id": evidence_identity(
                            Evidence.model_validate(single_evidence)
                        ),
                        "support_status": "supported",
                    }
                ],
            },
        },
    )
    assert single.must_cover[0].status == "covered"

    multiple = build_question_plan(
        "比较诊室血压，并说明指南来源", MedicalRiskAssessment()
    )
    UnifiedOrchestratorController._update_question_plan_from_history(
        multiple,
        {
            "action": "call_history_read",
            "history_id": "history-2",
            "task_id": multiple.retrieval_tasks[0].task_id,
            "must_cover_id": "mc-not-found",
        },
        {"result": {"matched": True, "evidence": [_evidence()]}},
    )
    assert {item.status for item in multiple.must_cover} == {"pending"}


def test_insufficient_must_cover_reopens_only_its_tasks_for_supplement() -> None:
    plan = build_question_plan("如何监测血压，并说明指南来源", MedicalRiskAssessment())
    for item in plan.must_cover:
        item.support_status = "supported"
        item.status = "covered"
    for task in plan.retrieval_tasks:
        task.status = "completed" if task.execution_stage == "primary" else "deferred"
    target_item = plan.must_cover[1]

    UnifiedOrchestratorController._mark_supplement_tasks(
        plan,
        {
            "evidence_sufficiency": {
                "items": [
                    {"item_id": plan.must_cover[0].item_id, "sufficient": True},
                    {"item_id": target_item.item_id, "sufficient": False},
                ]
            }
        },
    )

    assert plan.must_cover[0].status == "covered"
    assert target_item.status == "pending"
    assert {
        task.status
        for task in plan.retrieval_tasks
        if task.must_cover_id == target_item.item_id
    } == {"completed", "needs_supplement"}
    assert {
        task.status
        for task in plan.retrieval_tasks
        if task.must_cover_id != target_item.item_id
    } == {"completed", "deferred"}


def test_semantic_only_support_activates_optional_corroboration() -> None:
    controller = object.__new__(UnifiedOrchestratorController)
    plan = build_question_plan("高血压患者如何监测？", MedicalRiskAssessment())
    rag_task = next(task for task in plan.retrieval_tasks if task.target_agent == "rag")
    subtask = controller._build_subtask(
        action={
            "action": "call_rag",
            "task_id": rag_task.task_id,
            "must_cover_id": rag_task.must_cover_id,
        },
        max_tool_calls=3,
        question_plan=plan,
    )
    evidence = Evidence(
        source_type="document",
        source_id="guide",
        origin_document_id="guide",
        content="高血压患者应规律监测血压。",
    )

    controller._update_question_plan(
        plan,
        subtask,
        SubAgentResult(
            task_id=subtask.task_id,
            must_cover_id=subtask.must_cover_id,
            target_agent="rag",
            evidence=[evidence],
            claim_assessments=[
                ClaimEvidenceAssessment(
                    task_id=subtask.task_id,
                    must_cover_id=subtask.must_cover_id,
                    evidence_id=evidence_identity(evidence),
                    support_status="supported",
                    support_excerpt=evidence.content,
                    verifier_version="claim_support.semantic.v1",
                )
            ],
        ),
    )

    assert plan.must_cover[0].status == "pending"
    assert {
        task.status for task in plan.retrieval_tasks if task.target_agent == "kg"
    } == {"needs_supplement"}

    kg_task = next(task for task in plan.retrieval_tasks if task.target_agent == "kg")
    kg_subtask = controller._build_subtask(
        action={
            "action": "call_kg",
            "task_id": kg_task.task_id,
            "must_cover_id": kg_task.must_cover_id,
        },
        max_tool_calls=3,
        question_plan=plan,
    )
    controller._update_question_plan(
        plan,
        kg_subtask,
        SubAgentResult(
            task_id=kg_subtask.task_id,
            must_cover_id=kg_subtask.must_cover_id,
            target_agent="kg",
            evidence=[],
            claim_assessments=[],
            no_signal=True,
        ),
    )

    assert plan.must_cover[0].status == "covered"
    assert kg_task.status == "completed"


class _NoEvidenceSubagentClient:
    async def send_task(self, task: SubTask) -> SubAgentResult:
        return SubAgentResult(
            task_id=task.task_id,
            must_cover_id=task.must_cover_id,
            subtask_id=task.subtask_id,
            target_agent=task.target_agent,
            question=task.question,
            direct_support=False,
            no_signal=True,
            reason="no evidence",
        )


def test_run_stops_after_two_retrieval_rounds_without_progress() -> None:
    controller = object.__new__(UnifiedOrchestratorController)
    controller._subagent_client = _NoEvidenceSubagentClient()
    controller._mcp_gateway = None
    controller._conf = SimpleNamespace(
        A2A_SUBAGENT_MAX_TOOL_CALLS_DEFAULT=3,
        A2A_SUBAGENT_MAX_TOOL_CALLS_MAX=3,
    )
    plan = build_question_plan("高血压患者如何监测？", MedicalRiskAssessment())
    planned_task = plan.retrieval_tasks[0]
    decision_count = 0

    async def decide_action(
        **kwargs: Any,
    ) -> tuple[dict[str, Any], dict[str, int]]:
        nonlocal decision_count
        _ = kwargs
        decision_count += 1
        if decision_count == 2:
            return {"action": "final_answer", "answer": "证据不足"}, {"total_tokens": 1}
        return {
            "action": f"call_{planned_task.target_agent}",
            "task_id": planned_task.task_id,
            "must_cover_id": planned_task.must_cover_id,
            "reason": "继续检索",
        }, {"total_tokens": 1}

    controller._decide_action = decide_action

    result = asyncio.run(
        controller.run(
            input_text="高血压患者如何监测？",
            session_id="session-1",
            max_turns=5,
        )
    )

    assert decision_count == 3
    assert result["output_type"] == "orchestrator_abstained_insufficient_evidence"
    assert result["telemetry"]["stop_reason"] == "no_retrieval_progress"
    assert result["telemetry"]["no_progress_rounds"] == 2
    assert result["telemetry"]["no_progress_rounds_by_must_cover"] == {
        plan.must_cover[0].item_id: 2
    }
    assert result["telemetry"]["no_progress_reasons"] == {
        plan.must_cover[0].item_id: "support_not_improved"
    }
    assert (
        result["telemetry"]["must_cover_progress"][plan.must_cover[0].item_id][
            "supported_evidence_count"
        ]
        == 0
    )
    assert result["evidence_summary"]["plan_coverage"]["complete"] is False
