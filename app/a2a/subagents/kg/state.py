"""state 模块。

提供知识图谱子代理 LangGraph 节点之间传递的状态类型定义。
"""


from __future__ import annotations

from typing import Any, TypedDict


class KgSubAgentState(TypedDict, total=False):
    """定义 KG 子代理执行链路的状态数据结构。"""

    input_question: str
    subtask_id: int
    constraints: dict[str, Any]
    tool_name: str
    arguments: dict[str, Any]
    query_terms: list[str]
    query_statements: list[dict[str, Any]]
    tool_calls: list[dict[str, Any]]
    tool_results: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    no_signal: bool
    reason: str
    telemetry: dict[str, Any]
    final_result: dict[str, Any]
