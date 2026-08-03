"""Agent 离线评测指标测试。"""

from __future__ import annotations

from app.evaluation.agent_metrics import evaluate_agent_case, summarize_agent_results


def _successful_response() -> dict[str, object]:
    return {
        "ok": True,
        "answer": "动态血压监测由中国动态血压监测指南支持。[guide]",
        "output_type": "orchestrator_final",
        "tool_observations": [
            {
                "agent": "kg",
                "evidence": [
                    {
                        "provenance": {
                            "raw": {
                                "source_name": "高血压",
                                "target_name": "动态血压监测",
                                "graph_hop": 1,
                            }
                        }
                    },
                    {
                        "provenance": {
                            "raw": {
                                "source_name": "动态血压监测",
                                "target_name": "指南",
                                "graph_hop": 2,
                            }
                        }
                    },
                ],
            }
        ],
        "evidence_bundle": {
            "coverage": {"complete": True, "coverage_ratio": 1.0},
            "sufficiency": {"sufficient": True},
            "conflicts": [],
            "citations": ["[guide]"],
        },
        "telemetry": {
            "retrieval_round": 2,
            "medical_risk": {"risk_level": "low"},
        },
    }


def test_agent_case_requires_answer_multihop_coverage_and_sufficiency() -> None:
    case = {
        "id": "case-1",
        "question": "哪种方法由哪份指南支持？",
        "expected_term_groups": [
            ["ABPM", "动态血压监测"],
            ["中国动态血压监测指南"],
        ],
        "min_expected_groups": 2,
        "requires_multihop": True,
        "expected_risk_level": "low",
    }

    result = evaluate_agent_case(case, _successful_response())

    assert result["passed"] is True
    assert result["answer_term_coverage"] == 1.0
    assert result["has_multihop_evidence"] is True
    assert result["plan_complete"] is True
    assert result["evidence_sufficient"] is True
    assert result["citation_coverage"] == 1.0


def test_unresolved_conflict_blocks_normal_answer() -> None:
    response = _successful_response()
    response["evidence_bundle"] = {
        "coverage": {"complete": True, "coverage_ratio": 1.0},
        "sufficiency": {"sufficient": True},
        "conflicts": [{"conflict_id": "conflict-1"}],
    }
    case = {
        "id": "case-2",
        "question": "风险下降多少？",
        "expected_terms": ["动态血压监测"],
        "min_expected_matches": 1,
    }

    result = evaluate_agent_case(case, response)

    assert result["passed"] is False
    assert result["conflict_safe"] is False


def test_expected_abstention_is_scored_without_requiring_answer_evidence() -> None:
    case = {
        "id": "case-3",
        "question": "证据冲突时是否回答？",
        "expect_abstention": True,
    }
    response = {
        "ok": True,
        "answer": "当前证据冲突，无法可靠回答。",
        "output_type": "orchestrator_abstained_insufficient_evidence",
        "evidence_bundle": {
            "coverage": {"complete": False, "coverage_ratio": 0.5},
            "sufficiency": {"sufficient": False},
            "conflicts": [{"conflict_id": "conflict-1"}],
        },
    }

    result = evaluate_agent_case(case, response)

    assert result["passed"] is True
    assert result["abstention_correct"] is True
    assert result["conflict_safe"] is True


def test_agent_result_summary_reports_safety_quality_and_efficiency() -> None:
    success = evaluate_agent_case(
        {
            "id": "case-1",
            "question": "问题",
            "expected_terms": ["动态血压监测"],
            "min_expected_matches": 1,
            "requires_multihop": True,
        },
        _successful_response(),
    )
    abstention = evaluate_agent_case(
        {"id": "case-2", "question": "问题", "expect_abstention": True},
        {
            "ok": True,
            "output_type": "orchestrator_abstained_insufficient_evidence",
            "evidence_bundle": {
                "coverage": {"complete": False, "coverage_ratio": 0.5},
                "sufficiency": {"sufficient": False},
                "conflicts": [],
            },
        },
    )

    summary = summarize_agent_results([success, abstention])

    assert summary["task_success_rate"] == 1.0
    assert summary["plan_completion_rate"] == 0.5
    assert summary["evidence_sufficiency_rate"] == 0.5
    assert summary["conflict_safety_rate"] == 1.0
    assert summary["abstention_accuracy"] == 1.0
    assert summary["retrieval_round_avg"] == 1.0
