"""主控计划任务并行执行测试。"""

from __future__ import annotations

import asyncio

from app.a2a.medical_risk import MedicalRiskAssessment
from app.a2a.models import Evidence, SubAgentResult, SubTask
from app.a2a.orchestrator.controller import UnifiedOrchestratorController
from app.a2a.question_planner import build_question_plan


class _ConcurrentSubagentClient:
    def __init__(self, failing_target: str = "") -> None:
        self.failing_target = failing_target
        self.active = 0
        self.max_active = 0

    async def send_task(self, task: SubTask) -> SubAgentResult:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.02)
        self.active -= 1
        if task.target_agent == self.failing_target:
            raise RuntimeError("planned failure")
        return SubAgentResult(
            subtask_id=task.subtask_id,
            target_agent=task.target_agent,
            question=task.question,
            direct_support=True,
            evidence=[
                Evidence(
                    source_type=task.target_agent,
                    source_id=f"{task.target_agent}-source",
                    content=f"{task.target_agent} evidence",
                )
            ],
        )


def _controller(client: _ConcurrentSubagentClient) -> UnifiedOrchestratorController:
    controller = object.__new__(UnifiedOrchestratorController)
    controller._subagent_client = client
    return controller


def test_builds_independent_kg_and_rag_tasks_for_same_must_cover() -> None:
    controller = _controller(_ConcurrentSubagentClient())
    plan = build_question_plan("高血压患者如何监测？", MedicalRiskAssessment())

    tasks = controller._build_ready_subtasks(
        action={"action": "call_kg", "reason": "补全证据"},
        default_question=plan.original_question,
        max_tool_calls=3,
        question_plan=plan,
    )

    assert [task.target_agent for task in tasks] == ["kg", "rag"]
    assert len({task.subtask_id for task in tasks}) == 1
    assert len({task.question for task in tasks}) == 1


def test_dependency_blocks_task_from_parallel_batch() -> None:
    controller = _controller(_ConcurrentSubagentClient())
    plan = build_question_plan("高血压患者如何监测？", MedicalRiskAssessment())
    plan.retrieval_tasks[1].dependency_on = ["uncovered-item"]

    tasks = controller._build_ready_subtasks(
        action={"action": "call_kg"},
        default_question=plan.original_question,
        max_tool_calls=3,
        question_plan=plan,
    )

    assert [task.target_agent for task in tasks] == ["kg"]


def test_executes_tasks_concurrently_and_isolates_single_failure() -> None:
    client = _ConcurrentSubagentClient(failing_target="rag")
    controller = _controller(client)
    plan = build_question_plan("高血压患者如何监测？", MedicalRiskAssessment())
    tasks = controller._build_ready_subtasks(
        action={"action": "call_kg"},
        default_question=plan.original_question,
        max_tool_calls=3,
        question_plan=plan,
    )

    results = asyncio.run(controller._execute_subtasks(tasks))

    assert client.max_active == 2
    assert [result.target_agent for result in results] == ["kg", "rag"]
    assert results[0].direct_support is True
    assert results[1].no_signal is True
    assert results[1].reason == "subagent_error:RuntimeError"
