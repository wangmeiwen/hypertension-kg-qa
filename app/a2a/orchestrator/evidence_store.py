"""任务绑定证据归档模块。"""

from __future__ import annotations

from typing import Any


def _init_store(store: dict[str, Any] | None = None) -> dict[str, Any]:
    """执行init_store流程并返回结果。
    
    Args:
        store: 函数输入参数。
    
    Returns:
        dict[str, Any]: 结构化结果数据。
    """
    if isinstance(store, dict):
        base = dict(store)
    else:
        base = {}
    if not isinstance(base.get("global"), dict):
        base["global"] = {
            "direct": [],
            "indirect": [],
            "conflicting": [],
            "rejected": [],
        }
    if not isinstance(base.get("by_subtask"), dict):
        base["by_subtask"] = {}
    return base


def merge_task_evidence(
    store: dict[str, Any] | None,
    *,
    subtask_id: int,
    agent_type: str,
    evidence_rows: list[dict[str, Any]],
    coverage: dict[str, Any],
) -> dict[str, Any]:
    """合并task_evidence相关信息并返回结果。
    
    Args:
        store: 函数输入参数。
        subtask_id: 对象标识。
        agent_type: 函数输入参数。
        evidence_rows: 函数输入参数。
        coverage: 函数输入参数。
    
    Returns:
        dict[str, Any]: 结构化结果数据。
    """
    state = _init_store(store)
    task_key = str(subtask_id)

    by_subtask = state["by_subtask"]
    if not isinstance(by_subtask.get(task_key), dict):
        by_subtask[task_key] = {
            "direct": [],
            "indirect": [],
            "conflicting": [],
            "rejected": [],
            "agent_type": str(agent_type or ""),
        }

    task_bucket = by_subtask[task_key]
    direct_support = bool((coverage or {}).get("direct_support"))
    coverage_status = str((coverage or {}).get("coverage_status") or "")

    for row in evidence_rows:
        if not isinstance(row, dict):
            continue
        record = {
            "subtask_id": int(subtask_id),
            "agent_type": str(agent_type or ""),
            "evidence": row,
            "coverage_status": coverage_status,
            "direct_support": direct_support,
        }
        if coverage_status == "none":
            task_bucket["rejected"].append(record)
            state["global"]["rejected"].append(record)
        elif direct_support:
            task_bucket["direct"].append(record)
            state["global"]["direct"].append(record)
        else:
            task_bucket["indirect"].append(record)
            state["global"]["indirect"].append(record)

    return state
