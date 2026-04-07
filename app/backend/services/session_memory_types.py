"""会话记忆运行时类型定义。"""

from __future__ import annotations

from typing import Any, TypedDict


class SessionSummary(TypedDict):
    """定义会话长期摘要的结构化字段。"""

    version: int
    last_message_id: int
    conversation_goal: str
    confirmed_facts: list[str]
    working_assumptions: list[str]
    answered_points: list[str]
    open_questions: list[str]
    risk_notes: list[str]


class RetrievalSummary(TypedDict):
    """定义检索侧记忆摘要的结构化字段。"""

    version: int
    last_message_id: int
    focus_entities: list[str]
    covered_topics: list[str]
    evidence_gaps: list[str]
    recommended_queries: list[str]
    retrieval_risk_flags: list[str]


class SessionMemorySnapshot(TypedDict):
    """定义会话记忆快照的整体载荷结构。"""

    summary: SessionSummary
    retrieval_summary: RetrievalSummary
    updated_at: str


class ResolvedQuery(TypedDict):
    """定义问题改写后供编排层消费的查询结构。"""

    standalone_question: str
    intent: str
    question_mode: str
    subqueries: list[str]
    must_cover: list[str]
    entities: list[str]
    filters: list[str]
    preferred_sources: list[str]
    carry_over_context: list[str]


class TurnMemoryProjection(TypedDict):
    """定义单轮对话投影到记忆压缩前的结构化信息。"""

    answered_points: list[str]
    evidence_gaps: list[str]
    focus_entities: list[str]
    covered_topics: list[str]
    risk_notes: list[str]
    assistant_reply_digest: str
    question_scope: str


class TopicShiftSignal(TypedDict):
    """定义主题漂移检测信号与判定依据。"""

    topic_shift_detected: bool
    reason: str
    overlap_score: float
    question_scope: str
    history_scope: str
    previous_goal: str


class MemoryContext(TypedDict):
    """定义运行时注入编排层的会话记忆上下文。"""

    summary: SessionSummary
    retrieval_summary: RetrievalSummary
    last_message_id: int
    resolved_from_history: bool
    recent_messages_digest: list[str]
    topic_shift_signal: TopicShiftSignal
    metadata: dict[str, Any]


class RuntimeMemoryPayload(TypedDict):
    """定义主流程运行时传递的记忆与改写结果载荷。"""

    original_input_text: str
    resolved_query: ResolvedQuery
    memory_context: MemoryContext
