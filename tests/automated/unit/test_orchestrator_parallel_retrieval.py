"""主控计划任务并行执行测试。"""

from __future__ import annotations

import asyncio

from app.a2a.medical_risk import MedicalRiskAssessment
from app.a2a.models import Evidence, QuestionPlan, SubAgentResult, SubTask
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


def _action(plan: QuestionPlan, target_agent: str = "kg") -> dict[str, str]:
    task = next(
        item for item in plan.retrieval_tasks if item.target_agent == target_agent
    )
    return {
        "action": f"call_{target_agent}",
        "task_id": task.task_id,
        "must_cover_id": task.must_cover_id,
        "reason": "补全证据",
    }


def _require_both_retrievers(plan: QuestionPlan) -> None:
    plan.must_cover[0].evidence_policy.required_retrievers = ["kg", "rag"]
    plan.must_cover[0].evidence_policy.optional_retrievers = []
    for task in plan.retrieval_tasks:
        task.retriever_requirement = "required"


def test_optional_retriever_does_not_force_sibling_execution() -> None:
    controller = _controller(_ConcurrentSubagentClient())
    plan = build_question_plan("高血压患者如何监测？", MedicalRiskAssessment())

    tasks = controller._build_ready_subtasks(
        action=_action(plan),
        max_tool_calls=3,
        question_plan=plan,
    )

    assert [task.target_agent for task in tasks] == ["kg"]
    assert len({task.subtask_id for task in tasks}) == 1
    assert len({task.question for task in tasks}) == 1


def test_dependency_blocks_task_from_parallel_batch() -> None:
    controller = _controller(_ConcurrentSubagentClient())
    plan = build_question_plan("高血压患者如何监测？", MedicalRiskAssessment())
    _require_both_retrievers(plan)
    rag_task = next(task for task in plan.retrieval_tasks if task.target_agent == "rag")
    rag_task.dependency_on = ["uncovered-item"]

    tasks = controller._build_ready_subtasks(
        action=_action(plan),
        max_tool_calls=3,
        question_plan=plan,
    )

    assert [task.target_agent for task in tasks] == ["kg"]


def test_executes_tasks_concurrently_and_isolates_single_failure() -> None:
    client = _ConcurrentSubagentClient(failing_target="rag")
    controller = _controller(client)
    plan = build_question_plan("高血压患者如何监测？", MedicalRiskAssessment())
    _require_both_retrievers(plan)
    tasks = controller._build_ready_subtasks(
        action=_action(plan),
        max_tool_calls=3,
        question_plan=plan,
    )

    results = asyncio.run(controller._execute_subtasks(tasks))

    assert client.max_active == 2
    assert [result.target_agent for result in results] == ["kg", "rag"]
    assert results[0].direct_support is True
    assert results[1].no_signal is True
    assert results[1].reason == "subagent_error:RuntimeError"
