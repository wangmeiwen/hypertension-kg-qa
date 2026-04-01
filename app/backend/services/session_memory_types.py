"""会话记忆运行时类型定义。"""

from __future__ import annotations

from typing import TypedDict


class SessionSummary(TypedDict):
    version: int
    last_message_id: int
    conversation_goal: str
    confirmed_facts: list[str]
    working_assumptions: list[str]
    answered_points: list[str]
    open_questions: list[str]
    risk_notes: list[str]


class RetrievalSummary(TypedDict):
    version: int
    last_message_id: int
    focus_entities: list[str]
    covered_topics: list[str]
    evidence_gaps: list[str]
    recommended_queries: list[str]
    retrieval_risk_flags: list[str]


class SessionMemorySnapshot(TypedDict):
    summary: SessionSummary
    retrieval_summary: RetrievalSummary
    updated_at: str


class ResolvedQuery(TypedDict):
    standalone_question: str
    intent: str
    subqueries: list[str]
    must_cover: list[str]
    entities: list[str]
    filters: list[str]
    preferred_sources: list[str]
    carry_over_context: list[str]


class MemoryContext(TypedDict):
    summary: SessionSummary
    retrieval_summary: RetrievalSummary
    last_message_id: int
    resolved_from_history: bool
    recent_messages_digest: list[str]


class RuntimeMemoryPayload(TypedDict):
    original_input_text: str
    resolved_query: ResolvedQuery
    memory_context: MemoryContext
