"""医疗风险 Gate 测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from app.a2a.medical_risk import assess_medical_risk
from app.a2a.models import Evidence
from app.a2a.orchestrator.controller import UnifiedOrchestratorController
from app.a2a.question_planner import build_question_plan


@pytest.mark.parametrize(
    "question",
    [
        "我现在血压 190/100，而且胸痛",
        "我目前突然呼吸困难并且意识不清",
        "医生说我现在发生高血压急症",
        "我刚测得血压 170/125",
        "我现在收缩压达到 185",
        "我目前舒张压达到 122",
    ],
)
def test_emergency_signals_trigger_urgent_redirect(question: str) -> None:
    assessment = assess_medical_risk(question)

    assert assessment.risk_level == "emergency"
    assert assessment.action == "urgent_redirect"
    assert assessment.context_type == "current_self_report"
    assert assessment.rule_version == "medical_risk.v2"
    assert assessment.trigger_excerpts


@pytest.mark.parametrize(
    "question",
    [
        "降压药可以停药吗",
        "孕妇高血压如何用药",
        "儿童高血压怎么治疗",
        "eGFR < 30 时如何选择降压药",
        "透析患者如何控制血压",
    ],
)
def test_high_risk_questions_require_two_sources(question: str) -> None:
    assessment = assess_medical_risk(question)

    assert assessment.risk_level == "high"
    assert assessment.action == "evidence_required"
    assert assessment.minimum_distinct_publications == 2


def test_negated_emergency_signal_does_not_trigger_redirect() -> None:
    assessments = [
        assess_medical_risk("我不觉得有胸痛，只想了解家庭血压监测"),
        assess_medical_risk("我之前胸痛，现在没有"),
        assess_medical_risk("医生排除了高血压急症"),
    ]

    assert {item.risk_level for item in assessments} == {"low"}
    assert {item.action for item in assessments} == {"standard"}
    assert {item.context_type for item in assessments} == {"negated_symptom"}


def test_knowledge_query_does_not_trigger_urgent_redirect() -> None:
    assessment = assess_medical_risk("高血压急症的定义是什么？")

    assert assessment.risk_level == "low"
    assert assessment.action == "standard"
    assert assessment.context_type == "knowledge_query"


def test_historical_report_is_not_treated_as_current_emergency() -> None:
    historical = assess_medical_risk("我昨天出现胸痛，现在已经缓解")
    recurrent = assess_medical_risk("我昨天胸痛，今天又出现胸痛")

    assert historical.risk_level == "low"
    assert historical.context_type == "negated_symptom"
    assert recurrent.risk_level == "emergency"
    assert recurrent.context_type == "current_self_report"


@pytest.mark.parametrize(
    ("question", "context_type"),
    [
        ("胸痛怎么办", "uncertain"),
        ("我父亲现在胸痛", "third_party_case"),
        ("患者血压 190/125", "third_party_case"),
    ],
)
def test_unconfirmed_emergency_context_uses_safety_caution(
    question: str, context_type: str
) -> None:
    assessment = assess_medical_risk(question)

    assert assessment.risk_level == "high"
    assert assessment.action == "safety_caution"
    assert assessment.context_type == context_type
    assert assessment.uncertainty_reasons


def test_controller_redirects_before_calling_llm() -> None:
    controller = object.__new__(UnifiedOrchestratorController)

    result = asyncio.run(
        controller.run(
            input_text="我现在血压 190/125，并且出现胸痛",
            trace_id="trace-1",
        )
    )

    assert result["output_type"] == "orchestrator_urgent_redirect"
    assert result["telemetry"]["final_source"] == "medical_risk_gate"
    assert result["telemetry"]["medical_risk"]["risk_level"] == "emergency"
    assert result["telemetry"]["medical_risk"]["context_type"] == "current_self_report"
    assert result["tool_observations"] == []


def test_controller_returns_caution_when_subject_is_uncertain() -> None:
    controller = object.__new__(UnifiedOrchestratorController)

    result = asyncio.run(
        controller.run(
            input_text="血压 190/125，而且胸痛",
            trace_id="trace-2",
        )
    )

    assert result["output_type"] == "orchestrator_medical_risk_clarification"
    assert result["telemetry"]["final_source"] == "medical_risk_gate"
    assert result["telemetry"]["medical_risk"]["context_type"] == "uncertain"
    assert result["tool_observations"] == []


def test_high_risk_question_abstains_with_only_one_source() -> None:
    question = "孕妇高血压可以怎么调整药物？"
    plan = build_question_plan(question, assess_medical_risk(question))
    evidence = Evidence(
        source_type="document",
        source_id="guide-1",
        origin_document_id="guide-1",
        chunk_id="chunk-1",
        content="妊娠期高血压治疗需要专业评估。",
        retrieval_method="dense",
    ).model_dump()

    class _Gateway:
        async def invoke_tool(
            self, tool_name: str, payload: dict[str, Any]
        ) -> dict[str, Any]:
            _ = tool_name, payload
            return {
                "ok": True,
                "result": {
                    "matched": True,
                    "history_id": "history-1",
                    "evidence": [evidence],
                },
            }

    decisions = iter(
        [
            (
                {
                    "action": "call_history_read",
                    "task_id": plan.retrieval_tasks[0].task_id,
                    "must_cover_id": plan.must_cover[0].item_id,
                    "history_id": "history-1",
                },
                {"total_tokens": 1},
            ),
            (
                {"action": "final_answer", "answer": "直接调整药物。"},
                {"total_tokens": 1},
            ),
        ]
    )

    async def decide_action(
        **kwargs: Any,
    ) -> tuple[dict[str, Any], dict[str, int]]:
        _ = kwargs
        return next(decisions)

    async def stream_final_answer(**kwargs: Any) -> tuple[str, dict[str, int]]:
        _ = kwargs
        raise AssertionError("高风险证据不足时不应生成答案")

    controller = object.__new__(UnifiedOrchestratorController)
    controller._mcp_gateway = _Gateway()
    controller._subagent_client = None
    controller._conf = SimpleNamespace(
        A2A_SUBAGENT_MAX_TOOL_CALLS_DEFAULT=3,
        A2A_SUBAGENT_MAX_TOOL_CALLS_MAX=3,
    )
    controller._decide_action = decide_action
    controller._stream_final_answer = stream_final_answer

    result = asyncio.run(
        controller.run(
            input_text=question,
            session_id="session-1",
            max_turns=2,
        )
    )

    assert result["output_type"] == "orchestrator_abstained_insufficient_evidence"
    sufficiency_items = result["evidence_summary"]["evidence_sufficiency"]["items"]
    assert "distinct_publications" in sufficiency_items[0]["missing_reasons"]
    assert result["telemetry"]["medical_risk"]["risk_level"] == "high"
