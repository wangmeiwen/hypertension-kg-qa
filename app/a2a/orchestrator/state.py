"""主代理状态模型。"""

from __future__ import annotations

from typing import Any, TypedDict


class OrchestratorState(TypedDict, total=False):
    input_text: str
    session_id: str
    user_id: str
    trace_id: str
    strategy: str
    max_turns: int
    retrieval_round: int
    pending_subtasks: list[dict[str, Any]]
    current_subtask: dict[str, Any]
    evidence_bundle: dict[str, Any]
    tool_observations: list[dict[str, Any]]
    reflection_events: list[dict[str, Any]]
    processing_steps: list[str]
    last_subagent_result: dict[str, Any]
    decision: dict[str, Any]
    final_output: dict[str, Any]
    serial_guard: Any
