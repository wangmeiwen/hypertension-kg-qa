"""Agent canary 数据集契约与加载入口。"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CanaryCategory = Literal[
    "single_fact",
    "numeric_applicability",
    "multi_point",
    "multihop_relation",
    "safety_abstention",
]
ExpectedBehavior = Literal["answer", "abstain", "urgent_redirect"]
ReviewStatus = Literal["engineering_baseline", "domain_reviewed"]


class CanaryMustCover(BaseModel):
    """单个 canary 问题点的外部预期。"""

    model_config = ConfigDict(extra="forbid")

    item_id: str = Field(min_length=1)
    claim: str = Field(min_length=1)
    expected_term_groups: list[list[str]] = Field(default_factory=list)
    minimum_matched_groups: int = Field(default=0, ge=0)
    applicability_conditions: list[str] = Field(default_factory=list)
    expected_evidence_ids: list[str] = Field(default_factory=list)
    expected_conflict_types: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_group_requirement(self) -> "CanaryMustCover":
        """确保词组阈值不会超过已声明词组。"""
        if self.minimum_matched_groups > len(self.expected_term_groups):
            raise ValueError("minimum_matched_groups_exceeds_groups")
        return self


class DomainReviewMetadata(BaseModel):
    """领域复核记录，不包含评测系统自产指标。"""

    model_config = ConfigDict(extra="forbid")

    review_id: str = Field(min_length=1)
    reviewer_role: str = Field(min_length=1)
    reviewed_at: str = Field(min_length=1)
    guideline_snapshot_id: str = Field(min_length=1)


class AgentCanaryCase(BaseModel):
    """单个 Agent canary 用例。"""

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1)
    category: CanaryCategory
    question: str = Field(min_length=1)
    must_cover: list[CanaryMustCover] = Field(min_length=1)
    expected_behavior: ExpectedBehavior = "answer"
    expected_risk_level: Literal["", "low", "high", "emergency"] = ""
    required_retrievers: list[Literal["kg", "rag"]] = Field(default_factory=list)
    requires_multihop: bool = False
    review_status: ReviewStatus = "engineering_baseline"
    domain_review: DomainReviewMetadata | None = None

    @model_validator(mode="after")
    def validate_case(self) -> "AgentCanaryCase":
        """校验问题点身份和多跳用例约束。"""
        item_ids = [item.item_id for item in self.must_cover]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("duplicate_must_cover_id")
        if self.category == "multihop_relation" and not self.requires_multihop:
            raise ValueError("multihop_category_requires_multihop")
        if self.review_status == "domain_reviewed":
            if self.domain_review is None:
                raise ValueError("domain_review_metadata_required")
            if self.expected_behavior == "answer" and any(
                not item.expected_evidence_ids for item in self.must_cover
            ):
                raise ValueError("domain_reviewed_answer_requires_evidence_labels")
        return self

    def to_evaluation_case(self) -> dict[str, object]:
        """转换为确定性评测函数使用的扁平输入。"""
        groups = [
            group
            for item in self.must_cover
            for group in item.expected_term_groups
            if group
        ]
        minimum_groups = sum(item.minimum_matched_groups for item in self.must_cover)
        evidence_ids = [
            evidence_id
            for item in self.must_cover
            for evidence_id in item.expected_evidence_ids
        ]
        return {
            "id": self.case_id,
            "question": self.question,
            "expected_term_groups": groups,
            "min_expected_groups": minimum_groups,
            "expected_evidence_ids": evidence_ids,
            "must_cover_expectations": [item.model_dump() for item in self.must_cover],
            "expected_behavior": self.expected_behavior,
            "expected_risk_level": self.expected_risk_level,
            "requires_multihop": self.requires_multihop,
            "review_status": self.review_status,
            "category": self.category,
            "domain_review": (
                self.domain_review.model_dump()
                if self.domain_review is not None
                else None
            ),
        }


class CanarySourceSnapshot(BaseModel):
    """canary 对应的冻结数据边界。"""

    model_config = ConfigDict(extra="forbid")

    document_label: str = Field(min_length=1)
    page_count: int = Field(ge=1)
    neo4j_snapshot: str = Field(min_length=1)
    milvus_snapshot: str = Field(min_length=1)
    model_snapshot: str = Field(min_length=1)


class AgentCanaryDataset(BaseModel):
    """版本化 Agent canary 数据集。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["agent_canary.v1"]
    dataset_id: str = Field(min_length=1)
    review_status: ReviewStatus = "engineering_baseline"
    source_snapshot: CanarySourceSnapshot
    category_targets: dict[CanaryCategory, int]
    cases: list[AgentCanaryCase] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_dataset(self) -> "AgentCanaryDataset":
        """校验用例身份、类别分布和复核状态。"""
        case_ids = [item.case_id for item in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("duplicate_canary_case_id")
        expected = {key: value for key, value in self.category_targets.items()}
        actual = dict(Counter(item.category for item in self.cases))
        if actual != expected:
            raise ValueError(f"canary_category_distribution_mismatch:{actual}")
        if self.review_status == "domain_reviewed" and any(
            item.review_status != "domain_reviewed" for item in self.cases
        ):
            raise ValueError("domain_reviewed_dataset_contains_unreviewed_case")
        return self

    def evaluation_cases(self) -> list[dict[str, object]]:
        """返回评测入口使用的外部预期用例。"""
        return [item.to_evaluation_case() for item in self.cases]


def load_agent_canary(path: Path) -> AgentCanaryDataset:
    """从 JSON 文件加载并验证 Agent canary。"""
    payload = json.loads(path.read_text(encoding="utf-8"))
    return AgentCanaryDataset.model_validate(payload)
