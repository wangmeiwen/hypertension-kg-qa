"""结构化 Evidence 冲突检测测试。"""

from __future__ import annotations

from app.a2a.conflict_detection import detect_evidence_conflicts
from app.a2a.models import Evidence
from app.a2a.orchestrator.controller import UnifiedOrchestratorController


def _evidence(source_id: str, content: str, page: int = 1) -> Evidence:
    return Evidence(
        source_type="document",
        source_id=source_id,
        page=page,
        content=content,
    )


def test_detects_numeric_conflict_with_sources_and_citations() -> None:
    rows = [
        _evidence("guide-a", "治疗后风险下降 10%。", 12),
        _evidence("guide-b", "治疗后风险下降 20%。", 18),
    ]

    first = detect_evidence_conflicts(rows)
    second = detect_evidence_conflicts(rows)

    assert first == second
    assert len(first) == 1
    conflict = first[0]
    assert conflict.conflict_type == "numeric"
    assert conflict.values == ["10%", "20%"]
    assert conflict.citations == ["[guide-a p12]", "[guide-b p18]"]
    assert conflict.status == "unresolved"


def test_different_applicability_conditions_are_not_treated_as_conflict() -> None:
    rows = [
        _evidence("guide-a", "老年人血压目标为 140 mmHg。"),
        _evidence("guide-b", "孕妇血压目标为 150 mmHg。"),
    ]

    assert detect_evidence_conflicts(rows) == []


def test_different_values_from_same_canonical_source_are_not_cross_source_conflict() -> (
    None
):
    rows = [
        _evidence("guide-a", "血压目标为 130 mmHg。"),
        _evidence("GUIDE A", "血压目标为 140 mmHg。"),
    ]

    assert detect_evidence_conflicts(rows) == []


def test_controller_exposes_structured_conflict_details() -> None:
    controller = object.__new__(UnifiedOrchestratorController)
    observations = [
        {
            "agent": "rag",
            "evidence": [
                _evidence("guide-a", "治疗后风险下降 10%。").model_dump(),
                _evidence("guide-b", "治疗后风险下降 20%。").model_dump(),
            ],
        }
    ]

    readiness = controller._assess_answer_readiness(observations)
    summary = controller._build_evidence_summary(observations)

    assert readiness["ready"] is False
    assert readiness["unresolved_conflicts"] == ["numeric_conflict"]
    assert len(readiness["conflict_details"]) == 1
    assert summary["conflicts"] == readiness["conflict_details"]
