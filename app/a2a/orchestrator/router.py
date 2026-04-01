"""主控动态路由模块。"""

from __future__ import annotations

from app.a2a.models import SubTask


def select_agent(
    task: SubTask,
    coverage_map: dict[str, dict],
    failed_routes: list[dict],
    last_result: dict,
) -> str:
    task_id = str(task.subtask_id)
    coverage = coverage_map.get(task_id) if isinstance(coverage_map, dict) else {}
    status = str((coverage or {}).get("coverage_status") or "")

    if "text_quote" in task.evidence_type_required:
        preferred = "rag"
    elif "relation" in task.evidence_type_required:
        preferred = "kg"
    else:
        preferred = str(task.target_agent or "kg")

    if status == "strong":
        return preferred

    last_no_signal = bool((last_result or {}).get("no_signal"))
    last_agent = str((last_result or {}).get("target_agent") or "")
    if last_no_signal and last_agent in {"kg", "rag"}:
        return "rag" if last_agent == "kg" else "kg"

    recent_fail = False
    for row in failed_routes[-5:]:
        if not isinstance(row, dict):
            continue
        if str(row.get("subtask_id") or "") != task_id:
            continue
        if str(row.get("agent") or "") == preferred:
            recent_fail = True
            break
    if recent_fail:
        return "rag" if preferred == "kg" else "kg"
    return preferred
