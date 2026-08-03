"""A2A 多 Agent 共享模型定义。"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

AgentType = Literal["kg", "rag"]


class Evidence(BaseModel):
    """跨 KG、RAG 和历史检索共享的证据契约。"""

    model_config = ConfigDict(extra="forbid")

    source_type: str = Field(default="")
    source_id: str = Field(default="")
    document_id: str = Field(default="")
    chunk_id: str = Field(default="")
    page: Optional[int] = Field(default=None)
    section: str = Field(default="")
    content: str = Field(default="")
    score: Optional[float] = Field(default=None)
    retrieval_method: str = Field(default="")
    provenance: dict[str, Any] = Field(default_factory=dict)


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


class MustCoverItem(BaseModel):
    """回答前必须取得证据的单个问题点。"""

    model_config = ConfigDict(extra="forbid")

    item_id: str
    question: str
    claim_type: str = Field(default="fact")
    required_evidence_types: list[AgentType] = Field(default_factory=list)
    dependency_on: list[str] = Field(default_factory=list)
    applicability_conditions: list[str] = Field(default_factory=list)
    status: Literal["pending", "covered", "missing"] = "pending"


class RetrievalTask(BaseModel):
    """由问题计划生成的检索任务。"""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    must_cover_id: str
    target_agent: AgentType
    question: str
    dependency_on: list[str] = Field(default_factory=list)
    status: Literal["pending", "completed", "failed"] = "pending"


class QuestionPlan(BaseModel):
    """主控在执行检索前生成的结构化问题计划。"""

    model_config = ConfigDict(extra="forbid")

    plan_id: str
    original_question: str
    intent: str = Field(default="medical_qa")
    medical_risk_level: Literal["low", "high", "emergency"] = "low"
    must_cover: list[MustCoverItem] = Field(default_factory=list)
    retrieval_tasks: list[RetrievalTask] = Field(default_factory=list)
    answer_constraints: list[str] = Field(default_factory=list)


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
    evidence: list[Evidence] = Field(default_factory=list)
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


class EvidenceConflict(BaseModel):
    """融合证据中尚未解决的结构化冲突。"""

    model_config = ConfigDict(extra="forbid")

    conflict_id: str
    conflict_type: Literal["numeric"] = "numeric"
    claim_key: str
    values: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)
    status: Literal["unresolved", "resolved"] = "unresolved"


class EvidenceBundle(BaseModel):
    """主控终止时输出的统一证据包。"""

    bundle_id: str = Field(default="")
    plan_id: str = Field(default="")
    question: str = Field(default="")
    evidence: list[Evidence] = Field(default_factory=list)
    kg_evidence: list[Evidence] = Field(default_factory=list)
    rag_evidence: list[Evidence] = Field(default_factory=list)
    history_evidence: list[Evidence] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)
    conflicts: list[EvidenceConflict] = Field(default_factory=list)
    coverage: dict[str, Any] = Field(default_factory=dict)
    distinct_source_count: int = Field(default=0, ge=0)


class OrchestratorDecision(BaseModel):
    """主代理补检索决策。"""

    need_supplement: bool = Field(default=False)
    supplement_type: str = Field(default="")
    target_subtask_id: int = Field(default=0)
    next_agent: Optional[AgentType] = Field(default=None)
    next_question: str = Field(default="")
    query_hint: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(default="")


class OrchestratorAction(BaseModel):
    """主控模型单轮动作。"""

    model_config = ConfigDict(extra="forbid")

    action: Literal["final_answer", "call_kg", "call_rag", "call_history_read"]
    reason: str = Field(default="", max_length=400)
    question: str = Field(default="", max_length=1000)
    history_id: str = Field(default="", max_length=128)
    user_question: str = Field(default="", max_length=1000)
    answer: str = Field(default="", max_length=8000)

    @model_validator(mode="after")
    def validate_action_payload(self) -> "OrchestratorAction":
        """校验不同动作所需的字段。"""
        if self.action == "final_answer" and not self.answer.strip():
            raise ValueError("final_answer_requires_answer")
        if (
            self.action == "call_history_read"
            and not self.history_id.strip()
            and not self.user_question.strip()
        ):
            raise ValueError("history_read_requires_locator")
        return self


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
