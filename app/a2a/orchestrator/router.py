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

    # 主路径默认遵从 decompose/planner 已确定的 target_agent。
    preferred = "rag" if str(task.target_agent or "kg") == "rag" else "kg"

    # 明确 reroute 补检索任务直接使用既定目标代理。
    if str(task.agent_role or "") == "supplement" and "reroute" in str(task.reason or ""):
        return preferred

    if status == "strong":
        return preferred

    last_no_signal = bool((last_result or {}).get("no_signal"))
    last_agent = str((last_result or {}).get("target_agent") or "")
    if last_no_signal and last_agent in {"kg", "rag"} and last_agent == preferred:
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
