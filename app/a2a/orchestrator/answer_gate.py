"""终答门控与生成模块。"""

from __future__ import annotations

from typing import Any

from app.a2a.models import FinalAnswer


_INSUFFICIENT_MESSAGE = (
    "我目前没有查到足够支持这个问题的资料，不能给出可靠结论。你可以补充具体人群、血压数值、症状或用药情况，我再帮你梳理。"
)

_MEDICAL_DISCLAIMER = "以上内容仅供参考，不构成诊断或治疗建议，不能替代专业医生意见；如有不适或紧急情况，请及时就医。"


def _append_medical_disclaimer(answer: str) -> str:
    """执行append_medical_disclaimer流程并返回结果。
    
    Args:
        answer: 函数输入参数。
    
    Returns:
        str: 文本结果。
    """
    text = str(answer or "").replace(_MEDICAL_DISCLAIMER, "").strip()
    if text:
        text = "\n".join(
            line
            for line in text.splitlines()
            if "免责声明" not in line
        ).strip()
    if not text:
        return _MEDICAL_DISCLAIMER
    return f"{text}\n\n{_MEDICAL_DISCLAIMER}"


def assess_answerability(
    *,
    evidence_bundle: dict[str, Any],
    gate_result: dict[str, Any],
    coverage_map: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """执行assess_answerability流程并返回结果。
    
    Args:
        evidence_bundle: 融合后的证据集合。
        gate_result: 门控评估结果。
        coverage_map: 子任务覆盖度映射。
    
    Returns:
        dict[str, Any]: 结构化结果数据。
    """
    should_backtrack = bool((gate_result or {}).get("should_backtrack"))
    sufficient_raw = (gate_result or {}).get("sufficient")
    sufficient = bool(sufficient_raw) if sufficient_raw is not None else True
    raw_gate_passed = bool((gate_result or {}).get("gate_passed"))
    gate_passed = raw_gate_passed and (not should_backtrack) and sufficient
    confidence = float((gate_result or {}).get("confidence") or 0.0)
    missing_points_obj = (gate_result or {}).get("missing_points")
    missing_points = missing_points_obj if isinstance(missing_points_obj, list) else []

    kg_rows = (evidence_bundle or {}).get("kg_evidence")
    rag_rows = (evidence_bundle or {}).get("rag_evidence")
    kg_count = len(kg_rows) if isinstance(kg_rows, list) else 0
    rag_count = len(rag_rows) if isinstance(rag_rows, list) else 0
    final_evidence_count = kg_count + rag_count

    direct_support = False
    for row in (coverage_map or {}).values():
        if isinstance(row, dict) and bool(row.get("direct_support")):
            direct_support = True
            break

    if final_evidence_count <= 0:
        mode = "none"
        reason = "no_evidence"
    else:
        mode = "direct" if direct_support else "partial"
        if gate_passed:
            reason = "gate_passed_with_direct_support" if direct_support else "gate_passed_with_indirect_support"
        elif should_backtrack:
            reason = "gate_failed_backtrack_required"
        elif not sufficient:
            reason = "gate_failed_not_sufficient"
        else:
            reason = "gate_failed"

    return {
        "answer_mode": mode,
        "gate_passed": gate_passed,
        "should_backtrack": should_backtrack,
        "confidence_cap": min(confidence, 0.45) if mode == "none" else confidence,
        "missing_points": missing_points,
        "direct_support": direct_support,
        "evidence_count": final_evidence_count,
        "final_evidence_count": final_evidence_count,
        "reason": reason,
    }


def compose_final_answer(
    *,
    question: str,
    evidence_bundle: dict[str, Any],
    gate_result: dict[str, Any],
    answerability: dict[str, Any],
    trace: list[str],
    answer_text: str,
    writer_metadata: dict[str, Any] | None = None,
    writer_token_usage: dict[str, Any] | None = None,
    writer_packed_evidence: dict[str, Any] | None = None,
) -> FinalAnswer:
    """执行compose_final_answer流程并返回结果。
    
    Args:
        question: 用户输入问题文本。
        evidence_bundle: 融合后的证据集合。
        gate_result: 门控评估结果。
        answerability: 函数输入参数。
        trace: 处理链路追踪信息。
        answer_text: 终答文本内容。
        writer_metadata: 写作器元数据。
        writer_token_usage: 写作阶段的 token 统计。
        writer_packed_evidence: 写作器筛选后的证据摘要。
    
    Returns:
        FinalAnswer: 执行结果。
    """
    kg_rows = (evidence_bundle or {}).get("kg_evidence")
    rag_rows = (evidence_bundle or {}).get("rag_evidence")
    kg_count = len(kg_rows) if isinstance(kg_rows, list) else 0
    rag_count = len(rag_rows) if isinstance(rag_rows, list) else 0
    total = kg_count + rag_count

    mode = str((answerability or {}).get("answer_mode") or "none")
    confidence = float((answerability or {}).get("confidence_cap") or 0.0)

    answer = str(answer_text or "").strip()
    if not answer:
        if mode == "none":
            answer = _INSUFFICIENT_MESSAGE
        else:
            answer = (
                f"关于“{str(question or '').strip()}”，我查到了一些相关资料，但当前生成回答时出现异常。"
                "可以先把现有资料作为背景参考，后续建议结合具体病情和专业医生意见进一步判断。"
            )
    answer = _append_medical_disclaimer(answer)

    writer_meta = writer_metadata if isinstance(writer_metadata, dict) else {}
    writer_tokens = writer_token_usage if isinstance(writer_token_usage, dict) else {}
    prompt_version = str(writer_meta.get("prompt_version") or "")
    writer_used_raw = str(writer_meta.get("writer_used") or "").strip().lower()
    if writer_used_raw in {"llm", "fallback", "template", "memory", "direct"}:
        writer_used = writer_used_raw
    else:
        writer_used = "template" if mode == "none" else "llm"
    writer_fallback_reason = str(writer_meta.get("writer_fallback_reason") or "") if writer_used == "fallback" else ""

    coverage_intervention = float((gate_result or {}).get("coverage_intervention") or (gate_result or {}).get("hop_coverage") or 0.0)
    gate_payload = gate_result if isinstance(gate_result, dict) else {}
    raw_coverage_comparator = gate_payload.get("coverage_comparator") if "coverage_comparator" in gate_payload else None
    raw_coverage_outcomes = gate_payload.get("coverage_outcomes") if "coverage_outcomes" in gate_payload else None

    missing_points_obj = (answerability or {}).get("missing_points")
    missing_points = missing_points_obj if isinstance(missing_points_obj, list) else []
    packed_evidence = writer_packed_evidence if isinstance(writer_packed_evidence, dict) else {}
    packed_missing = packed_evidence.get("missing_points") if isinstance(packed_evidence.get("missing_points"), list) else []

    evidence_summary = {
        "evidence_count": total,
        "kg_evidence_count": kg_count,
        "rag_evidence_count": rag_count,
        "conflict_count": int((gate_result or {}).get("conflict_count") or 0),
        "hop_coverage": float((gate_result or {}).get("hop_coverage") or 0.0),
        "uncovered_points": (gate_result or {}).get("uncovered_points") or [],
        "answer_mode": mode,
        "writer_used": writer_used,
        "writer_fallback_reason": writer_fallback_reason,
        "prompt_version": prompt_version,
        "direct_comparison_support": bool((answerability or {}).get("direct_support")),
        "coverage_intervention": coverage_intervention,
        "missing_points": packed_missing or missing_points,
        "supported_points": packed_evidence.get("supported_points") if isinstance(packed_evidence.get("supported_points"), list) else [],
        "indirect_points": packed_evidence.get("indirect_points") if isinstance(packed_evidence.get("indirect_points"), list) else [],
        "evidence_citations": packed_evidence.get("evidence_citations") if isinstance(packed_evidence.get("evidence_citations"), list) else [],
        "selected_evidence_count": int(packed_evidence.get("selected_evidence_count") or 0),
        "high_risk": bool((gate_result or {}).get("high_risk")),
        "high_risk_tokens": (gate_result or {}).get("high_risk_tokens") or [],
        "disambiguation_ambiguous_count": int((gate_result or {}).get("disambiguation_ambiguous_count") or 0),
        "community_count": int((gate_result or {}).get("community_count") or 0),
        "top_community_score": float((gate_result or {}).get("top_community_score") or 0.0),
        "force_supplement_required": bool((gate_result or {}).get("force_supplement_required")),
        "writer_prompt_tokens": int(writer_tokens.get("prompt_tokens") or 0),
        "writer_completion_tokens": int(writer_tokens.get("completion_tokens") or 0),
        "writer_total_tokens": int(writer_tokens.get("total_tokens") or 0),
        "answer_generation": {
            "mode": mode,
            "writer": writer_used,
            "prompt_version": prompt_version,
        },
    }

    if raw_coverage_comparator is not None:
        evidence_summary["coverage_comparator"] = float(raw_coverage_comparator)
    if raw_coverage_outcomes is not None:
        evidence_summary["coverage_outcomes"] = float(raw_coverage_outcomes)

    return FinalAnswer(
        answer=answer,
        confidence=max(0.0, min(confidence, 1.0)),
        evidence_summary=evidence_summary,
        trace=trace,
    )
