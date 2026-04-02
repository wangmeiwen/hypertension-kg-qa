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
    binding_insufficient = bool((coverage or {}).get("binding_insufficient"))
    high_evidence_count = int((coverage or {}).get("high_evidence_count") or 0)
    low_signal = bool((coverage or {}).get("low_signal"))
    evidence_count = int((coverage or {}).get("evidence_count") or 0)
    can_reroute = round_index <= max_rounds

    should_reroute_for_low_value = (
        can_reroute
        and
        coverage_status != "strong"
        and bool(missing)
        and evidence_count > 0
        and (
            binding_insufficient
            or high_evidence_count == 0
            or low_signal
            or (round_index >= 2 and coverage_status == "partial")
        )
    )

    if coverage_status == "strong":
        return OrchestratorDecision(
            need_supplement=False,
            supplement_type="none",
            target_subtask_id=int(task.subtask_id),
            reason="coverage_sufficient",
        )

    if last_no_signal and can_reroute:
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

    if should_reroute_for_low_value:
        next_agent = "rag" if str(last_agent or "") == "kg" else "kg"
        return OrchestratorDecision(
            need_supplement=True,
            supplement_type="reroute",
            target_subtask_id=int(task.subtask_id),
            next_agent=next_agent,
            next_question=str(task.question or ""),
            query_hint={
                "missing_points": missing,
                "strategy": "switch_agent_low_signal",
                "binding_insufficient": binding_insufficient,
                "high_evidence_count": high_evidence_count,
                "low_signal": low_signal,
            },
            reason="low_value_signal_reroute",
        )

    if round_index >= max_rounds:
        return OrchestratorDecision(
            need_supplement=False,
            supplement_type="none",
            target_subtask_id=int(task.subtask_id),
            reason="subtask_max_rounds_reached",
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
