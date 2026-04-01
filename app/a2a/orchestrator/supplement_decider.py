"""覆盖度驱动补检索决策模块。"""

from __future__ import annotations

from app.a2a.models import OrchestratorDecision, SubTask


def decide_supplement(
    *,
    task: SubTask,
    round_index: int,
    max_rounds: int,
    coverage: dict,
    last_agent: str,
    last_no_signal: bool,
) -> OrchestratorDecision:
    coverage_status = str((coverage or {}).get("coverage_status") or "none")
    missing_points = (coverage or {}).get("missing_points")
    missing = missing_points if isinstance(missing_points, list) else []

    if coverage_status == "strong":
        return OrchestratorDecision(
            need_supplement=False,
            supplement_type="none",
            target_subtask_id=int(task.subtask_id),
            reason="coverage_sufficient",
        )

    if round_index >= max_rounds:
        return OrchestratorDecision(
            need_supplement=False,
            supplement_type="none",
            target_subtask_id=int(task.subtask_id),
            reason="max_rounds_with_insufficient_evidence",
        )

    if last_no_signal:
        next_agent = "rag" if str(last_agent or "") == "kg" else "kg"
        return OrchestratorDecision(
            need_supplement=True,
            supplement_type="reroute",
            target_subtask_id=int(task.subtask_id),
            next_agent=next_agent,
            next_question=str(task.question or ""),
            query_hint={"missing_points": missing, "strategy": "switch_agent"},
            reason="no_signal_reroute",
        )

    if missing:
        return OrchestratorDecision(
            need_supplement=True,
            supplement_type="query_refine",
            target_subtask_id=int(task.subtask_id),
            next_agent=str(task.target_agent or "kg"),
            next_question=str(task.question or ""),
            query_hint={"missing_points": missing, "strategy": "refine_query"},
            reason="coverage_gap_refine",
        )

    return OrchestratorDecision(
        need_supplement=False,
        supplement_type="none",
        target_subtask_id=int(task.subtask_id),
        reason="no_supplement_needed",
    )
