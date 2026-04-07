"""state 模块。

提供 state 相关能力与对外接口。
"""


from __future__ import annotations

from typing import Any, TypedDict


class OrchestratorState(TypedDict, total=False):
    """定义主控编排图在各节点间传递的状态载荷。"""

    input_text: str
    original_input_text: str
    resolved_query: dict[str, Any]
    memory_context: dict[str, Any]
    session_id: str
    user_id: str
    trace_id: str
    strategy: str
    max_turns: int
    supplement_max_rounds: int
    subagent_max_tool_calls: int
    retrieval_round: int
    pending_subtasks: list[dict[str, Any]]
    current_subtask: dict[str, Any]
    evidence_bundle: dict[str, Any]
    evidence_store: dict[str, Any]
    tool_observations: list[dict[str, Any]]
    reflection_events: list[dict[str, Any]]
    processing_steps: list[str]
    last_subagent_result: dict[str, Any]
    subtask_results: dict[str, dict[str, Any]]
    subtask_status: dict[str, str]
    coverage_map: dict[str, dict[str, Any]]
    attempt_counters: dict[str, int]
    agent_history: list[dict[str, Any]]
    query_hints: dict[str, dict[str, Any]]
    failed_routes: list[dict[str, Any]]
    no_signal_streak: int
    parallel_groups: list[list[int]]
    dependency_graph: dict[str, list[int]]
    answerability: dict[str, Any]
    gate_result: dict[str, Any]
    evidence_gaps: list[str]
    classification: dict[str, Any]
    subtask_decisions: dict[str, dict[str, Any]]
    response_mode: str
    direct_reason: str
    retrieval_reason: str
    answer_source: str
    memory_supported_points: list[str]
    memory_missing_points: list[str]
    memory_coverage_ratio: float
    memory_route_reason: str
    final_output: dict[str, Any]
    serial_guard: Any
