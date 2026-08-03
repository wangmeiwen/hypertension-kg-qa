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
