"""主控 must-cover 覆盖与语义停止测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from app.a2a.medical_risk import MedicalRiskAssessment
from app.a2a.models import Evidence, SubAgentResult, SubTask
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
    observations = [{"agent": "rag", "evidence": [_evidence()]}]

    pending = controller._assess_answer_readiness(
        observations, MedicalRiskAssessment(), plan
    )
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
    UnifiedOrchestratorController._update_question_plan_from_history(
        single,
        {"action": "call_history_read", "history_id": "history-1"},
        {"result": {"matched": True, "evidence": [_evidence()]}},
    )
    assert single.must_cover[0].status == "covered"

    multiple = build_question_plan(
        "比较诊室血压，并说明指南来源", MedicalRiskAssessment()
    )
    UnifiedOrchestratorController._update_question_plan_from_history(
        multiple,
        {"action": "call_history_read", "history_id": "history-2"},
        {"result": {"matched": True, "evidence": [_evidence()]}},
    )
    assert {item.status for item in multiple.must_cover} == {"pending"}


class _NoEvidenceSubagentClient:
    async def send_task(self, task: SubTask) -> SubAgentResult:
        return SubAgentResult(
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
    decision_count = 0

    async def decide_action(
        **kwargs: Any,
    ) -> tuple[dict[str, Any], dict[str, int]]:
        nonlocal decision_count
        _ = kwargs
        decision_count += 1
        return {"action": "call_kg", "reason": "继续检索"}, {"total_tokens": 1}

    controller._decide_action = decide_action

    result = asyncio.run(
        controller.run(
            input_text="高血压患者如何监测？",
            session_id="session-1",
            max_turns=5,
        )
    )

    assert decision_count == 2
    assert result["output_type"] == "orchestrator_abstained_insufficient_evidence"
    assert result["telemetry"]["stop_reason"] == "no_retrieval_progress"
    assert result["telemetry"]["no_progress_rounds"] == 2
    assert result["evidence_summary"]["plan_coverage"]["complete"] is False
