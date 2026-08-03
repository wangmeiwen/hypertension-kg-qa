"""主控证据就绪和安全停止测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from app.a2a.models import Evidence
from app.a2a.orchestrator.controller import UnifiedOrchestratorController


def _evidence(content: str, source_id: str = "guide") -> dict[str, Any]:
    return Evidence(
        source_type="document",
        source_id=source_id,
        origin_document_id=source_id,
        chunk_id=f"chunk-{source_id}",
        content=content,
        retrieval_method="dense",
    ).model_dump()


def _controller() -> UnifiedOrchestratorController:
    controller = object.__new__(UnifiedOrchestratorController)
    controller._mcp_gateway = None
    controller._subagent_client = None
    controller._conf = SimpleNamespace(
        A2A_SUBAGENT_MAX_TOOL_CALLS_DEFAULT=3,
        A2A_SUBAGENT_MAX_TOOL_CALLS_MAX=3,
    )
    return controller


def test_answer_readiness_requires_direct_evidence() -> None:
    controller = _controller()

    readiness = controller._assess_answer_readiness([])

    assert readiness["ready"] is False
    assert readiness["missing_points"] == ["direct_evidence"]


def test_answer_readiness_accepts_single_direct_evidence() -> None:
    controller = _controller()

    readiness = controller._assess_answer_readiness(
        [{"evidence": [_evidence("诊室血压应规范测量。")]}]
    )

    assert readiness["ready"] is True
    assert readiness["evidence_count"] == 1
    assert readiness["missing_points"] == []


def test_answer_readiness_blocks_unresolved_numeric_conflict() -> None:
    controller = _controller()

    readiness = controller._assess_answer_readiness(
        [
            {
                "evidence": [
                    _evidence("治疗后风险下降 10%。", "guide-a"),
                    _evidence("治疗后风险下降 20%。", "guide-b"),
                ]
            }
        ]
    )

    assert readiness["ready"] is False
    assert readiness["unresolved_conflicts"] == ["numeric_conflict"]
    assert "unresolved_conflict" in readiness["missing_points"]


def test_run_does_not_force_answer_without_evidence() -> None:
    controller = _controller()
    decision_count = 0
    answer_generator_called = False

    async def decide_action(
        **kwargs: Any,
    ) -> tuple[dict[str, Any], dict[str, int]]:
        nonlocal decision_count
        _ = kwargs
        decision_count += 1
        return (
            {"action": "final_answer", "answer": "无证据答案"},
            {"total_tokens": 1},
        )

    async def stream_final_answer(**kwargs: Any) -> tuple[str, dict[str, int]]:
        nonlocal answer_generator_called
        _ = kwargs
        answer_generator_called = True
        return "不应生成", {"total_tokens": 1}

    controller._decide_action = decide_action
    controller._stream_final_answer = stream_final_answer

    result = asyncio.run(
        controller.run(
            input_text="高血压如何监测？",
            session_id="session-1",
            max_turns=2,
        )
    )

    assert decision_count == 2
    assert answer_generator_called is False
    assert result["output_type"] == "orchestrator_abstained_insufficient_evidence"
    assert result["confidence"] == "low"
    assert result["confidence_breakdown"]["level"] == "low"
    assert result["evidence_summary"]["missing_points"] == ["direct_evidence"]
    assert result["telemetry"]["final_source"] == "insufficient_evidence_policy"
