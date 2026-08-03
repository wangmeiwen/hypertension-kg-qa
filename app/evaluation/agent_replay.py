"""冻结 Agent 响应快照的确定性回放入口。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.evaluation.agent_canary import AgentCanaryDataset
from app.evaluation.agent_metrics import evaluate_agent_case, summarize_agent_results


class AgentReplayManifest(BaseModel):
    """一次回放所依赖的数据、图谱、模型、Prompt 与配置身份。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["agent_replay.v1"] = "agent_replay.v1"
    replay_id: str = Field(min_length=1)
    replay_kind: Literal["contract_fixture", "frozen_environment"]
    canary_dataset_id: str = Field(min_length=1)
    document_snapshot: str = Field(min_length=1)
    document_page_count: int = Field(ge=1)
    neo4j_snapshot: str = Field(min_length=1)
    milvus_snapshot: str = Field(min_length=1)
    model_snapshot: str = Field(min_length=1)
    prompt_snapshot: str = Field(min_length=1)
    config_snapshot: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_frozen_environment(self) -> "AgentReplayManifest":
        if self.replay_kind != "frozen_environment":
            return self
        identities = (
            self.document_snapshot,
            self.neo4j_snapshot,
            self.milvus_snapshot,
            self.model_snapshot,
            self.prompt_snapshot,
            self.config_snapshot,
        )
        if any("pending" in item.casefold() for item in identities):
            raise ValueError("frozen_environment_contains_pending_snapshot")
        return self


class AgentReplayRecord(BaseModel):
    """单个 canary 用例的冻结 API 响应。"""

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1)
    response: dict[str, Any]


class AgentReplayDataset(BaseModel):
    """可重复执行的 Agent 响应快照。"""

    model_config = ConfigDict(extra="forbid")

    manifest: AgentReplayManifest
    records: list[AgentReplayRecord] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_records(self) -> "AgentReplayDataset":
        case_ids = [item.case_id for item in self.records]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("duplicate_agent_replay_case_id")
        return self


def load_agent_replay(path: Path) -> AgentReplayDataset:
    """加载并验证冻结响应快照。"""
    return AgentReplayDataset.model_validate(
        json.loads(path.read_text(encoding="utf-8"))
    )


def evaluate_agent_replay(
    *, canary: AgentCanaryDataset, replay: AgentReplayDataset
) -> dict[str, Any]:
    """使用外部 canary 标签重放冻结响应，不读取响应自产通过标记。"""
    if replay.manifest.canary_dataset_id != canary.dataset_id:
        raise ValueError("replay_canary_dataset_mismatch")
    case_by_id = {item.case_id: item for item in canary.cases}
    unknown_case_ids = [
        item.case_id for item in replay.records if item.case_id not in case_by_id
    ]
    if unknown_case_ids:
        raise ValueError(f"replay_contains_unknown_cases:{','.join(unknown_case_ids)}")
    results = [
        evaluate_agent_case(
            case_by_id[item.case_id].to_evaluation_case(), item.response
        )
        for item in replay.records
    ]
    report = summarize_agent_results(results)
    report["evaluation_layer"] = (
        "frozen_environment_replay"
        if replay.manifest.replay_kind == "frozen_environment"
        else "synthetic_contract_replay"
    )
    report["replay_manifest"] = replay.manifest.model_dump()
    report["domain_validation_complete"] = (
        replay.manifest.replay_kind == "frozen_environment"
        and report["quality_scope"] == "domain_validated"
    )
    return report
