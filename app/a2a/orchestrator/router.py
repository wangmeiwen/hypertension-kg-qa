"""主控动态路由模块。"""

from __future__ import annotations

from app.a2a.models import SubTask


def select_agent(
    task: SubTask,
    coverage_map: dict[str, dict],
    failed_routes: list[dict],
    last_result: dict,
) -> str:
    """执行select_agent流程并返回结果。
    
    Args:
        task: 当前处理任务。
        coverage_map: 子任务覆盖度映射。
        failed_routes: 函数输入参数。
        last_result: 函数输入参数。
    
    Returns:
        str: 文本结果。
    """
    task_id = str(task.subtask_id)
    coverage = coverage_map.get(task_id) if isinstance(coverage_map, dict) else {}
    status = str((coverage or {}).get("coverage_status") or "")
    _ = failed_routes
    _ = last_result

    # 主路径默认遵从 decompose/planner 已确定的 target_agent。
    preferred = "rag" if str(task.target_agent or "kg") == "rag" else "kg"

    # 明确 reroute 补检索任务直接使用既定目标代理。
    if str(task.agent_role or "") == "supplement" and "reroute" in str(task.reason or ""):
        return preferred

    if status == "strong":
        return preferred
    return preferred
