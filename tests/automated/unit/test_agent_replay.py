"""Agent 分层快照回放测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.evaluation.agent_canary import AgentCanaryCase, load_agent_canary
from app.evaluation.agent_replay import (
    AgentReplayManifest,
    evaluate_agent_replay,
    load_agent_replay,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def test_contract_snapshot_replay_uses_external_expectations() -> None:
    canary = load_agent_canary(
        PROJECT_ROOT
        / "tests"
        / "ragas"
        / "data"
        / "agent_replay_contract_canary_v1.json"
    )
    replay = load_agent_replay(
        PROJECT_ROOT / "tests" / "ragas" / "data" / "agent_replay_contract_v1.json"
    )

    report = evaluate_agent_replay(canary=canary, replay=replay)

    assert report["all_passed"] is True
    assert report["evaluation_layer"] == "synthetic_contract_replay"
    assert report["quality_scope"] == "engineering_baseline"
    assert report["domain_validation_complete"] is False
    assert report["quality_metrics"]["claim_citation_alignment_rate"] == 1.0
    assert report["telemetry_metrics"]["evidence_sufficiency_rate"] == 1.0


def test_frozen_environment_rejects_pending_snapshot_identity() -> None:
    with pytest.raises(
        ValidationError, match="frozen_environment_contains_pending_snapshot"
    ):
        AgentReplayManifest(
            replay_id="replay-1",
            replay_kind="frozen_environment",
            canary_dataset_id="canary-1",
            document_snapshot="document-sha256",
            document_page_count=20,
            neo4j_snapshot="pending-freeze",
            milvus_snapshot="milvus-backup-1",
            model_snapshot="model-1",
            prompt_snapshot="prompt-1",
            config_snapshot="config-1",
        )


def test_replay_rejects_unknown_case_identity() -> None:
    canary = load_agent_canary(
        PROJECT_ROOT
        / "tests"
        / "ragas"
        / "data"
        / "agent_replay_contract_canary_v1.json"
    )
    replay = load_agent_replay(
        PROJECT_ROOT / "tests" / "ragas" / "data" / "agent_replay_contract_v1.json"
    )
    replay.records[0].case_id = "unknown-case"

    with pytest.raises(ValueError, match="replay_contains_unknown_cases"):
        evaluate_agent_replay(canary=canary, replay=replay)


def test_domain_reviewed_answer_requires_review_metadata_and_evidence_labels() -> None:
    with pytest.raises(ValidationError, match="domain_review_metadata_required"):
        AgentCanaryCase(
            case_id="reviewed-1",
            category="single_fact",
            question="问题",
            must_cover=[
                {
                    "item_id": "claim-1",
                    "claim": "结论",
                    "expected_term_groups": [["答案"]],
                    "minimum_matched_groups": 1,
                    "expected_evidence_ids": ["publication-1"],
                }
            ],
            review_status="domain_reviewed",
        )

    with pytest.raises(
        ValidationError, match="domain_reviewed_answer_requires_evidence_labels"
    ):
        AgentCanaryCase(
            case_id="reviewed-2",
            category="single_fact",
            question="问题",
            must_cover=[
                {
                    "item_id": "claim-1",
                    "claim": "结论",
                    "expected_term_groups": [["答案"]],
                    "minimum_matched_groups": 1,
                }
            ],
            review_status="domain_reviewed",
            domain_review={
                "review_id": "review-1",
                "reviewer_role": "hypertension_specialist",
                "reviewed_at": "2026-08-03",
                "guideline_snapshot_id": "guideline-sha256",
            },
        )
