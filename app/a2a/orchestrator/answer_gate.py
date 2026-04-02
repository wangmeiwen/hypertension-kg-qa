"""终答门控与生成模块。"""

from __future__ import annotations

from typing import Any

from app.a2a.models import FinalAnswer


_INSUFFICIENT_MESSAGE = (
    "数据库未检索到足够数据支撑回答，根据通识回答为：当前信息无法形成可靠结论，建议补充更具体的问题条件后重试，例如明确人群、指标阈值或治疗场景。"
)


def assess_answerability(
    *,
    gate_result: dict[str, Any],
    coverage_map: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    should_backtrack = bool((gate_result or {}).get("should_backtrack"))
    sufficient_raw = (gate_result or {}).get("sufficient")
    sufficient = bool(sufficient_raw) if sufficient_raw is not None else True
    raw_gate_passed = bool((gate_result or {}).get("gate_passed"))
    gate_passed = raw_gate_passed and (not should_backtrack) and sufficient
    confidence = float((gate_result or {}).get("confidence") or 0.0)
    missing_points_obj = (gate_result or {}).get("missing_points")
    missing_points = missing_points_obj if isinstance(missing_points_obj, list) else []

    direct_support = False
    for row in (coverage_map or {}).values():
        if isinstance(row, dict) and bool(row.get("direct_support")):
            direct_support = True
            break

    if should_backtrack or (not sufficient):
        mode = "insufficient"
        reason = "gate_backtrack_required" if should_backtrack else "gate_not_sufficient"
    elif direct_support:
        mode = "direct"
        reason = "global_gate_passed_with_direct_support"
    else:
        mode = "conservative"
        reason = "global_gate_passed"

    return {
        "answer_mode": mode,
        "gate_passed": gate_passed,
        "should_backtrack": should_backtrack,
        "confidence_cap": min(confidence, 0.45) if mode == "insufficient" else confidence,
        "missing_points": missing_points,
        "direct_support": direct_support,
        "reason": reason,
    }


def compose_final_answer(
    *,
    question: str,
    evidence_bundle: dict[str, Any],
    gate_result: dict[str, Any],
    answerability: dict[str, Any],
    trace: list[str],
) -> FinalAnswer:
    kg_rows = (evidence_bundle or {}).get("kg_evidence")
    rag_rows = (evidence_bundle or {}).get("rag_evidence")
    kg_count = len(kg_rows) if isinstance(kg_rows, list) else 0
    rag_count = len(rag_rows) if isinstance(rag_rows, list) else 0
    gate_total_raw = (gate_result or {}).get("evidence_count")
    if gate_total_raw is None:
        total = kg_count + rag_count
    else:
        try:
            total = int(gate_total_raw)
        except (TypeError, ValueError):
            total = kg_count + rag_count

    mode = str((answerability or {}).get("answer_mode") or "insufficient")
    confidence = float((answerability or {}).get("confidence_cap") or 0.0)

    if mode == "insufficient":
        answer = _INSUFFICIENT_MESSAGE
    elif mode == "direct":
        answer = (
            f"问题：{str(question or '').strip()}\n"
            f"结论：当前证据可直接支持回答。\n"
            f"证据概览：图谱证据 {kg_count} 条，文本证据 {rag_count} 条。"
        )
    else:
        answer = (
            f"问题：{str(question or '').strip()}\n"
            f"结论：当前证据可提供保守回答，建议结合具体场景复核。\n"
            f"证据概览：图谱证据 {kg_count} 条，文本证据 {rag_count} 条。"
        )

    missing_points_obj = (answerability or {}).get("missing_points")
    missing_points = missing_points_obj if isinstance(missing_points_obj, list) else []

    return FinalAnswer(
        answer=answer,
        confidence=max(0.0, min(confidence, 1.0)),
        evidence_summary={
            "evidence_count": total,
            "kg_evidence_count": kg_count,
            "rag_evidence_count": rag_count,
            "conflict_count": int((gate_result or {}).get("conflict_count") or 0),
            "hop_coverage": float((gate_result or {}).get("hop_coverage") or 0.0),
            "uncovered_points": (gate_result or {}).get("uncovered_points") or [],
            "direct_comparison_support": bool((answerability or {}).get("direct_support")),
            "coverage_intervention": float((gate_result or {}).get("hop_coverage") or 0.0),
            "coverage_comparator": 0.0,
            "coverage_outcomes": 0.0,
            "missing_points": missing_points,
            "high_risk": bool((gate_result or {}).get("high_risk")),
            "high_risk_tokens": (gate_result or {}).get("high_risk_tokens") or [],
            "disambiguation_ambiguous_count": int((gate_result or {}).get("disambiguation_ambiguous_count") or 0),
            "community_count": int((gate_result or {}).get("community_count") or 0),
            "top_community_score": float((gate_result or {}).get("top_community_score") or 0.0),
            "force_supplement_required": bool((gate_result or {}).get("force_supplement_required")),
        },
        trace=trace,
    )
