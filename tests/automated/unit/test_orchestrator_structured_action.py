"""主控结构化动作测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from app.a2a.medical_risk import MedicalRiskAssessment
from app.a2a.models import OrchestratorAction
from app.a2a.orchestrator.controller import (
    OrchestratorActionValidationError,
    UnifiedOrchestratorController,
)
from app.a2a.question_planner import build_question_plan


class _FakeLlmClient:
    def __init__(self, responses: list[str]) -> None:
        self.responses = iter(responses)
        self.calls: list[dict[str, Any]] = []

    async def chat_with_tokens_async(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(
            content=next(self.responses),
            prompt_tokens=1,
            completion_tokens=2,
            total_tokens=3,
        )

    async def chat_with_vision_async(self, **kwargs: Any) -> SimpleNamespace:
        return await self.chat_with_tokens_async(**kwargs)


def _build_controller(responses: list[str]) -> UnifiedOrchestratorController:
    controller = object.__new__(UnifiedOrchestratorController)
    controller._client = _FakeLlmClient(responses)
    controller._subagent_client = None
    controller._mcp_gateway = None
    controller._conf = SimpleNamespace(
        LLM_REQUEST_TIMEOUT_SECONDS=30,
        A2A_SUBAGENT_MAX_TOOL_CALLS_DEFAULT=3,
        A2A_SUBAGENT_MAX_TOOL_CALLS_MAX=3,
    )

    async def prepare_messages(
        **kwargs: Any,
    ) -> tuple[list[dict[str, str]], dict[str, int]]:
        _ = kwargs
        return ([{"role": "user", "content": "question"}], {"total_tokens": 0})

    controller._prepare_budgeted_messages = prepare_messages
    return controller


def _decide(
    controller: UnifiedOrchestratorController,
) -> tuple[dict[str, Any], dict[str, int]]:
    return asyncio.run(
        controller._decide_action(
            question="高血压如何监测？",
            session_id="session-1",
            qa_memory_text="无",
            retrieval_index_text="无",
            tool_observations=[],
            turn_index=1,
            max_turns=3,
            compression_state={},
            processing_steps=[],
            progress_callback=None,
        )
    )


def test_orchestrator_action_rejects_missing_required_fields() -> None:
    with pytest.raises(ValidationError, match="final_answer_requires_answer"):
        OrchestratorAction.model_validate({"action": "final_answer"})
    with pytest.raises(ValidationError, match="history_read_requires_locator"):
        OrchestratorAction.model_validate({"action": "call_history_read"})
    with pytest.raises(ValidationError, match="retrieval_action_requires_task_id"):
        OrchestratorAction.model_validate(
            {"action": "call_kg", "must_cover_id": "mc-1"}
        )
    with pytest.raises(
        ValidationError, match="retrieval_action_requires_must_cover_id"
    ):
        OrchestratorAction.model_validate({"action": "call_rag", "task_id": "task-1"})
    with pytest.raises(ValidationError, match="history_read_requires_task_id"):
        OrchestratorAction.model_validate(
            {"action": "call_history_read", "history_id": "history-1"}
        )
    with pytest.raises(ValidationError, match="history_read_requires_must_cover_id"):
        OrchestratorAction.model_validate(
            {
                "action": "call_history_read",
                "history_id": "history-1",
                "task_id": "task-1",
            }
        )


def test_decide_action_retries_once_and_uses_structured_output() -> None:
    controller = _build_controller(
        [
            '{"action":"final_answer"}',
            '{"action":"call_kg","task_id":"task-1","must_cover_id":"mc-1","question":"高血压监测","reason":"需要图谱"}',
        ]
    )

    action, tokens = _decide(controller)

    assert action["action"] == "call_kg"
    assert tokens == {"prompt_tokens": 2, "completion_tokens": 4, "total_tokens": 6}
    assert len(controller._client.calls) == 2
    assert all(
        call["response_format"] == {"type": "json_object"}
        for call in controller._client.calls
    )
    retry_messages = controller._client.calls[1]["messages"]
    assert retry_messages[-1]["role"] == "user"
    assert "JSON Schema" in retry_messages[-1]["content"]


def test_decide_action_rejects_markdown_wrapped_json_after_one_retry() -> None:
    controller = _build_controller(
        [
            '```json\n{"action":"call_rag","task_id":"task-1","must_cover_id":"mc-1","question":"监测"}\n```',
            '{"action":"unknown"}',
        ]
    )

    with pytest.raises(OrchestratorActionValidationError):
        _decide(controller)

    assert len(controller._client.calls) == 2


def test_decide_action_retries_when_task_binding_is_invalid() -> None:
    plan = build_question_plan("高血压患者如何监测？", MedicalRiskAssessment())
    kg_task = next(task for task in plan.retrieval_tasks if task.target_agent == "kg")
    controller = _build_controller(
        [
            (
                '{"action":"call_rag","task_id":"'
                f'{kg_task.task_id}","must_cover_id":"{kg_task.must_cover_id}"'
                "}"
            ),
            (
                '{"action":"call_kg","task_id":"'
                f'{kg_task.task_id}","must_cover_id":"{kg_task.must_cover_id}"'
                "}"
            ),
        ]
    )

    action, _ = asyncio.run(
        controller._decide_action(
            question=plan.original_question,
            session_id="session-1",
            qa_memory_text="无",
            retrieval_index_text="无",
            question_plan_text=plan.model_dump_json(),
            question_plan=plan,
            tool_observations=[],
            turn_index=1,
            max_turns=3,
            compression_state={},
            processing_steps=[],
            progress_callback=None,
        )
    )

    assert action["action"] == "call_kg"
    assert action["task_id"] == kg_task.task_id
    assert len(controller._client.calls) == 2
    assert (
        "action_task_agent_mismatch"
        in controller._client.calls[1]["messages"][-1]["content"]
    )


def test_run_safely_abstains_when_structured_action_is_invalid() -> None:
    controller = _build_controller(["not-json", '{"action":"final_answer"}'])

    result = asyncio.run(
        controller.run(
            input_text="高血压如何监测？",
            session_id="session-1",
            max_turns=2,
        )
    )

    assert result["output_type"] == "orchestrator_abstained_invalid_decision"
    assert result["confidence"] == 0.0
    assert result["telemetry"]["final_source"] == "invalid_orchestrator_action"
    assert len(controller._client.calls) == 2
