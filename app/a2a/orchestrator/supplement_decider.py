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
    """执行decide_supplement流程并返回结果。
    
    Args:
        task: 当前处理任务。
        round_index: 函数输入参数。
        max_rounds: 函数输入参数。
        coverage: 函数输入参数。
        last_agent: 函数输入参数。
        last_no_signal: 函数输入参数。
    
    Returns:
        OrchestratorDecision: 执行结果。
    """
    coverage_status = str((coverage or {}).get("coverage_status") or "none")
    missing_points = (coverage or {}).get("missing_points")
    missing = missing_points if isinstance(missing_points, list) else []
    binding_insufficient = bool((coverage or {}).get("binding_insufficient"))
    high_evidence_count = int((coverage or {}).get("high_evidence_count") or 0)
    low_signal = bool((coverage or {}).get("low_signal"))
    evidence_count = int((coverage or {}).get("evidence_count") or 0)
    can_reroute = round_index <= max_rounds

    # 可执行 KG->RAG 条件：仅在 KG 连续无充分信号后才开启 RAG 补检索，避免早切换。
    should_escalate_to_rag = (
        can_reroute
        and str(last_agent or "") == "kg"
        and str(task.target_agent or "kg") == "kg"
        and round_index >= 2
        and coverage_status != "strong"
        and bool(missing)
        and (
            last_no_signal
            or low_signal
            or binding_insufficient
            or high_evidence_count == 0
            or evidence_count == 0
        )
    )

    if coverage_status == "strong":
        return OrchestratorDecision(
            need_supplement=False,
            supplement_type="none",
            target_subtask_id=int(task.subtask_id),
            reason="coverage_sufficient",
        )

    if should_escalate_to_rag:
        return OrchestratorDecision(
            need_supplement=True,
            supplement_type="reroute",
            target_subtask_id=int(task.subtask_id),
            next_agent="rag",
            next_question=str(task.question or ""),
            query_hint={
                "missing_points": missing,
                "strategy": "kg_to_rag_escalation",
                "trigger_round": int(round_index),
                "last_no_signal": bool(last_no_signal),
                "binding_insufficient": binding_insufficient,
                "high_evidence_count": high_evidence_count,
                "low_signal": low_signal,
                "evidence_count": evidence_count,
            },
            reason="kg_to_rag_escalation",
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
