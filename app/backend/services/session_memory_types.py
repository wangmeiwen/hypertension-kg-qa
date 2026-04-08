"""会话记忆运行时类型定义。"""

from __future__ import annotations

from typing import TypedDict


class QAMemoryEntry(TypedDict):
    """单轮完整问答对。"""

    user_question: str
    assistant_answer: str
    user_message_id: int
    assistant_message_id: int
    created_at: str


class QAMemorySnapshot(TypedDict):
    """问答记忆快照。"""

    version: int
    last_message_id: int
    qa_pairs: list[QAMemoryEntry]


class RetrievalRecord(TypedDict):
    """单条有效检索记录。"""

    agent: str
    query: str
    result_content: str
    captured_at: str
    citations: list[str]


class RetrievalHistoryEntry(TypedDict):
    """按用户问题分组的一组检索记录。"""

    history_id: str
    user_question: str
    created_at: str
    agent_types: list[str]
    records: list[RetrievalRecord]


class RetrievalMemorySnapshot(TypedDict):
    """检索记忆快照。"""

    version: int
    last_message_id: int
    questions: list[RetrievalHistoryEntry]


class SessionMemorySnapshot(TypedDict):
    """整体会话记忆快照。"""

    qa_memory: QAMemorySnapshot
    retrieval_memory: RetrievalMemorySnapshot
    updated_at: str


class MemoryContext(TypedDict, total=False):
    """主控运行时可见的记忆上下文。"""

    qa_pairs: list[QAMemoryEntry]
    retrieval_question_index: list[dict[str, object]]
    qa_memory_text: str
    retrieval_question_index_text: str


class RuntimeMemoryPayload(TypedDict):
    """主流程运行时传递的记忆载荷。"""

    original_input_text: str
    memory_context: MemoryContext
