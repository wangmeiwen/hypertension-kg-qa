"""结构化终答上下文与后置校验测试。"""

from __future__ import annotations

from app.a2a.answer_context import (
    build_answer_generation_context,
    extract_structured_answer_claims,
    validate_answer_generation,
)
from app.a2a.evidence_fusion import evidence_identity, legacy_evidence_identity
from app.a2a.medical_risk import MedicalRiskAssessment
from app.a2a.models import ClaimEvidenceAssessment, Evidence, EvidenceConflict
from app.a2a.orchestrator.controller import UnifiedOrchestratorController
from app.a2a.question_planner import build_question_plan


def test_context_only_contains_verified_support_for_its_must_cover() -> None:
    plan = build_question_plan("高血压患者如何监测？", MedicalRiskAssessment())
    task = next(item for item in plan.retrieval_tasks if item.target_agent == "rag")
    supported = Evidence(
        source_type="document",
        source_id="guide-a",
        origin_document_id="guide-a",
        source_page=8,
        content="高血压患者应规律监测血压。",
    )
    irrelevant = Evidence(
        source_type="document",
        source_id="guide-b",
        origin_document_id="guide-b",
        source_page=9,
        content="老年人应保持均衡饮食。",
    )
    plan.must_cover[0].support_status = "supported"
    plan.claim_evidence_assessments.extend(
        [
            ClaimEvidenceAssessment(
                task_id=task.task_id,
                must_cover_id=task.must_cover_id,
                evidence_id=evidence_identity(supported),
                support_status="supported",
                support_excerpt="高血压患者应规律监测血压。",
            ),
            ClaimEvidenceAssessment(
                task_id=task.task_id,
                must_cover_id=task.must_cover_id,
                evidence_id=evidence_identity(irrelevant),
                support_status="irrelevant",
                support_excerpt="老年人应保持均衡饮食。",
            ),
        ]
    )

    context = build_answer_generation_context(
        question_plan=plan,
        evidence_by_item={
            task.must_cover_id: [("rag", supported), ("rag", irrelevant)]
        },
        conflicts=[],
    )

    evidence_contexts = context.must_cover[0].supporting_evidence
    assert len(evidence_contexts) == 1
    assert evidence_contexts[0].evidence_id == evidence_identity(supported)
    assert evidence_contexts[0].citation == "[guide-a p8]"
    assert "均衡饮食" not in context.model_dump_json()


def test_context_reads_unique_legacy_identity_and_emits_canonical_identity() -> None:
    plan = build_question_plan("高血压患者如何监测？", MedicalRiskAssessment())
    task = next(item for item in plan.retrieval_tasks if item.target_agent == "rag")
    evidence = Evidence(
        source_type="document",
        source_id="guide.pdf",
        origin_document_id="guide-a",
        source_page=8,
        source_chunk_id="chunk-8-1",
        content="高血压患者应规律监测血压。",
    )
    plan.must_cover[0].support_status = "supported"
    plan.claim_evidence_assessments.append(
        ClaimEvidenceAssessment(
            task_id=task.task_id,
            must_cover_id=task.must_cover_id,
            evidence_id=legacy_evidence_identity(evidence),
            support_status="supported",
            support_excerpt=evidence.content,
        )
    )

    context = build_answer_generation_context(
        question_plan=plan,
        evidence_by_item={task.must_cover_id: [("rag", evidence)]},
        conflicts=[],
    )

    assert len(context.must_cover[0].supporting_evidence) == 1
    assert context.must_cover[0].supporting_evidence[
        0
    ].evidence_id == evidence_identity(evidence)


def test_context_rejects_ambiguous_legacy_identity() -> None:
    plan = build_question_plan("高血压患者如何监测？", MedicalRiskAssessment())
    task = next(item for item in plan.retrieval_tasks if item.target_agent == "rag")
    first = Evidence(
        source_type="document",
        source_id="guide.pdf",
        origin_document_id="guide-a",
        source_page=8,
        source_chunk_id="chunk-8-1",
        content="高血压患者应规律监测血压。",
    )
    second = Evidence(
        source_type="document",
        source_id="guide.pdf",
        origin_document_id="guide-a",
        source_page=9,
        source_chunk_id="chunk-9-1",
        content="监测血压时应记录测量时间。",
    )
    plan.must_cover[0].support_status = "supported"
    plan.claim_evidence_assessments.append(
        ClaimEvidenceAssessment(
            task_id=task.task_id,
            must_cover_id=task.must_cover_id,
            evidence_id=legacy_evidence_identity(first),
            support_status="supported",
            support_excerpt=first.content,
        )
    )

    context = build_answer_generation_context(
        question_plan=plan,
        evidence_by_item={task.must_cover_id: [("rag", first), ("rag", second)]},
        conflicts=[],
    )

    assert context.must_cover[0].supporting_evidence == []


def test_post_validation_requires_bound_citation_and_applicability() -> None:
    plan = build_question_plan("老年人的收缩压目标是多少？", MedicalRiskAssessment())
    task = next(item for item in plan.retrieval_tasks if item.target_agent == "rag")
    evidence = Evidence(
        source_type="document",
        source_id="guide-a",
        origin_document_id="guide-a",
        source_page=12,
        content="老年人的收缩压目标为 130 mmHg。",
    )
    corroborating_evidence = evidence.model_copy(
        update={
            "source_id": "guide-b",
            "origin_document_id": "guide-b",
            "source_page": 18,
        }
    )
    plan.must_cover[0].support_status = "supported"
    plan.claim_evidence_assessments.append(
        ClaimEvidenceAssessment(
            task_id=task.task_id,
            must_cover_id=task.must_cover_id,
            evidence_id=evidence_identity(evidence),
            support_status="supported",
            support_excerpt=evidence.content,
            applicability_conditions=["老年人"],
        )
    )
    plan.claim_evidence_assessments.append(
        ClaimEvidenceAssessment(
            task_id=task.task_id,
            must_cover_id=task.must_cover_id,
            evidence_id=evidence_identity(corroborating_evidence),
            support_status="supported",
            support_excerpt=corroborating_evidence.content,
            applicability_conditions=["老年人"],
        )
    )
    context = build_answer_generation_context(
        question_plan=plan,
        evidence_by_item={
            task.must_cover_id: [
                ("rag", evidence),
                ("rag", corroborating_evidence),
            ]
        },
        conflicts=[],
    )

    no_citation = validate_answer_generation("老年人的目标为 130 mmHg。", context)
    no_applicability = validate_answer_generation(
        "目标为 130 mmHg。[guide-a p12]", context
    )
    valid = validate_answer_generation(
        "老年人的收缩压目标为 130 mmHg。[guide-a p12]", context
    )

    assert no_citation.valid is False
    assert no_citation.missing_citation_ids == [task.must_cover_id]
    assert no_applicability.missing_applicability == {task.must_cover_id: ["老年人"]}
    assert valid.valid is True
    assert valid.covered_must_cover_ids == [task.must_cover_id]


def test_post_validation_rejects_wrong_number_with_valid_citation() -> None:
    plan = build_question_plan("老年人的收缩压目标是多少？", MedicalRiskAssessment())
    task = next(item for item in plan.retrieval_tasks if item.target_agent == "rag")
    evidence = Evidence(
        source_type="document",
        source_id="guide-a",
        origin_document_id="guide-a",
        source_page=12,
        content="老年人的收缩压目标为 140 mmHg。",
    )
    plan.must_cover[0].support_status = "supported"
    plan.claim_evidence_assessments.append(
        ClaimEvidenceAssessment(
            task_id=task.task_id,
            must_cover_id=task.must_cover_id,
            evidence_id=evidence_identity(evidence),
            support_status="supported",
            support_excerpt=evidence.content,
            applicability_conditions=["老年人"],
        )
    )
    context = build_answer_generation_context(
        question_plan=plan,
        evidence_by_item={task.must_cover_id: [("rag", evidence)]},
        conflicts=[],
    )

    result = validate_answer_generation(
        "老年人的收缩压目标为 130 mmHg。[guide-a p12]", context
    )

    assert result.valid is False
    assert result.numeric_mismatch_ids == [result.answer_claims[0].claim_id]
    assert result.unsupported_claim_ids == [result.answer_claims[0].claim_id]


def test_post_validation_rejects_reversed_recommendation_polarity() -> None:
    plan = build_question_plan("是否推荐家庭血压监测？", MedicalRiskAssessment())
    task = next(item for item in plan.retrieval_tasks if item.target_agent == "rag")
    evidence = Evidence(
        source_type="document",
        source_id="guide-a",
        origin_document_id="guide-a",
        source_page=8,
        content="推荐高血压患者进行家庭血压监测。",
    )
    plan.must_cover[0].support_status = "supported"
    plan.claim_evidence_assessments.append(
        ClaimEvidenceAssessment(
            task_id=task.task_id,
            must_cover_id=task.must_cover_id,
            evidence_id=evidence_identity(evidence),
            support_status="supported",
            support_excerpt=evidence.content,
        )
    )
    context = build_answer_generation_context(
        question_plan=plan,
        evidence_by_item={task.must_cover_id: [("rag", evidence)]},
        conflicts=[],
    )

    result = validate_answer_generation(
        "不推荐高血压患者进行家庭血压监测。[guide-a p8]", context
    )

    assert result.valid is False
    assert result.unsupported_claim_ids == [result.answer_claims[0].claim_id]


def test_post_validation_rejects_reversal_of_implicit_positive_recommendation() -> None:
    plan = build_question_plan("是否推荐家庭血压监测？", MedicalRiskAssessment())
    task = next(item for item in plan.retrieval_tasks if item.target_agent == "rag")
    evidence = Evidence(
        source_type="document",
        source_id="guide-a",
        origin_document_id="guide-a",
        content="HBPM可以显著提升高血压控制率，并改善患者预后。",
    )
    plan.must_cover[0].support_status = "supported"
    plan.claim_evidence_assessments.append(
        ClaimEvidenceAssessment(
            task_id=task.task_id,
            must_cover_id=task.must_cover_id,
            evidence_id=evidence_identity(evidence),
            support_status="supported",
            support_excerpt=evidence.content,
        )
    )
    context = build_answer_generation_context(
        question_plan=plan,
        evidence_by_item={task.must_cover_id: [("rag", evidence)]},
        conflicts=[],
    )

    result = validate_answer_generation(
        "不推荐高血压患者进行家庭血压监测。[guide-a]", context
    )

    assert result.valid is False
    assert result.unsupported_claim_ids == [result.answer_claims[0].claim_id]


def test_post_validation_rejects_explicit_polarity_when_evidence_is_directionless() -> (
    None
):
    plan = build_question_plan("是否推荐家庭血压监测？", MedicalRiskAssessment())
    task = next(item for item in plan.retrieval_tasks if item.target_agent == "rag")
    evidence = Evidence(
        source_type="document",
        source_id="guide-a",
        origin_document_id="guide-a",
        content="家庭血压监测是一种诊室外血压测量方法。",
    )
    plan.must_cover[0].support_status = "supported"
    plan.claim_evidence_assessments.append(
        ClaimEvidenceAssessment(
            task_id=task.task_id,
            must_cover_id=task.must_cover_id,
            evidence_id=evidence_identity(evidence),
            support_status="supported",
            support_excerpt=evidence.content,
        )
    )
    context = build_answer_generation_context(
        question_plan=plan,
        evidence_by_item={task.must_cover_id: [("rag", evidence)]},
        conflicts=[],
    )

    result = validate_answer_generation(
        "推荐高血压患者进行家庭血压监测。[guide-a]", context
    )

    assert result.valid is False
    assert result.unsupported_claim_ids == [result.answer_claims[0].claim_id]


def test_post_validation_rejects_reversed_kg_relation_direction() -> None:
    plan = build_question_plan("高血压和慢性肾病有什么关系？", MedicalRiskAssessment())
    task = next(item for item in plan.retrieval_tasks if item.target_agent == "kg")
    evidence = Evidence(
        source_type="knowledge_graph",
        source_id="rel-1",
        origin_document_id="guide-a",
        content="高血压 -[增加风险]-> 慢性肾病",
        provenance={
            "kind": "relationship",
            "raw": {
                "source_name": "高血压",
                "relation_type": "增加风险",
                "target_name": "慢性肾病",
            },
        },
    )
    plan.must_cover[0].support_status = "supported"
    plan.claim_evidence_assessments.append(
        ClaimEvidenceAssessment(
            task_id=task.task_id,
            must_cover_id=task.must_cover_id,
            evidence_id=evidence_identity(evidence),
            support_status="supported",
            support_excerpt=evidence.content,
        )
    )
    context = build_answer_generation_context(
        question_plan=plan,
        evidence_by_item={task.must_cover_id: [("kg", evidence)]},
        conflicts=[],
    )

    result = validate_answer_generation("慢性肾病导致高血压。[rel-1]", context)

    assert result.valid is False
    assert result.unsupported_claim_ids == [result.answer_claims[0].claim_id]


def test_post_validation_rejects_passive_reversed_kg_relation_direction() -> None:
    plan = build_question_plan("高血压和慢性肾病有什么关系？", MedicalRiskAssessment())
    task = next(item for item in plan.retrieval_tasks if item.target_agent == "kg")
    evidence = Evidence(
        source_type="knowledge_graph",
        source_id="rel-1",
        origin_document_id="guide-a",
        content="高血压 -[增加风险]-> 慢性肾病",
        provenance={
            "kind": "relationship",
            "raw": {
                "source_name": "高血压",
                "relation_type": "增加风险",
                "target_name": "慢性肾病",
            },
        },
    )
    plan.must_cover[0].support_status = "supported"
    plan.claim_evidence_assessments.append(
        ClaimEvidenceAssessment(
            task_id=task.task_id,
            must_cover_id=task.must_cover_id,
            evidence_id=evidence_identity(evidence),
            support_status="supported",
            support_excerpt=evidence.content,
        )
    )
    context = build_answer_generation_context(
        question_plan=plan,
        evidence_by_item={task.must_cover_id: [("kg", evidence)]},
        conflicts=[],
    )

    reversed_result = validate_answer_generation(
        "高血压由慢性肾病引起。[rel-1]", context
    )
    forward_result = validate_answer_generation(
        "慢性肾病可由高血压引起。[rel-1]", context
    )

    assert reversed_result.valid is False
    assert reversed_result.unsupported_claim_ids == [
        reversed_result.answer_claims[0].claim_id
    ]
    assert forward_result.valid is True


def test_post_validation_rejects_directionless_relation_summary() -> None:
    plan = build_question_plan("高血压和慢性肾病有什么关系？", MedicalRiskAssessment())
    task = next(item for item in plan.retrieval_tasks if item.target_agent == "kg")
    evidence = Evidence(
        source_type="knowledge_graph",
        source_id="rel-1",
        origin_document_id="guide-a",
        content="高血压 -[增加风险]-> 慢性肾病",
        provenance={
            "kind": "relationship",
            "raw": {
                "source_name": "高血压",
                "relation_type": "增加风险",
                "target_name": "慢性肾病",
            },
        },
    )
    plan.must_cover[0].support_status = "supported"
    plan.claim_evidence_assessments.append(
        ClaimEvidenceAssessment(
            task_id=task.task_id,
            must_cover_id=task.must_cover_id,
            evidence_id=evidence_identity(evidence),
            support_status="supported",
            support_excerpt=evidence.content,
        )
    )
    context = build_answer_generation_context(
        question_plan=plan,
        evidence_by_item={task.must_cover_id: [("kg", evidence)]},
        conflicts=[],
    )

    result = validate_answer_generation("高血压与慢性肾病相关。[rel-1]", context)

    assert result.valid is False
    assert result.unsupported_claim_ids == [result.answer_claims[0].claim_id]


def test_post_validation_accepts_explicit_systolic_explanation_of_bp_pair() -> None:
    plan = build_question_plan(
        "80岁以上老年人的收缩压目标是多少？", MedicalRiskAssessment()
    )
    task = next(item for item in plan.retrieval_tasks if item.target_agent == "rag")
    evidence = Evidence(
        source_type="document",
        source_id="中国高血压防治指南2024.pdf",
        origin_document_id="中国高血压防治指南2024.pdf",
        source_page=20,
        source_chunk_id="2",
        content=(
            "80岁及以上高龄老年人降压目标<150/90mmHg，" "如能耐受，可降至<140/90mmHg。"
        ),
    )
    plan.must_cover[0].support_status = "supported"
    plan.claim_evidence_assessments.append(
        ClaimEvidenceAssessment(
            task_id=task.task_id,
            must_cover_id=task.must_cover_id,
            evidence_id=evidence_identity(evidence),
            support_status="supported",
            support_excerpt=evidence.content,
            applicability_conditions=["老年人", "80岁以上"],
        )
    )
    context = build_answer_generation_context(
        question_plan=plan,
        evidence_by_item={task.must_cover_id: [("rag", evidence)]},
        conflicts=[],
    )

    result = validate_answer_generation(
        "对于80岁以上的老年人，降压目标为<150/90mmHg（即收缩压目标"
        "<150mmHg），如能耐受，可降至<140/90mmHg（即收缩压目标"
        "<140mmHg）[中国高血压防治指南2024.pdf p20 chunk:2]。",
        context,
    )

    assert result.valid is True
    assert result.numeric_mismatch_ids == []


def test_post_validation_binds_number_to_the_cited_evidence() -> None:
    plan = build_question_plan("收缩压目标是多少？", MedicalRiskAssessment())
    task = next(item for item in plan.retrieval_tasks if item.target_agent == "rag")
    first = Evidence(
        source_type="document",
        source_id="guide-a",
        origin_document_id="guide-a",
        source_page=12,
        source_chunk_id="target-130",
        content="收缩压目标为 130 mmHg。",
    )
    second = Evidence(
        source_type="document",
        source_id="guide-a",
        origin_document_id="guide-a",
        source_page=13,
        source_chunk_id="target-140",
        content="收缩压目标为 140 mmHg。",
    )
    plan.must_cover[0].support_status = "supported"
    plan.claim_evidence_assessments.extend(
        ClaimEvidenceAssessment(
            task_id=task.task_id,
            must_cover_id=task.must_cover_id,
            evidence_id=evidence_identity(row),
            support_status="supported",
            support_excerpt=row.content,
        )
        for row in (first, second)
    )
    context = build_answer_generation_context(
        question_plan=plan,
        evidence_by_item={task.must_cover_id: [("rag", first), ("rag", second)]},
        conflicts=[],
    )

    wrong_citation = validate_answer_generation(
        "收缩压目标为 130 mmHg。[guide-a p13 chunk:target-140]", context
    )
    correct_citation = validate_answer_generation(
        "收缩压目标为 130 mmHg。[guide-a p12 chunk:target-130]", context
    )

    assert wrong_citation.valid is False
    assert wrong_citation.numeric_mismatch_ids
    assert correct_citation.valid is True


def test_post_validation_inherits_single_item_scope_and_rejects_added_number() -> None:
    plan = build_question_plan(
        "家庭测血压一般连续几天、每天测几次？", MedicalRiskAssessment()
    )
    task = next(item for item in plan.retrieval_tasks if item.target_agent == "rag")
    evidence = Evidence(
        source_type="document",
        source_id="guide-a",
        origin_document_id="guide-a",
        source_page=7,
        content=(
            "建议进行5~7d的HBPM，早晚各测量2~3个读数。"
            "在不少于3d、12个测量值时取平均值。"
        ),
    )
    plan.must_cover[0].support_status = "supported"
    plan.claim_evidence_assessments.append(
        ClaimEvidenceAssessment(
            task_id=task.task_id,
            must_cover_id=task.must_cover_id,
            evidence_id=evidence_identity(evidence),
            support_status="supported",
            support_excerpt=evidence.content,
            applicability_conditions=["家庭血压"],
        )
    )
    context = build_answer_generation_context(
        question_plan=plan,
        evidence_by_item={task.must_cover_id: [("rag", evidence)]},
        conflicts=[],
    )

    valid = validate_answer_generation(
        "针对家庭血压，建议连续测量5~7天，早晚各测量2~3个读数" "[guide-a p7]。",
        context,
    )
    added_number = validate_answer_generation(
        "针对家庭血压，建议连续测量5~7天，每天早晚各测量1次，"
        "每次测量2~3个读数[guide-a p7]。",
        context,
    )
    supported_details = validate_answer_generation(
        "针对家庭血压，建议连续测量5~7天，早晚各测量2~3个读数；"
        "在不少于3d、12个测量值时取平均值[guide-a p7]。",
        context,
    )
    wrong_details = validate_answer_generation(
        "针对家庭血压，建议连续测量5~7天，早晚各测量2~3个读数；"
        "在不少于4d、13个测量值时取平均值[guide-a p7]。",
        context,
    )

    assert valid.valid is True
    assert supported_details.valid is True
    assert added_number.valid is False
    assert added_number.numeric_mismatch_ids == [added_number.answer_claims[0].claim_id]
    assert wrong_details.valid is False
    assert wrong_details.numeric_mismatch_ids == [
        wrong_details.answer_claims[0].claim_id
    ]


def test_post_validation_accepts_definition_thresholds_across_ocr_linebreaks() -> None:
    plan = build_question_plan("高血压急症的定义是什么？", MedicalRiskAssessment())
    task = next(item for item in plan.retrieval_tasks if item.target_agent == "rag")
    evidence = Evidence(
        source_type="document",
        source_id="guide-a",
        origin_document_id="guide-a",
        source_page=35,
        content=(
            "高血压急症是指原发性或继发\n性高血压患者，血压突然和显著升\n"
            "高，通常收缩压>180mmHg和/或舒张压>120mmHg。"
        ),
    )
    plan.must_cover[0].support_status = "supported"
    plan.claim_evidence_assessments.append(
        ClaimEvidenceAssessment(
            task_id=task.task_id,
            must_cover_id=task.must_cover_id,
            evidence_id=evidence_identity(evidence),
            support_status="supported",
            support_excerpt=evidence.content,
        )
    )
    context = build_answer_generation_context(
        question_plan=plan,
        evidence_by_item={task.must_cover_id: [("rag", evidence)]},
        conflicts=[],
    )

    result = validate_answer_generation(
        "高血压急症通常收缩压>180mmHg和/或舒张压>120mmHg" "[guide-a p35]。",
        context,
    )

    assert result.valid is True
    assert result.numeric_mismatch_ids == []


def test_post_validation_accepts_logical_blood_pressure_pairs_from_guide_table() -> (
    None
):
    plan = build_question_plan("动态血压诊断阈值是多少？", MedicalRiskAssessment())
    task = next(item for item in plan.retrieval_tasks if item.target_agent == "rag")
    evidence = Evidence(
        source_type="document",
        source_id="guide-a",
        origin_document_id="guide-a",
        source_page=16,
        content=(
            "基于动态血压的高血压诊断标准"
            "|血压类型|测量方式|收缩压/舒张压(mmHg)|"
            "|动态血压|24h平均值|≥130和/或≥80|"
            "|动态血压|白天平均值|≥135和/或≥85|"
            "|动态血压|夜晚平均值|≥120和/或≥70|"
        ),
    )
    plan.must_cover[0].support_status = "supported"
    plan.claim_evidence_assessments.append(
        ClaimEvidenceAssessment(
            task_id=task.task_id,
            must_cover_id=task.must_cover_id,
            evidence_id=evidence_identity(evidence),
            support_status="supported",
            support_excerpt=evidence.content,
            applicability_conditions=["动态血压"],
        )
    )
    context = build_answer_generation_context(
        question_plan=plan,
        evidence_by_item={task.must_cover_id: [("rag", evidence)]},
        conflicts=[],
    )

    result = validate_answer_generation(
        "针对动态血压，诊断阈值包括：24h平均值≥130和/或≥80mmHg；"
        "白天平均值≥135和/或≥85mmHg；"
        "夜晚平均值≥120和/或≥70mmHg[guide-a p16]。",
        context,
    )

    assert result.valid is True
    assert result.numeric_mismatch_ids == []


def test_post_validation_inherits_scope_for_exclusively_cited_numeric_bullets() -> None:
    plan = build_question_plan(
        "家庭血压和动态血压分别是多少？", MedicalRiskAssessment()
    )
    home_item, ambulatory_item = plan.must_cover
    home_task = next(
        item
        for item in plan.retrieval_tasks
        if item.must_cover_id == home_item.item_id and item.target_agent == "rag"
    )
    ambulatory_task = next(
        item
        for item in plan.retrieval_tasks
        if item.must_cover_id == ambulatory_item.item_id and item.target_agent == "rag"
    )
    home_evidence = Evidence(
        source_type="document",
        source_id="guide-a",
        origin_document_id="guide-a",
        source_page=6,
        content="家庭血压诊断阈值为≥135/85mmHg。",
    )
    ambulatory_evidence = Evidence(
        source_type="document",
        source_id="guide-a",
        origin_document_id="guide-a",
        source_page=16,
        content=(
            "基于动态血压的高血压诊断标准"
            "|血压类型|测量方式|收缩压/舒张压(mmHg)|"
            "|动态血压|24h平均值|≥130和/或≥80|"
            "|动态血压|白天平均值|≥135和/或≥85|"
            "|动态血压|夜晚平均值|≥120和/或≥70|"
        ),
    )
    for item, task, evidence, condition in (
        (home_item, home_task, home_evidence, "家庭血压"),
        (ambulatory_item, ambulatory_task, ambulatory_evidence, "动态血压"),
    ):
        item.support_status = "supported"
        plan.claim_evidence_assessments.append(
            ClaimEvidenceAssessment(
                task_id=task.task_id,
                must_cover_id=item.item_id,
                evidence_id=evidence_identity(evidence),
                support_status="supported",
                support_excerpt=evidence.content,
                applicability_conditions=[condition],
            )
        )
    context = build_answer_generation_context(
        question_plan=plan,
        evidence_by_item={
            home_item.item_id: [("rag", home_evidence)],
            ambulatory_item.item_id: [("rag", ambulatory_evidence)],
        },
        conflicts=[],
    )

    result = validate_answer_generation(
        "### 家庭血压\n"
        "在未使用降压药的情况下，连续5~7d测量家庭血压≥135/85mmHg"
        "[guide-a p6]。\n"
        "动态血压诊断阈值如下[guide-a p16]：\n"
        "- 24h平均值：≥130和/或≥80mmHg[guide-a p16]。\n"
        "- 白天平均值：≥135和/或≥85mmHg[guide-a p16]。\n"
        "- 夜晚平均值：≥120和/或≥70mmHg[guide-a p16]。",
        context,
    )

    assert result.valid is True
    assert result.numeric_mismatch_ids == []


def test_post_validation_does_not_inherit_scope_from_shared_citation() -> None:
    plan = build_question_plan(
        "家庭血压和动态血压分别是多少？", MedicalRiskAssessment()
    )
    evidence = Evidence(
        source_type="document",
        source_id="guide-a",
        origin_document_id="guide-a",
        source_page=16,
        content=(
            "基于家庭血压和动态血压的高血压诊断标准"
            "|血压类型|测量方式|收缩压/舒张压(mmHg)|"
            "|家庭血压|平均值|≥135和/或≥85|"
            "|动态血压|24h平均值|≥130和/或≥80|"
        ),
    )
    evidence_by_item = {}
    for item in plan.must_cover:
        task = next(
            task
            for task in plan.retrieval_tasks
            if task.must_cover_id == item.item_id and task.target_agent == "rag"
        )
        item.support_status = "supported"
        plan.claim_evidence_assessments.append(
            ClaimEvidenceAssessment(
                task_id=task.task_id,
                must_cover_id=item.item_id,
                evidence_id=evidence_identity(evidence),
                support_status="supported",
                support_excerpt=evidence.content,
                applicability_conditions=item.applicability_conditions,
            )
        )
        evidence_by_item[item.item_id] = [("rag", evidence)]
    context = build_answer_generation_context(
        question_plan=plan,
        evidence_by_item=evidence_by_item,
        conflicts=[],
    )

    result = validate_answer_generation(
        "家庭血压诊断阈值为≥135/85mmHg[guide-a p16]。\n"
        "动态血压诊断阈值如下[guide-a p16]：\n"
        "- 24h平均值：≥130和/或≥80mmHg[guide-a p16]。",
        context,
    )

    assert result.valid is False
    assert plan.must_cover[1].item_id in result.missing_must_cover_ids


def test_citation_must_be_locally_bound_to_answer_claim() -> None:
    plan = build_question_plan("高血压患者如何监测？", MedicalRiskAssessment())
    task = next(item for item in plan.retrieval_tasks if item.target_agent == "rag")
    evidence = Evidence(
        source_type="document",
        source_id="guide-a",
        origin_document_id="guide-a",
        content="高血压患者应规律监测血压。",
    )
    plan.must_cover[0].support_status = "supported"
    plan.claim_evidence_assessments.append(
        ClaimEvidenceAssessment(
            task_id=task.task_id,
            must_cover_id=task.must_cover_id,
            evidence_id=evidence_identity(evidence),
            support_status="supported",
            support_excerpt=evidence.content,
        )
    )
    context = build_answer_generation_context(
        question_plan=plan,
        evidence_by_item={task.must_cover_id: [("rag", evidence)]},
        conflicts=[],
    )

    claims = extract_structured_answer_claims(
        "高血压患者应规律监测血压。\n\n[guide-a]", context
    )
    result = validate_answer_generation(
        "高血压患者应规律监测血压。\n\n[guide-a]", context
    )

    assert claims == []
    assert result.valid is False
    assert result.missing_citation_ids == [task.must_cover_id]


def test_post_validation_rejects_uncited_added_medical_fact() -> None:
    plan = build_question_plan("是否推荐家庭血压监测？", MedicalRiskAssessment())
    task = next(item for item in plan.retrieval_tasks if item.target_agent == "rag")
    evidence = Evidence(
        source_type="document",
        source_id="guide-a",
        origin_document_id="guide-a",
        source_page=8,
        content="推荐高血压患者进行家庭血压监测。",
    )
    plan.must_cover[0].support_status = "supported"
    plan.claim_evidence_assessments.append(
        ClaimEvidenceAssessment(
            task_id=task.task_id,
            must_cover_id=task.must_cover_id,
            evidence_id=evidence_identity(evidence),
            support_status="supported",
            support_excerpt=evidence.content,
        )
    )
    context = build_answer_generation_context(
        question_plan=plan,
        evidence_by_item={task.must_cover_id: [("rag", evidence)]},
        conflicts=[],
    )

    result = validate_answer_generation(
        "推荐高血压患者进行家庭血压监测。[guide-a p8]"
        "另外，家庭血压监测还能降低死亡风险。",
        context,
    )
    heading = validate_answer_generation(
        "### 家庭血压监测\n\n" "推荐高血压患者进行家庭血压监测。[guide-a p8]",
        context,
    )

    assert result.valid is False
    assert result.uncited_claims == ["另外，家庭血压监测还能降低死亡风险。"]
    assert heading.valid is True
    assert heading.uncited_claims == []


def test_post_validation_rejects_uncited_fact_without_legacy_keywords() -> None:
    plan = build_question_plan("是否推荐家庭血压监测？", MedicalRiskAssessment())
    task = next(item for item in plan.retrieval_tasks if item.target_agent == "rag")
    evidence = Evidence(
        source_type="document",
        source_id="guide-a",
        origin_document_id="guide-a",
        content="推荐高血压患者进行家庭血压监测。",
    )
    plan.must_cover[0].support_status = "supported"
    plan.claim_evidence_assessments.append(
        ClaimEvidenceAssessment(
            task_id=task.task_id,
            must_cover_id=task.must_cover_id,
            evidence_id=evidence_identity(evidence),
            support_status="supported",
            support_excerpt=evidence.content,
        )
    )
    context = build_answer_generation_context(
        question_plan=plan,
        evidence_by_item={task.must_cover_id: [("rag", evidence)]},
        conflicts=[],
    )

    result = validate_answer_generation(
        "推荐高血压患者进行家庭血压监测。[guide-a]" "此外，它对依从性有益。",
        context,
    )

    assert result.valid is False
    assert result.uncited_claims == ["此外，它对依从性有益。"]


def test_post_validation_blocks_unresolved_conflict() -> None:
    plan = build_question_plan("高血压患者如何监测？", MedicalRiskAssessment())
    task = next(item for item in plan.retrieval_tasks if item.target_agent == "rag")
    evidence = Evidence(
        source_type="document",
        source_id="guide-a",
        origin_document_id="guide-a",
        content="高血压患者应规律监测血压。",
    )
    plan.must_cover[0].support_status = "supported"
    plan.claim_evidence_assessments.append(
        ClaimEvidenceAssessment(
            task_id=task.task_id,
            must_cover_id=task.must_cover_id,
            evidence_id=evidence_identity(evidence),
            support_status="supported",
            support_excerpt=evidence.content,
        )
    )
    context = build_answer_generation_context(
        question_plan=plan,
        evidence_by_item={task.must_cover_id: [("rag", evidence)]},
        conflicts=[
            EvidenceConflict(
                conflict_id="conflict-1",
                claim_key="monitoring",
                values=["推荐", "不推荐"],
                source_ids=["guide-a", "guide-b"],
            )
        ],
    )

    result = validate_answer_generation("应规律监测血压。[guide-a]", context)

    assert result.valid is False
    assert result.unresolved_conflict_ids == ["conflict-1"]


def test_final_prompt_serializes_structured_context() -> None:
    plan = build_question_plan("高血压患者如何监测？", MedicalRiskAssessment())
    context = build_answer_generation_context(
        question_plan=plan,
        evidence_by_item={plan.must_cover[0].item_id: []},
        conflicts=[],
    )
    controller = object.__new__(UnifiedOrchestratorController)

    prompt = controller._build_final_answer_user_prompt(
        question=plan.original_question,
        answer_context=context,
    )

    assert "AnswerGenerationContext" in prompt
    assert '"schema_version":"answer_context.v1"' in prompt.replace(" ", "")
    assert plan.must_cover[0].item_id in prompt
