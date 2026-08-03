"""主控计划任务并行执行测试。"""

from __future__ import annotations

import asyncio

import pytest

from app.a2a.medical_risk import MedicalRiskAssessment
from app.a2a.models import Evidence, QuestionPlan, SubAgentResult, SubTask
from app.a2a.orchestrator.controller import UnifiedOrchestratorController
from app.a2a.question_planner import build_question_plan


class _ConcurrentSubagentClient:
    def __init__(self, failing_target: str = "", delay: float = 0.02) -> None:
        self.failing_target = failing_target
        self.delay = delay
        self.active = 0
        self.max_active = 0
        self.active_by_agent = {"kg": 0, "rag": 0}
        self.max_active_by_agent = {"kg": 0, "rag": 0}
        self.attempts_by_task: dict[str, int] = {}

    async def send_task(self, task: SubTask) -> SubAgentResult:
        self.attempts_by_task[task.task_id] = (
            self.attempts_by_task.get(task.task_id, 0) + 1
        )
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.active_by_agent[task.target_agent] += 1
        self.max_active_by_agent[task.target_agent] = max(
            self.max_active_by_agent[task.target_agent],
            self.active_by_agent[task.target_agent],
        )
        await asyncio.sleep(self.delay)
        self.active -= 1
        self.active_by_agent[task.target_agent] -= 1
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


def test_ready_wave_includes_optional_sibling_tasks() -> None:
    controller = _controller(_ConcurrentSubagentClient())
    plan = build_question_plan("高血压患者如何监测？", MedicalRiskAssessment())

    tasks = controller._build_ready_subtasks(
        action=_action(plan),
        max_tool_calls=3,
        question_plan=plan,
    )

    assert [task.target_agent for task in tasks] == ["kg", "rag"]
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


def test_task_dependency_requires_completed_predecessor() -> None:
    controller = _controller(_ConcurrentSubagentClient())
    plan = build_question_plan("高血压患者如何监测？", MedicalRiskAssessment())
    kg_task = next(task for task in plan.retrieval_tasks if task.target_agent == "kg")
    rag_task = next(task for task in plan.retrieval_tasks if task.target_agent == "rag")
    rag_task.dependency_on = [kg_task.task_id]

    with pytest.raises(ValueError, match="action_task_dependency_unmet"):
        controller._build_subtask(
            action=_action(plan, "rag"),
            max_tool_calls=3,
            question_plan=plan,
        )

    kg_task.status = "completed"
    task = controller._build_subtask(
        action=_action(plan, "rag"),
        max_tool_calls=3,
        question_plan=plan,
    )

    assert task.task_id == rag_task.task_id
    assert task.dependency_on == [kg_task.task_id]


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
    assert results[1].telemetry["attempts"] == 2


def test_ready_wave_spans_multiple_must_cover_items() -> None:
    controller = _controller(_ConcurrentSubagentClient())
    plan = build_question_plan("如何监测血压，并说明指南来源", MedicalRiskAssessment())

    tasks = controller._build_ready_subtasks(
        action=_action(plan),
        max_tool_calls=3,
        question_plan=plan,
    )

    assert len(tasks) == 4
    assert {task.must_cover_id for task in tasks} == {
        item.item_id for item in plan.must_cover
    }
    assert {task.target_agent for task in tasks} == {"kg", "rag"}


def test_cross_item_dependency_respects_topological_order() -> None:
    controller = _controller(_ConcurrentSubagentClient())
    plan = build_question_plan("如何监测血压，并说明指南来源", MedicalRiskAssessment())
    first_item, second_item = plan.must_cover
    for task in plan.retrieval_tasks:
        if task.must_cover_id == second_item.item_id:
            task.dependency_on = [first_item.item_id]

    first_wave = controller._build_ready_subtasks(
        action=_action(plan),
        max_tool_calls=3,
        question_plan=plan,
    )
    first_item.status = "covered"
    second_primary = next(
        task
        for task in plan.retrieval_tasks
        if task.must_cover_id == second_item.item_id and task.target_agent == "kg"
    )
    second_wave = controller._build_ready_subtasks(
        action={
            "action": "call_kg",
            "task_id": second_primary.task_id,
            "must_cover_id": second_item.item_id,
        },
        max_tool_calls=3,
        question_plan=plan,
    )

    assert {task.must_cover_id for task in first_wave} == {first_item.item_id}
    assert {task.must_cover_id for task in second_wave} == {second_item.item_id}


def test_execution_enforces_total_and_per_agent_concurrency_limits() -> None:
    client = _ConcurrentSubagentClient()
    controller = _controller(client)
    plan = build_question_plan(
        "如何监测血压，并说明指南来源，并说明与肾病的关系",
        MedicalRiskAssessment(),
    )
    tasks = controller._build_ready_subtasks(
        action=_action(plan),
        max_tool_calls=3,
        question_plan=plan,
    )

    results = asyncio.run(controller._execute_subtasks(tasks))

    assert len(results) == len({task.task_id for task in tasks})
    assert client.max_active <= controller._DAG_TOTAL_CONCURRENCY
    assert max(client.max_active_by_agent.values()) <= controller._DAG_AGENT_CONCURRENCY


def test_execution_times_out_and_retries_task() -> None:
    client = _ConcurrentSubagentClient(delay=0.05)
    controller = _controller(client)
    controller._DAG_TASK_TIMEOUT_SECONDS = 0.01
    plan = build_question_plan("高血压患者如何监测？", MedicalRiskAssessment())
    tasks = controller._build_ready_subtasks(
        action=_action(plan),
        max_tool_calls=3,
        question_plan=plan,
    )

    results = asyncio.run(controller._execute_subtasks([tasks[0], tasks[0]]))

    assert len(results) == 1
    assert results[0].reason == "subagent_timeout"
    assert results[0].telemetry["attempts"] == 2
    assert client.attempts_by_task[tasks[0].task_id] == 2
