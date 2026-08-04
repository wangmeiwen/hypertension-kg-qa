"""结构化检索 DAG 的 ready-wave 构建与并发执行。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.a2a.models import QuestionPlan, SubAgentResult, SubTask
from app.a2a.orchestrator.planning_service import PlanningService

logger = logging.getLogger(__name__)


class RetrievalScheduler:
    """只负责可执行任务选择、并发限制、重试与失败隔离。"""

    def __init__(
        self,
        *,
        subagent_client: Any,
        planning_service: PlanningService,
        total_concurrency: int,
        agent_concurrency: int,
        task_timeout_seconds: float,
        max_attempts: int,
    ) -> None:
        self._subagent_client = subagent_client
        self._planning_service = planning_service
        self._total_concurrency = total_concurrency
        self._agent_concurrency = agent_concurrency
        self._task_timeout_seconds = task_timeout_seconds
        self._max_attempts = max_attempts

    def build_ready_subtasks(
        self,
        *,
        action: dict[str, Any],
        max_tool_calls: int,
        question_plan: QuestionPlan,
    ) -> list[SubTask]:
        primary = self._planning_service.build_subtask(
            action=action,
            max_tool_calls=max_tool_calls,
            question_plan=question_plan,
        )
        covered_ids = {
            item.item_id
            for item in question_plan.must_cover
            if item.status == "covered"
        }
        completed_task_ids = {
            item.task_id
            for item in question_plan.retrieval_tasks
            if item.status == "completed"
        }
        actionable_item_ids = {
            item.item_id
            for item in question_plan.must_cover
            if item.status == "pending"
        }
        ready_tasks = [
            item
            for item in question_plan.retrieval_tasks
            if item.must_cover_id in actionable_item_ids
            and item.status in {"pending", "needs_supplement"}
            and all(
                dependency in covered_ids or dependency in completed_task_ids
                for dependency in item.dependency_on
            )
        ]
        task_order = {
            item.task_id: index
            for index, item in enumerate(question_plan.retrieval_tasks)
        }
        ready_tasks.sort(
            key=lambda item: (
                item.task_id != primary.task_id,
                item.retriever_requirement != "required",
                task_order[item.task_id],
            )
        )
        unique_tasks = list({item.task_id: item for item in ready_tasks}.values())
        return [
            self._planning_service.build_subtask(
                action={
                    "action": f"call_{task.target_agent}",
                    "task_id": task.task_id,
                    "must_cover_id": task.must_cover_id,
                    "question": task.question,
                    "reason": action.get("reason"),
                },
                max_tool_calls=max_tool_calls,
                question_plan=question_plan,
            )
            for task in unique_tasks
        ]

    async def execute_subtasks(self, tasks: list[SubTask]) -> list[SubAgentResult]:
        unique_tasks = list({task.task_id: task for task in tasks}.values())
        total_semaphore = asyncio.Semaphore(self._total_concurrency)
        agent_semaphores = {
            agent: asyncio.Semaphore(self._agent_concurrency) for agent in ("kg", "rag")
        }

        def failure_result(
            task: SubTask, *, reason: str, error: str, attempts: int
        ) -> SubAgentResult:
            return SubAgentResult(
                task_id=task.task_id,
                must_cover_id=task.must_cover_id,
                subtask_id=task.subtask_id,
                target_agent=task.target_agent,
                question=task.question,
                signal="none",
                coverage={
                    "coverage_status": "none",
                    "coverage_ratio": 0.0,
                    "evidence_count": 0,
                },
                missing_points=list(task.must_cover),
                no_signal=True,
                reason=reason,
                telemetry={
                    "error": error,
                    "attempts": attempts,
                    "task_timeout_seconds": self._task_timeout_seconds,
                },
            )

        async def execute(task: SubTask) -> SubAgentResult:
            last_error = ""
            for attempt in range(1, self._max_attempts + 1):
                task.attempt_count = attempt
                try:
                    async with agent_semaphores[task.target_agent], total_semaphore:
                        result = await asyncio.wait_for(
                            self._subagent_client.send_task(task),
                            timeout=self._task_timeout_seconds,
                        )
                    result.task_id = task.task_id
                    result.must_cover_id = task.must_cover_id
                    result.subtask_id = task.subtask_id
                    result.target_agent = task.target_agent
                    result.telemetry = {
                        **result.telemetry,
                        "attempts": attempt,
                        "task_timeout_seconds": self._task_timeout_seconds,
                    }
                    return result
                except TimeoutError:
                    last_error = "task_timeout"
                    logger.warning(
                        "子代理任务超时: task_id=%s attempt=%s",
                        task.task_id,
                        attempt,
                    )
                except Exception as exc:
                    last_error = str(exc).replace("\n", " ").strip()[:240]
                    logger.exception(
                        "子代理任务执行失败: task_id=%s attempt=%s",
                        task.task_id,
                        attempt,
                    )
                    if attempt == self._max_attempts:
                        return failure_result(
                            task,
                            reason=f"subagent_error:{type(exc).__name__}",
                            error=last_error,
                            attempts=attempt,
                        )
            return failure_result(
                task,
                reason="subagent_timeout",
                error=last_error,
                attempts=self._max_attempts,
            )

        return list(await asyncio.gather(*(execute(task) for task in unique_tasks)))
