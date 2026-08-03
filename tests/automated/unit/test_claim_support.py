"""Claim-Evidence 支持校验测试。"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

from app.a2a.claim_support import (
    deterministic_claim_support,
    verify_claim_evidence,
)
from app.a2a.relation_claims import relation_endpoints, relation_hop_bounds
from app.a2a.evidence_fusion import evidence_identity
from app.a2a.medical_risk import MedicalRiskAssessment
from app.a2a.models import Evidence, SubAgentResult
from app.a2a.numeric_claims import (
    extract_numeric_requirements,
    extract_structured_numeric_claims,
)
from app.a2a.orchestrator.controller import UnifiedOrchestratorController
from app.a2a.question_planner import build_question_plan


def _task(plan, target_agent: str):
    return next(
        item for item in plan.retrieval_tasks if item.target_agent == target_agent
    )


def test_numeric_validator_checks_value_unit_and_applicability() -> None:
    plan = build_question_plan("老年人的收缩压目标是多少？", MedicalRiskAssessment())
    item = plan.must_cover[0]
    task = _task(plan, "rag")

    supported = deterministic_claim_support(
        task=task,
        must_cover=item,
        evidence=Evidence(
            source_type="document",
            source_id="numeric-1",
            content="老年人的收缩压目标为 130 mmHg。",
        ),
    )
    partial = deterministic_claim_support(
        task=task,
        must_cover=item,
        evidence=Evidence(
            source_type="document",
            source_id="numeric-2",
            content="收缩压目标为 130 mmHg。",
        ),
    )
    irrelevant = deterministic_claim_support(
        task=task,
        must_cover=item,
        evidence=Evidence(
            source_type="document",
            source_id="numeric-3",
            content="老年人应定期监测血压。",
        ),
    )

    assert supported is not None and supported.support_status == "supported"
    assert supported.applicability_conditions == ["老年人"]
    assert partial is not None and partial.support_status == "partially_supported"
    assert partial.reason == "applicability_condition_missing"
    assert irrelevant is not None and irrelevant.support_status == "irrelevant"


def test_numeric_validator_checks_age_and_requested_interval() -> None:
    plan = build_question_plan(
        "80 岁以上老年人的收缩压目标范围是多少？", MedicalRiskAssessment()
    )
    item = plan.must_cover[0]
    task = _task(plan, "rag")

    partial = deterministic_claim_support(
        task=task,
        must_cover=item,
        evidence=Evidence(
            source_type="document",
            source_id="numeric-range-1",
            content="80岁以上老年人的收缩压目标为 140 mmHg。",
        ),
    )
    supported = deterministic_claim_support(
        task=task,
        must_cover=item,
        evidence=Evidence(
            source_type="document",
            source_id="numeric-range-2",
            content="80岁以上老年人的收缩压目标范围为 130～140 mmHg。",
        ),
    )

    assert "80岁以上" in item.applicability_conditions
    assert partial is not None and partial.reason == "numeric_interval_missing"
    assert supported is not None and supported.support_status == "supported"


def test_age_number_cannot_satisfy_blood_pressure_target() -> None:
    plan = build_question_plan(
        "80岁以上老年人的收缩压目标是多少？", MedicalRiskAssessment()
    )
    item = plan.must_cover[0]
    task = _task(plan, "rag")

    assessment = deterministic_claim_support(
        task=task,
        must_cover=item,
        evidence=Evidence(
            source_type="document",
            source_id="age-only",
            content="80岁以上老年人应定期监测血压。",
        ),
    )

    claims = extract_structured_numeric_claims("80岁以上老年人应定期监测血压。")
    assert [claim.role for claim in claims] == ["applicability"]
    assert assessment is not None
    assert assessment.support_status == "irrelevant"
    assert assessment.reason == "numeric_claim_value_missing"


def test_blood_pressure_target_requires_matching_semantic_slot() -> None:
    plan = build_question_plan(
        "80岁以上老年人的收缩压目标是多少？", MedicalRiskAssessment()
    )
    item = plan.must_cover[0]
    task = _task(plan, "rag")

    assessment = deterministic_claim_support(
        task=task,
        must_cover=item,
        evidence=Evidence(
            source_type="document",
            source_id="target",
            content="80岁以上老年人的收缩压目标为140 mmHg。",
        ),
    )

    assert assessment is not None
    assert assessment.support_status == "supported"


def test_ocr_spaced_target_and_equivalent_age_condition_are_supported() -> None:
    plan = build_question_plan(
        "80岁以上老年人的收缩压目标是多少？", MedicalRiskAssessment()
    )
    item = plan.must_cover[0]
    task = _task(plan, "rag")

    assessment = deterministic_claim_support(
        task=task,
        must_cover=item,
        evidence=Evidence(
            source_type="document",
            source_id="ocr-target",
            content=(
                "8 0岁及以上高龄老年人降压目标<1 5 0 / 9 0mmH g，"
                "如能耐受可降至<1 4 0 / 9 0mmH g。"
            ),
        ),
    )
    claims = extract_structured_numeric_claims(
        "8 0岁及以上高龄老年人降压目标<1 5 0 / 9 0mmH g。"
    )

    assert {claim.subject for claim in claims if claim.role == "claim_value"} == {
        "systolic_blood_pressure",
        "diastolic_blood_pressure",
    }
    assert assessment is not None
    assert assessment.support_status == "supported"
    assert assessment.applicability_conditions == ["老年人", "80岁以上"]


def test_percentage_claim_is_not_confused_with_other_numbers() -> None:
    plan = build_question_plan("高血压患病率是多少？", MedicalRiskAssessment())
    item = plan.must_cover[0]
    task = _task(plan, "rag")

    assessment = deterministic_claim_support(
        task=task,
        must_cover=item,
        evidence=Evidence(
            source_type="document",
            source_id="prevalence",
            content="调查显示高血压患病率为27.5%。",
        ),
    )

    assert assessment is not None
    assert assessment.support_status == "supported"


def test_monitoring_frequency_only_supports_frequency_requirement() -> None:
    frequency_plan = build_question_plan(
        "家庭测血压每天测几次？", MedicalRiskAssessment()
    )
    target_plan = build_question_plan("家庭血压目标是多少？", MedicalRiskAssessment())
    evidence = Evidence(
        source_type="document",
        source_id="frequency",
        content="家庭测血压每天测2次/天。",
    )

    frequency = deterministic_claim_support(
        task=_task(frequency_plan, "rag"),
        must_cover=frequency_plan.must_cover[0],
        evidence=evidence,
    )
    target = deterministic_claim_support(
        task=_task(target_plan, "rag"),
        must_cover=target_plan.must_cover[0],
        evidence=evidence,
    )

    assert frequency is not None and frequency.support_status == "supported"
    assert target is not None and target.support_status == "irrelevant"


def test_multi_slot_monitoring_question_requires_duration_and_frequency() -> None:
    plan = build_question_plan(
        "家庭测血压一般连续几天、每天测几次？", MedicalRiskAssessment()
    )
    item = plan.must_cover[0]
    task = _task(plan, "rag")
    requirements = extract_numeric_requirements(item.question)

    partial = deterministic_claim_support(
        task=task,
        must_cover=item,
        evidence=Evidence(
            source_type="document",
            source_id="frequency-only",
            content="家庭测血压每天测2次/天。",
        ),
    )
    supported = deterministic_claim_support(
        task=task,
        must_cover=item,
        evidence=Evidence(
            source_type="document",
            source_id="duration-frequency",
            content="家庭测血压连续7天，每天测2次/天。",
        ),
    )

    assert [item.role for item in requirements] == ["temporal", "frequency"]
    assert partial is not None and partial.support_status == "partially_supported"
    assert partial.reason == "numeric_semantic_slot_missing"
    assert supported is not None and supported.support_status == "supported"


def test_diagnostic_predicate_is_inherited_by_local_threshold_clause() -> None:
    plan = build_question_plan(
        "家庭血压达到多少可以判断为高血压？", MedicalRiskAssessment()
    )
    item = plan.must_cover[0]

    assessment = deterministic_claim_support(
        task=_task(plan, "rag"),
        must_cover=item,
        evidence=Evidence(
            source_type="document",
            source_id="home-threshold",
            content=(
                "高血压的定义如下：在未使用降压药的情况下，"
                "非同日3次测量诊室血压≥140/90mmHg；或连续5~7d测量"
                "家庭血压≥135/85mmHg。"
            ),
        ),
    )

    assert assessment is not None
    assert assessment.support_status == "supported"
    assert assessment.applicability_conditions == ["家庭血压"]


def test_hbpm_duration_and_per_session_readings_support_monitoring_slots() -> None:
    plan = build_question_plan(
        "家庭测血压一般连续几天、每天测几次？", MedicalRiskAssessment()
    )
    item = plan.must_cover[0]
    evidence_text = "结合我国居民生活方式，建议进行5~7d的HBPM，早晚各测量2~3个读数。"

    assessment = deterministic_claim_support(
        task=_task(plan, "rag"),
        must_cover=item,
        evidence=Evidence(
            source_type="document",
            source_id="home-monitoring",
            content=evidence_text,
        ),
    )
    claims = extract_structured_numeric_claims(evidence_text)

    assert {claim.role for claim in claims} == {"temporal", "frequency"}
    assert assessment is not None
    assert assessment.support_status == "supported"
    assert assessment.applicability_conditions == ["家庭血压"]


def test_clinic_frequency_does_not_fill_home_monitoring_frequency_slot() -> None:
    plan = build_question_plan(
        "家庭测血压一般连续几天、每天测几次？", MedicalRiskAssessment()
    )
    item = plan.must_cover[0]
    evidence_text = (
        "用于诊断高血压时，诊室血压需非同日3次测量；" "家庭血压需连续5~7d测量。"
    )

    assessment = deterministic_claim_support(
        task=_task(plan, "rag"),
        must_cover=item,
        evidence=Evidence(
            source_type="document",
            source_id="mixed-measurement-contexts",
            content=evidence_text,
        ),
    )
    claims = extract_structured_numeric_claims(evidence_text)
    frequency_claim = next(claim for claim in claims if claim.role == "frequency")

    assert frequency_claim.measurement_context == ["clinic"]
    assert assessment is not None
    assert assessment.support_status == "partially_supported"
    assert assessment.reason == "numeric_semantic_slot_missing"


def test_comparison_claim_requires_each_numeric_subject() -> None:
    plan = build_question_plan(
        "收缩压和舒张压目标分别是多少？", MedicalRiskAssessment()
    )
    item = plan.must_cover[0]
    task = _task(plan, "rag")

    partial = deterministic_claim_support(
        task=task,
        must_cover=item,
        evidence=Evidence(
            source_type="document",
            source_id="systolic-only",
            content="收缩压目标为130 mmHg。",
        ),
    )
    supported = deterministic_claim_support(
        task=task,
        must_cover=item,
        evidence=Evidence(
            source_type="document",
            source_id="both-targets",
            content="收缩压目标为130 mmHg，舒张压目标为80 mmHg。",
        ),
    )

    assert item.claim_type == "comparison"
    assert partial is not None and partial.support_status == "partially_supported"
    assert partial.reason == "numeric_semantic_slot_missing"
    assert supported is not None and supported.support_status == "supported"


def test_dose_claim_requires_matching_intervention() -> None:
    plan = build_question_plan("氨氯地平剂量是多少？", MedicalRiskAssessment())
    item = plan.must_cover[0]
    task = _task(plan, "rag")

    missing_intervention = deterministic_claim_support(
        task=task,
        must_cover=item,
        evidence=Evidence(
            source_type="document",
            source_id="dose-generic",
            content="推荐剂量为5 mg。",
        ),
    )
    matched = deterministic_claim_support(
        task=task,
        must_cover=item,
        evidence=Evidence(
            source_type="document",
            source_id="dose-drug",
            content="氨氯地平剂量为5 mg。",
        ),
    )

    assert missing_intervention is not None
    assert missing_intervention.support_status == "irrelevant"
    assert matched is not None and matched.support_status == "supported"


def test_relation_validator_requires_relationship_shape() -> None:
    plan = build_question_plan("高血压和肾病有什么关系？", MedicalRiskAssessment())
    item = plan.must_cover[0]
    task = _task(plan, "kg")
    relationship = Evidence(
        source_type="knowledge_graph",
        source_id="rel-1",
        content="高血压 -[增加风险]-> 慢性肾病",
        provenance={"kind": "relationship", "raw": {"relation_type": "增加风险"}},
    )
    entity = Evidence(
        source_type="knowledge_graph",
        source_id="entity-1",
        content="高血压",
        provenance={"kind": "entity", "raw": {"name": "高血压"}},
    )

    supported = deterministic_claim_support(
        task=task, must_cover=item, evidence=relationship
    )
    partial = deterministic_claim_support(task=task, must_cover=item, evidence=entity)

    assert supported is not None and supported.support_status == "supported"
    assert partial is not None and partial.support_status == "partially_supported"
    assert partial.reason == "relationship_structure_missing"


def test_source_validator_requires_publication_and_locator() -> None:
    plan = build_question_plan("请说明指南来源", MedicalRiskAssessment())
    item = plan.must_cover[0]
    task = _task(plan, "rag")
    located = Evidence(
        source_type="document",
        source_id="source-1",
        origin_document_id="guide-2024",
        source_chunk_id="chunk-1",
        content="指南原文",
    )
    unlocated = located.model_copy(
        update={"source_id": "source-2", "source_chunk_id": ""}
    )

    supported = deterministic_claim_support(
        task=task, must_cover=item, evidence=located
    )
    partial = deterministic_claim_support(
        task=task, must_cover=item, evidence=unlocated
    )

    assert supported is not None and supported.support_status == "supported"
    assert partial is not None and partial.support_status == "partially_supported"
    assert partial.reason == "source_locator_missing"


def test_source_validator_rejects_inconsistent_locator() -> None:
    plan = build_question_plan("请说明指南来源", MedicalRiskAssessment())
    item = plan.must_cover[0]
    task = _task(plan, "rag")
    inconsistent = Evidence(
        source_type="document",
        source_id="source-1",
        origin_document_id="guide-2024",
        page=10,
        source_page=11,
        content="指南原文",
    )

    assessment = deterministic_claim_support(
        task=task, must_cover=item, evidence=inconsistent
    )

    assert assessment is not None
    assert assessment.support_status == "contradicted"
    assert assessment.reason == "source_locator_inconsistent"


class _SemanticVerifierClient:
    def __init__(
        self,
        support_status: str = "supported",
        support_excerpt: str = "应规律监测血压",
    ) -> None:
        self.support_status = support_status
        self.support_excerpt = support_excerpt
        self.calls: list[dict[str, Any]] = []

    async def chat_with_tokens_async(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        user_payload = json.loads(kwargs["messages"][1]["content"])
        evidence_id = user_payload["evidence"][0]["evidence_id"]
        return SimpleNamespace(
            content=json.dumps(
                {
                    "assessments": [
                        {
                            "evidence_id": evidence_id,
                            "support_status": self.support_status,
                            "support_excerpt": self.support_excerpt,
                            "applicability_conditions": [],
                            "reason": "语义直接支持",
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
        )


def test_free_text_uses_structured_semantic_verifier() -> None:
    plan = build_question_plan("高血压患者如何监测？", MedicalRiskAssessment())
    item = plan.must_cover[0]
    task = _task(plan, "rag")
    evidence = Evidence(
        source_type="document",
        source_id="fact-1",
        origin_document_id="guide-2024",
        content="高血压患者应规律监测血压。",
    )
    client = _SemanticVerifierClient()

    assessments, usage = asyncio.run(
        verify_claim_evidence(
            task=task,
            must_cover=item,
            evidence=[evidence],
            llm_client=client,
            timeout=30,
        )
    )

    assert len(client.calls) == 1
    assert client.calls[0]["response_format"] == {"type": "json_object"}
    assert assessments[0].evidence_id == evidence_identity(evidence)
    assert assessments[0].support_status == "supported"
    assert assessments[0].verifier_version == "claim_support.semantic.v1"
    assert usage == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}


def test_missing_semantic_model_returns_uncertain() -> None:
    plan = build_question_plan("高血压患者如何监测？", MedicalRiskAssessment())
    item = plan.must_cover[0]
    task = _task(plan, "rag")
    evidence = Evidence(
        source_type="document",
        source_id="fact-1",
        content="高血压患者应规律监测血压。",
    )

    assessments, usage = asyncio.run(
        verify_claim_evidence(
            task=task,
            must_cover=item,
            evidence=[evidence],
            llm_client=None,
            timeout=0,
        )
    )

    assert assessments[0].support_status == "uncertain"
    assert assessments[0].reason == "semantic_verifier_unavailable"
    assert usage == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def test_controller_does_not_trust_unverified_subagent_assessment() -> None:
    plan = build_question_plan("高血压患者如何监测？", MedicalRiskAssessment())
    item = plan.must_cover[0]
    task = _task(plan, "rag")
    evidence = Evidence(
        source_type="document",
        source_id="fact-1",
        content="高血压患者应规律监测血压。",
    )
    controller = object.__new__(UnifiedOrchestratorController)

    assessments, _ = asyncio.run(
        controller._verify_task_evidence(
            question_plan=plan,
            task_id=task.task_id,
            must_cover_id=item.item_id,
            evidence_rows=[evidence],
            existing_rows=[
                {
                    "task_id": task.task_id,
                    "must_cover_id": item.item_id,
                    "evidence_id": evidence_identity(evidence),
                    "support_status": "supported",
                    "support_excerpt": evidence.content,
                }
            ],
        )
    )

    assert assessments[0].support_status == "uncertain"
    assert assessments[0].reason == "semantic_verifier_unavailable"


def test_semantic_verifier_rejects_ungrounded_excerpt() -> None:
    plan = build_question_plan("高血压患者如何监测？", MedicalRiskAssessment())
    item = plan.must_cover[0]
    task = _task(plan, "rag")
    evidence = Evidence(
        source_type="document",
        source_id="fact-1",
        content="高血压患者应规律监测血压。",
    )

    assessments, _ = asyncio.run(
        verify_claim_evidence(
            task=task,
            must_cover=item,
            evidence=[evidence],
            llm_client=_SemanticVerifierClient(support_excerpt="证据中不存在的内容"),
            timeout=30,
        )
    )

    assert assessments[0].support_status == "uncertain"
    assert assessments[0].support_excerpt == ""
    assert assessments[0].reason == "semantic_verifier_excerpt_not_grounded"


def test_controller_records_verifier_output_before_support_transition() -> None:
    plan = build_question_plan("高血压患者如何监测？", MedicalRiskAssessment())
    item = plan.must_cover[0]
    planned_task = _task(plan, "rag")
    evidence = Evidence(
        source_type="document",
        source_id="fact-1",
        origin_document_id="guide-2024",
        content="高血压患者应规律监测血压。",
    )
    controller = object.__new__(UnifiedOrchestratorController)
    controller._client = _SemanticVerifierClient()
    controller._conf = SimpleNamespace(LLM_REQUEST_TIMEOUT_SECONDS=30)

    assessments, _ = asyncio.run(
        controller._verify_task_evidence(
            question_plan=plan,
            task_id=planned_task.task_id,
            must_cover_id=item.item_id,
            evidence_rows=[evidence],
            existing_rows=[],
        )
    )
    task = controller._build_subtask(
        action={
            "action": "call_rag",
            "task_id": planned_task.task_id,
            "must_cover_id": item.item_id,
        },
        max_tool_calls=3,
        question_plan=plan,
    )
    result = SubAgentResult(
        task_id=task.task_id,
        must_cover_id=task.must_cover_id,
        target_agent="rag",
        evidence=[evidence],
        claim_assessments=assessments,
    )

    controller._update_question_plan(plan, task, result)

    assert item.support_status == "supported"
    assert item.status == "covered"
    assert plan.claim_evidence_assessments == assessments


def test_relation_question_parses_path_endpoints_and_exact_hops() -> None:
    assert relation_endpoints("高血压与家庭之间有什么两跳路径？") == [
        "高血压",
        "家庭",
    ]
    assert relation_hop_bounds("高血压与家庭之间有什么两跳路径？") == (2, 2)


def test_relation_question_does_not_split_age_phrase_on_and_character() -> None:
    assert relation_endpoints("80岁及以上人群与诊室血压的关系是什么？") == [
        "80岁及以上人群",
        "诊室血压",
    ]
