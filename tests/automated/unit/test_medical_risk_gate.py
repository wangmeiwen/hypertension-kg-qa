"""医疗风险 Gate 测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from app.a2a.medical_risk import assess_medical_risk
from app.a2a.models import Evidence
from app.a2a.orchestrator.controller import UnifiedOrchestratorController


@pytest.mark.parametrize(
    "question",
    [
        "血压 190/125，而且胸痛",
        "突然呼吸困难并且意识不清",
        "高血压急症应该怎么办",
    ],
)
def test_emergency_signals_trigger_urgent_redirect(question: str) -> None:
    assessment = assess_medical_risk(question)

    assert assessment.risk_level == "emergency"
    assert assessment.action == "urgent_redirect"


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
    assert assessment.minimum_distinct_sources == 2


def test_negated_emergency_signal_does_not_trigger_redirect() -> None:
    assessment = assess_medical_risk("目前没有胸痛，只想了解家庭血压监测")

    assert assessment.risk_level == "low"
    assert assessment.action == "standard"


def test_controller_redirects_before_calling_llm() -> None:
    controller = object.__new__(UnifiedOrchestratorController)

    result = asyncio.run(
        controller.run(
            input_text="血压 190/125，并且出现胸痛",
            trace_id="trace-1",
        )
    )

    assert result["output_type"] == "orchestrator_urgent_redirect"
    assert result["telemetry"]["final_source"] == "medical_risk_gate"
    assert result["telemetry"]["medical_risk"]["risk_level"] == "emergency"
    assert result["tool_observations"] == []


def test_high_risk_question_abstains_with_only_one_source() -> None:
    evidence = Evidence(
        source_type="document",
        source_id="guide-1",
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
            input_text="孕妇高血压可以怎么调整药物？",
            session_id="session-1",
            max_turns=2,
        )
    )

    assert result["output_type"] == "orchestrator_abstained_insufficient_evidence"
    assert "independent_sources" in result["evidence_summary"]["missing_points"]
    assert result["telemetry"]["medical_risk"]["risk_level"] == "high"
