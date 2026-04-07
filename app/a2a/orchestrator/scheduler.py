"""主控优先级调度模块。"""

from __future__ import annotations

from app.a2a.models import SubTask


def _dependencies_satisfied(task: SubTask, subtask_status: dict[str, str]) -> bool:
    """执行dependencies_satisfied流程并返回结果。
    
    Args:
        task: 当前处理任务。
        subtask_status: 函数输入参数。
    
    Returns:
        bool: 是否满足条件。
    """
    if not task.dependency_on:
        return True
    for dep in task.dependency_on:
        dep_state = str((subtask_status or {}).get(str(dep)) or "")
        if dep_state != "done":
            return False
    return True


def pick_next_task(
    pending_tasks: list[SubTask],
    subtask_status: dict[str, str],
) -> tuple[SubTask | None, list[SubTask]]:
    """执行pick_next_task流程并返回结果。
    
    Args:
        pending_tasks: 函数输入参数。
        subtask_status: 函数输入参数。
    
    Returns:
        tuple[SubTask | None, list[SubTask]]: 执行结果。
    """
    eligible: list[SubTask] = []
    waiting: list[SubTask] = []
    for task in pending_tasks:
        if _dependencies_satisfied(task, subtask_status):
            eligible.append(task)
        else:
            waiting.append(task)

    if not eligible:
        return None, pending_tasks

    eligible.sort(key=lambda task: (-int(task.priority), int(task.attempt_count), int(task.subtask_id)))
    current = eligible[0]
    remaining: list[SubTask] = []
    selected = False
    for task in pending_tasks:
        if not selected and int(task.subtask_id) == int(current.subtask_id):
            selected = True
            continue
        remaining.append(task)
    return current, remaining
