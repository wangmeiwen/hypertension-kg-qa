"""A2A 多 Agent 共享模型定义。"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

AgentType = Literal["kg", "rag"]
ClaimSupportStatus = Literal[
    "supported",
    "partially_supported",
    "contradicted",
    "irrelevant",
    "uncertain",
]
ConflictPolicy = Literal["block_unresolved", "allow_with_disclosure"]


def _default_required_support_statuses() -> list[ClaimSupportStatus]:
    return ["supported"]


class MinimumEvidencePolicy(BaseModel):
    """单个 must-cover 的最低证据要求。"""

    model_config = ConfigDict(extra="forbid")

    policy_version: Literal["minimum_evidence.v1"] = "minimum_evidence.v1"
    minimum_supported_evidence: int = Field(default=1, ge=1, le=5)
    minimum_distinct_publications: int = Field(default=1, ge=1, le=3)
    required_support_statuses: list[ClaimSupportStatus] = Field(
        default_factory=_default_required_support_statuses
    )
    required_applicability_fields: list[str] = Field(default_factory=list)
    conflict_policy: ConflictPolicy = "block_unresolved"
    required_retrievers: list[AgentType] = Field(default_factory=list)
    optional_retrievers: list[AgentType] = Field(default_factory=list)


class Evidence(BaseModel):
    """跨 KG、RAG 和历史检索共享的证据契约。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["evidence.v2"] = "evidence.v2"
    source_type: str = Field(default="")
    source_id: str = Field(default="")
    document_id: str = Field(default="")
    chunk_id: str = Field(default="")
    page: Optional[int] = Field(default=None)
    origin_guideline_id: str = Field(default="")
    origin_document_id: str = Field(default="")
    source_hash: str = Field(default="")
    source_page: Optional[int] = Field(default=None)
    source_chunk_id: str = Field(default="")
    origin_status: Literal["known", "unknown_origin"] = "unknown_origin"
    section: str = Field(default="")
    content: str = Field(default="")
    score: Optional[float] = Field(default=None)
    retrieval_method: str = Field(default="")
    provenance: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def normalize_origin_status(self) -> "Evidence":
        """按可信出版来源字段确定来源状态并补齐定位字段。"""
        if self.source_page is None:
            self.source_page = self.page
        if not self.source_chunk_id:
            self.source_chunk_id = self.chunk_id
        self.origin_status = (
            "known"
            if any(
                value.strip()
                for value in (
                    self.origin_guideline_id,
                    self.origin_document_id,
                    self.source_hash,
                )
            )
            else "unknown_origin"
        )
        return self


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
    evidence_policy: MinimumEvidencePolicy = Field(
        default_factory=MinimumEvidencePolicy
    )
    dependency_on: list[str] = Field(default_factory=list)
    applicability_conditions: list[str] = Field(default_factory=list)
    support_status: ClaimSupportStatus = "uncertain"
    status: Literal["pending", "covered", "missing"] = "pending"


class ClaimEvidenceAssessment(BaseModel):
    """一条 Evidence 对目标 claim 的支持判定。"""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    must_cover_id: str
    evidence_id: str
    support_status: ClaimSupportStatus
    support_excerpt: str = Field(default="", max_length=2000)
    applicability_conditions: list[str] = Field(default_factory=list)
    reason: str = Field(default="", max_length=1000)
    verifier_version: str = Field(default="claim_support.v1", max_length=64)


class RetrievalTask(BaseModel):
    """由问题计划生成的检索任务。"""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    must_cover_id: str
    target_agent: AgentType
    retriever_requirement: Literal["required", "optional"] = "optional"
    question: str
    dependency_on: list[str] = Field(default_factory=list)
    status: Literal["pending", "completed", "failed", "needs_supplement"] = "pending"


class QuestionPlan(BaseModel):
    """主控在执行检索前生成的结构化问题计划。"""

    model_config = ConfigDict(extra="forbid")

    plan_id: str
    original_question: str
    intent: str = Field(default="medical_qa")
    medical_risk_level: Literal["low", "high", "emergency"] = "low"
    must_cover: list[MustCoverItem] = Field(default_factory=list)
    retrieval_tasks: list[RetrievalTask] = Field(default_factory=list)
    claim_evidence_assessments: list[ClaimEvidenceAssessment] = Field(
        default_factory=list
    )
    answer_constraints: list[str] = Field(default_factory=list)


class MustCoverSufficiency(BaseModel):
    """单个 must-cover 的证据充分性结果。"""

    model_config = ConfigDict(extra="forbid")

    item_id: str
    claim_type: str
    support_status: ClaimSupportStatus = "uncertain"
    sufficient: bool
    evidence_count: int = Field(default=0, ge=0)
    supported_evidence_count: int = Field(default=0, ge=0)
    distinct_publication_count: int = Field(default=0, ge=0)
    required_publication_count: int = Field(default=1, ge=1)
    available_evidence_types: list[AgentType] = Field(default_factory=list)
    applicability_complete: bool = True
    unresolved_conflict_count: int = Field(default=0, ge=0)
    policy: MinimumEvidencePolicy = Field(default_factory=MinimumEvidencePolicy)
    missing_reasons: list[str] = Field(default_factory=list)


class EvidenceSufficiencyAssessment(BaseModel):
    """整份回答的证据充分性结果。"""

    model_config = ConfigDict(extra="forbid")

    sufficient: bool
    sufficiency_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    items: list[MustCoverSufficiency] = Field(default_factory=list)


class MustCoverProgressState(BaseModel):
    """单个 must-cover 的语义检索进展快照。"""

    model_config = ConfigDict(extra="forbid")

    item_id: str
    support_status: ClaimSupportStatus
    supported_evidence_count: int = Field(default=0, ge=0)
    distinct_publication_count: int = Field(default=0, ge=0)
    applicability_completeness: float = Field(default=0.0, ge=0.0, le=1.0)
    conflict_status: Literal["none", "unresolved"] = "none"
    best_support_score: float = Field(default=0.0, ge=0.0, le=1.0)


class SubTask(BaseModel):
    """主代理派发给子代理的任务单元。"""

    task_id: str
    must_cover_id: str
    subtask_id: int
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

    task_id: str = Field(default="")
    must_cover_id: str = Field(default="")
    subtask_id: int = Field(default=1)
    target_agent: AgentType = Field(default="kg")
    question: str = Field(default="")
    query_statements: list[dict[str, Any]] = Field(default_factory=list)
    queries_used: list[str] = Field(default_factory=list)
    query_records: list[dict[str, Any]] = Field(default_factory=list)
    tool_results: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    claim_assessments: list[ClaimEvidenceAssessment] = Field(default_factory=list)
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


class AnswerEvidenceContext(BaseModel):
    """最终答案中一条已校验的支持证据。"""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    must_cover_id: str
    evidence_id: str
    citation: str
    publication_id: str = ""
    source_type: str = ""
    support_status: ClaimSupportStatus
    support_excerpt: str = Field(max_length=2000)
    applicability_conditions: list[str] = Field(default_factory=list)


class AnswerMustCoverContext(BaseModel):
    """按问题点组织的最终答案约束。"""

    model_config = ConfigDict(extra="forbid")

    item_id: str
    question: str
    claim_type: str
    support_status: ClaimSupportStatus
    evidence_policy: MinimumEvidencePolicy
    applicability_conditions: list[str] = Field(default_factory=list)
    supporting_evidence: list[AnswerEvidenceContext] = Field(default_factory=list)


class AnswerGenerationContext(BaseModel):
    """最终回答生成器唯一可使用的结构化证据上下文。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["answer_context.v1"] = "answer_context.v1"
    question: str
    plan_id: str
    must_cover: list[AnswerMustCoverContext] = Field(default_factory=list)
    conflicts: list[EvidenceConflict] = Field(default_factory=list)
    answer_constraints: list[str] = Field(default_factory=list)


class AnswerValidationResult(BaseModel):
    """终答与结构化证据上下文的一致性校验结果。"""

    model_config = ConfigDict(extra="forbid")

    valid: bool
    covered_must_cover_ids: list[str] = Field(default_factory=list)
    missing_must_cover_ids: list[str] = Field(default_factory=list)
    missing_citation_ids: list[str] = Field(default_factory=list)
    missing_applicability: dict[str, list[str]] = Field(default_factory=dict)
    unresolved_conflict_ids: list[str] = Field(default_factory=list)


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
    sufficiency: dict[str, Any] = Field(default_factory=dict)
    distinct_publication_count: int = Field(default=0, ge=0)


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
    task_id: str = Field(default="", max_length=64)
    must_cover_id: str = Field(default="", max_length=64)
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
        if self.action in {"call_kg", "call_rag"}:
            if not self.task_id.strip():
                raise ValueError("retrieval_action_requires_task_id")
            if not self.must_cover_id.strip():
                raise ValueError("retrieval_action_requires_must_cover_id")
        if (
            self.action == "call_history_read"
            and not self.history_id.strip()
            and not self.user_question.strip()
        ):
            raise ValueError("history_read_requires_locator")
        if self.action == "call_history_read" and not self.task_id.strip():
            raise ValueError("history_read_requires_task_id")
        if self.action == "call_history_read" and not self.must_cover_id.strip():
            raise ValueError("history_read_requires_must_cover_id")
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
