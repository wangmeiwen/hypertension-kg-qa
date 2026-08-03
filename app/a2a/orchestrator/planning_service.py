"""问题计划动作校验与 SubTask 构建。"""

from __future__ import annotations

from typing import Any

from app.a2a.models import (
    MustCoverItem,
    QuestionPlan,
    RetrievalTask,
    SubAgentConstraints,
    SubTask,
)


def _clean(value: Any, limit: int) -> str:
    return str(value or "").replace("\n", " ").strip()[:limit]


class PlanningService:
    """维护主控动作与结构化问题计划之间的强绑定。"""

    def validate_action(
        self, action: dict[str, Any], question_plan: QuestionPlan
    ) -> tuple[RetrievalTask | None, MustCoverItem | None]:
        action_name = str(action.get("action") or "").strip()
        if action_name == "final_answer":
            return None, None
        must_cover_id = _clean(action.get("must_cover_id"), 64)
        must_cover_item = next(
            (
                item
                for item in question_plan.must_cover
                if item.item_id == must_cover_id
            ),
            None,
        )
        if must_cover_item is None:
            raise ValueError("action_must_cover_not_found")
        if must_cover_item.status == "missing":
            raise ValueError("action_must_cover_is_missing")
        task_id = _clean(action.get("task_id"), 64)
        retrieval_task = next(
            (item for item in question_plan.retrieval_tasks if item.task_id == task_id),
            None,
        )
        if retrieval_task is None:
            raise ValueError("action_task_not_found")
        if retrieval_task.must_cover_id != must_cover_id:
            raise ValueError("action_task_must_cover_mismatch")
        if retrieval_task.status not in {"pending", "needs_supplement"}:
            raise ValueError("action_task_not_actionable")
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
        if any(
            dependency not in covered_ids and dependency not in completed_task_ids
            for dependency in retrieval_task.dependency_on
        ):
            raise ValueError("action_task_dependency_unmet")
        if action_name == "call_history_read":
            if must_cover_item.status != "pending":
                raise ValueError("history_must_cover_not_actionable")
            return retrieval_task, must_cover_item
        expected_agent = "kg" if action_name == "call_kg" else "rag"
        if retrieval_task.target_agent != expected_agent:
            raise ValueError("action_task_agent_mismatch")
        return retrieval_task, must_cover_item

    def build_subtask(
        self,
        *,
        action: dict[str, Any],
        max_tool_calls: int,
        question_plan: QuestionPlan,
    ) -> SubTask:
        planned_task, planned_item = self.validate_action(action, question_plan)
        if planned_task is None or planned_item is None:
            raise ValueError("retrieval_action_required")
        return SubTask(
            task_id=planned_task.task_id,
            must_cover_id=planned_item.item_id,
            subtask_id=question_plan.must_cover.index(planned_item) + 1,
            target_agent=planned_task.target_agent,
            question=planned_task.question,
            intent="retrieve_for_answer",
            dependency_on=list(planned_task.dependency_on),
            evidence_type_required=list(
                planned_item.evidence_policy.required_retrievers
            ),
            must_cover=[planned_item.question],
            priority=50,
            coverage_status=planned_item.status,
            query_focus=planned_item.claim_type,
            query_directions=list(planned_item.applicability_conditions),
            constraints=SubAgentConstraints(
                allow_extra_retrieval=False,
                max_tool_calls=max_tool_calls,
            ),
            tool_name=(
                "kg_auto" if planned_task.target_agent == "kg" else "rag_search"
            ),
            reason=_clean(action.get("reason"), 200),
        )
