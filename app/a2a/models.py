"""A2A 多 Agent 共享模型定义。"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


AgentType = Literal["kg", "rag"]


class UserQuestion(BaseModel):
    """主代理统一入口问题模型。"""

    question: str = Field(default="", description="用户问题文本")
    session_id: str = Field(default="", description="会话 ID")
    user_id: str = Field(default="", description="用户 ID")
    trace_id: str = Field(default="", description="追踪 ID")
    metadata: dict[str, Any] = Field(default_factory=dict)


class SubAgentConstraints(BaseModel):
    """子代理执行约束。"""

    allow_extra_retrieval: bool = Field(default=False)
    max_tool_calls: int = Field(default=3, ge=1, le=3)


class SubTask(BaseModel):
    """主代理派发给子代理的任务单元。"""

    subtask_id: int = Field(default=1)
    target_agent: AgentType = Field(default="kg")
    question: str = Field(default="")
    intent: str = Field(default="")
    entities: list[str] = Field(default_factory=list)
    dependency_on: list[int] = Field(default_factory=list)
    evidence_type_required: list[str] = Field(default_factory=list)
    must_cover: list[str] = Field(default_factory=list)
    priority: int = Field(default=50)
    attempt_count: int = Field(default=0)
    coverage_status: str = Field(default="unknown")
    query_focus: str = Field(default="")
    query_terms: list[str] = Field(default_factory=list)
    query_directions: list[str] = Field(default_factory=list)
    retry_context: dict[str, Any] = Field(default_factory=dict)
    agent_role: str = Field(default="")
    constraints: SubAgentConstraints = Field(default_factory=SubAgentConstraints)
    tool_name: str = Field(default="")
    arguments: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(default="")


class SubAgentResult(BaseModel):
    """子代理标准输出。"""

    subtask_id: int = Field(default=1)
    target_agent: AgentType = Field(default="kg")
    question: str = Field(default="")
    query_statements: list[dict[str, Any]] = Field(default_factory=list)
    queries_used: list[str] = Field(default_factory=list)
    query_records: list[dict[str, Any]] = Field(default_factory=list)
    tool_results: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    signal: str = Field(default="none")
    coverage: dict[str, Any] = Field(default_factory=dict)
    missing_points: list[str] = Field(default_factory=list)
    direct_support: bool = Field(default=False)
    suggested_next_route: str = Field(default="")
    no_signal: bool = Field(default=False)
    reason: str = Field(default="")
    token_usage: dict[str, int] = Field(
        default_factory=lambda: {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
    )
    telemetry: dict[str, Any] = Field(default_factory=dict)


class EvidenceBundle(BaseModel):
    """主代理证据池。"""

    kg_evidence: list[dict[str, Any]] = Field(default_factory=list)
    rag_evidence: list[dict[str, Any]] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)


class OrchestratorDecision(BaseModel):
    """主代理补检索决策。"""

    need_supplement: bool = Field(default=False)
    supplement_type: str = Field(default="")
    target_subtask_id: int = Field(default=0)
    next_agent: Optional[AgentType] = Field(default=None)
    next_question: str = Field(default="")
    query_hint: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(default="")


class FinalAnswer(BaseModel):
    """主代理最终结果。"""

    answer: str = Field(default="")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_summary: dict[str, Any] = Field(default_factory=dict)
    trace: list[str] = Field(default_factory=list)


class A2AProgressEvent(BaseModel):
    """A2A 任务过程事件。"""

    event: str = Field(default="")
    detail: str = Field(default="")
    metadata: dict[str, Any] = Field(default_factory=dict)


class A2ATaskMessage(BaseModel):
    """A2A message/send 输入中的 message。"""

    role: str = Field(default="user")
    parts: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    contextId: str = Field(default="")
    userId: str = Field(default="")


class A2AStreamInfo(BaseModel):
    """A2A stream 信息。"""

    url: str = Field(default="")
    cursor: int = Field(default=0)


class A2ATaskSnapshot(BaseModel):
    """A2A 任务快照。"""

    id: str = Field(default="")
    contextId: str = Field(default="")
    status: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolCallRequest(BaseModel):
    """子代理工具调用输入结构。"""

    tool_name: str = Field(default="")
    payload: dict[str, Any] = Field(default_factory=dict)


class ToolCallResult(BaseModel):
    """子代理工具调用标准化输出。"""

    ok: bool = Field(default=False)
    tool_name: str = Field(default="")
    result: dict[str, Any] = Field(default_factory=dict)
    error: str = Field(default="")
    meta: dict[str, Any] = Field(default_factory=dict)
