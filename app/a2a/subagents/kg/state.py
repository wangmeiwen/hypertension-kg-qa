"""state 模块。

提供 state 相关能力与对外接口。
"""


from __future__ import annotations

from typing import Any, TypedDict


class KgSubAgentState(TypedDict, total=False):
    input_question: str
    subtask_id: int
    constraints: dict[str, Any]
    tool_name: str
    arguments: dict[str, Any]
    query_statements: list[dict[str, Any]]
    tool_calls: list[dict[str, Any]]
    tool_results: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    no_signal: bool
    reason: str
    telemetry: dict[str, Any]
    final_result: dict[str, Any]
