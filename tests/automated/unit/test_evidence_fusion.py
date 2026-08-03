"""跨代理 Evidence 融合测试。"""

from __future__ import annotations

from app.a2a.evidence_fusion import canonical_source_id, fuse_evidence
from app.a2a.models import Evidence
from app.a2a.orchestrator.controller import UnifiedOrchestratorController


def _evidence(
    *,
    source_id: str,
    content: str,
    document_id: str = "",
    score: float | None = None,
) -> Evidence:
    return Evidence(
        source_type="document",
        source_id=source_id,
        document_id=document_id,
        page=12,
        content=content,
        score=score,
        retrieval_method="dense",
    )


def test_fusion_deduplicates_same_document_and_preserves_support_provenance() -> None:
    first = _evidence(
        source_id="guide.pdf",
        document_id="Guide-2024",
        content="高血压患者应规律监测血压。",
        score=0.7,
    )
    second = _evidence(
        source_id="GUIDE.PDF",
        document_id="guide 2024",
        content="高血压患者应规律监测血压",
        score=0.9,
    )

    result = fuse_evidence([("rag", first), ("history", second)])

    assert result.raw_evidence_count == 2
    assert result.duplicate_count == 1
    assert result.distinct_source_count == 1
    assert len(result.evidence) == 1
    fused = result.evidence[0]
    assert fused.score == 0.9
    assert fused.provenance["supporting_agents"] == ["history", "rag"]
    assert fused.provenance["duplicate_count"] == 1
    assert fused.provenance["canonical_source_id"] == canonical_source_id(first)


def test_fusion_tracks_independent_sources_for_same_claim() -> None:
    statement = "家庭血压监测有助于评估治疗效果。"
    first = _evidence(source_id="guide-a", content=statement)
    second = _evidence(source_id="guide-b", content=statement)

    result = fuse_evidence([("rag", first), ("kg", second)])

    assert result.duplicate_count == 0
    assert result.distinct_source_count == 2
    assert result.corroborated_claim_count == 1
    assert all(
        item.provenance["independent_source_count"] == 2 for item in result.evidence
    )


def test_canonical_source_identity_is_shared_across_kg_and_rag() -> None:
    rag = _evidence(
        source_id="guide.pdf",
        document_id="Guide-2024",
        content="文档证据",
    )
    kg = Evidence(
        source_type="knowledge_graph",
        source_id="guide 2024",
        content="图谱证据",
    )

    assert canonical_source_id(rag) == canonical_source_id(kg)
    result = fuse_evidence([("rag", rag), ("kg", kg)])
    assert result.distinct_source_count == 1


def test_controller_summary_and_final_prompt_use_fused_evidence() -> None:
    controller = object.__new__(UnifiedOrchestratorController)
    evidence = _evidence(
        source_id="guide.pdf",
        content="高血压患者应规律监测血压。",
    ).model_dump()
    observations = [
        {"agent": "rag", "subquery": "如何监测", "evidence": [evidence]},
        {"agent": "history", "subquery": "如何监测", "evidence": [evidence]},
    ]

    summary = controller._build_evidence_summary(observations)
    prompt = controller._build_final_answer_user_prompt(
        question="如何监测血压？",
        qa_memory_text="无",
        retrieval_index_text="无",
        tool_observations=observations,
        draft_answer="",
    )

    assert summary["raw_evidence_count"] == 2
    assert summary["evidence_count"] == 1
    assert summary["duplicate_count"] == 1
    assert len(summary["fused_evidence"]) == 1
    assert prompt.count("高血压患者应规律监测血压") == 1
