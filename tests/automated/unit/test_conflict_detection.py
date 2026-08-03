"""结构化 Evidence 冲突检测测试。"""

from __future__ import annotations

from app.a2a.conflict_detection import (
    detect_evidence_conflicts,
    extract_claim_frames,
)
from app.a2a.models import Evidence
from app.a2a.orchestrator.controller import UnifiedOrchestratorController


def _evidence(
    source_id: str,
    content: str,
    page: int = 1,
    *,
    guideline_id: str = "",
) -> Evidence:
    return Evidence(
        source_type="document",
        source_id=source_id,
        origin_document_id=source_id,
        origin_guideline_id=guideline_id,
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
    assert {frame.topic for frame in conflict.claim_frames} == {"risk_reduction"}
    assert conflict.status == "unresolved"


def test_different_applicability_conditions_are_not_treated_as_conflict() -> None:
    rows = [
        _evidence("guide-a", "老年人血压目标为 140 mmHg。"),
        _evidence("guide-b", "孕妇血压目标为 150 mmHg。"),
    ]

    assert detect_evidence_conflicts(rows) == []


def test_different_measurement_contexts_are_not_treated_as_conflict() -> None:
    rows = [
        _evidence("guide-a", "诊室血压目标为 140 mmHg。"),
        _evidence("guide-b", "家庭血压目标为 135 mmHg。"),
    ]

    frames = extract_claim_frames(rows)

    assert {tuple(frame.measurement_context) for frame in frames} == {
        ("clinic",),
        ("home",),
    }
    assert detect_evidence_conflicts(rows) == []


def test_compatible_units_are_normalized_before_comparison() -> None:
    equivalent = [
        _evidence("guide-a", "血压目标为 140 mmHg。"),
        _evidence("guide-b", "血压目标为 18.7 kPa。"),
    ]
    different = [
        _evidence("guide-a", "血压目标为 140 mmHg。"),
        _evidence("guide-b", "血压目标为 18 kPa。"),
    ]

    assert detect_evidence_conflicts(equivalent) == []
    conflicts = detect_evidence_conflicts(different)
    assert len(conflicts) == 1
    assert conflicts[0].conflict_type == "numeric"
    assert {frame.normalized_unit for frame in conflicts[0].claim_frames} == {"mmhg"}


def test_incompatible_units_are_reported_separately() -> None:
    rows = [
        _evidence("guide-a", "血压目标为 140 mmHg。"),
        _evidence("guide-b", "血压目标为 10 mg。"),
    ]

    conflicts = detect_evidence_conflicts(rows)

    assert len(conflicts) == 1
    assert conflicts[0].conflict_type == "unit"
    assert {frame.normalized_unit for frame in conflicts[0].claim_frames} == {
        "mmhg",
        "mg",
    }


def test_detects_recommendation_polarity_conflict() -> None:
    rows = [
        _evidence("guide-a", "老年人推荐使用 ACEI 治疗。"),
        _evidence("guide-b", "老年人不推荐使用 ACEI 治疗。"),
    ]

    conflicts = detect_evidence_conflicts(rows)

    assert len(conflicts) == 1
    assert conflicts[0].conflict_type == "recommendation"
    assert conflicts[0].values == ["discourage", "recommend"]
    assert {frame.intervention for frame in conflicts[0].claim_frames} == {"acei"}


def test_contraindication_conflicts_with_positive_recommendation() -> None:
    rows = [
        _evidence("guide-a", "孕妇可考虑使用 ACEI。"),
        _evidence("guide-b", "孕妇禁忌使用 ACEI。"),
    ]

    conflicts = detect_evidence_conflicts(rows)

    assert len(conflicts) == 1
    assert conflicts[0].conflict_type == "contraindication"
    assert conflicts[0].values == ["consider", "contraindicated"]


def test_recommendations_for_different_populations_do_not_conflict() -> None:
    rows = [
        _evidence("guide-a", "老年人推荐使用 ACEI 治疗。"),
        _evidence("guide-b", "孕妇不推荐使用 ACEI 治疗。"),
    ]

    assert detect_evidence_conflicts(rows) == []


def test_same_source_recommendation_difference_is_not_cross_source_conflict() -> None:
    rows = [
        _evidence("guide-a", "推荐使用 ACEI 治疗。"),
        _evidence("GUIDE A", "不推荐使用 ACEI 治疗。"),
    ]

    assert detect_evidence_conflicts(rows) == []


def test_detects_treatment_priority_conflict() -> None:
    rows = [
        _evidence("guide-a", "老年人 ACEI 为一线治疗。"),
        _evidence("guide-b", "老年人 ACEI 为二线治疗。"),
    ]

    conflicts = detect_evidence_conflicts(rows)

    priority_conflict = next(
        item for item in conflicts if item.conflict_type == "treatment_priority"
    )
    assert priority_conflict.values == ["first_line", "second_line"]


def test_detects_evidence_grade_conflict() -> None:
    rows = [
        _evidence("guide-a", "ACEI 治疗证据等级 A。"),
        _evidence("guide-b", "ACEI 治疗证据等级 C。"),
    ]

    conflicts = detect_evidence_conflicts(rows)

    grade_conflict = next(
        item for item in conflicts if item.conflict_type == "evidence_grade"
    )
    assert grade_conflict.values == ["A", "C"]


def test_treatment_priority_for_different_populations_does_not_conflict() -> None:
    rows = [
        _evidence("guide-a", "老年人 ACEI 为一线治疗。"),
        _evidence("guide-b", "孕妇 ACEI 为二线治疗。"),
    ]

    assert detect_evidence_conflicts(rows) == []


def test_detects_changed_conclusion_across_guideline_versions() -> None:
    rows = [
        _evidence(
            "guide-2020",
            "推荐使用 ACEI 治疗。",
            guideline_id="china-hypertension-2020",
        ),
        _evidence(
            "guide-2024",
            "不推荐使用 ACEI 治疗。",
            guideline_id="china-hypertension-2024",
        ),
    ]

    conflicts = detect_evidence_conflicts(rows)

    version_conflict = next(
        item for item in conflicts if item.conflict_type == "guideline_version"
    )
    assert {frame.guideline_family for frame in version_conflict.claim_frames} == {
        "chinahypertension"
    }
    assert {frame.guideline_version for frame in version_conflict.claim_frames} == {
        "2020",
        "2024",
    }


def test_same_conclusion_across_guideline_versions_is_not_a_version_conflict() -> None:
    rows = [
        _evidence(
            "guide-2020",
            "推荐使用 ACEI 治疗。",
            guideline_id="china-hypertension-2020",
        ),
        _evidence(
            "guide-2024",
            "推荐使用 ACEI 治疗。",
            guideline_id="china-hypertension-2024",
        ),
    ]

    conflicts = detect_evidence_conflicts(rows)

    assert all(item.conflict_type != "guideline_version" for item in conflicts)


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


def test_controller_blocks_recommendation_conflict() -> None:
    controller = object.__new__(UnifiedOrchestratorController)
    observations = [
        {
            "agent": "rag",
            "evidence": [
                _evidence("guide-a", "推荐使用 ACEI 治疗。").model_dump(),
                _evidence("guide-b", "不推荐使用 ACEI 治疗。").model_dump(),
            ],
        }
    ]

    readiness = controller._assess_answer_readiness(observations)

    assert readiness["ready"] is False
    assert readiness["unresolved_conflicts"] == ["recommendation_conflict"]
    assert readiness["conflict_details"][0]["conflict_type"] == "recommendation"
