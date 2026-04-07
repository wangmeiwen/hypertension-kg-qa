"""会话记忆优先路由。"""

from __future__ import annotations

from typing import Any


def _extract_terms(text: str) -> set[str]:
    """提取并返回关键信息。
    
    Args:
        text: 输入文本。
    
    Returns:
        set[str]: 执行结果。
    """
    lowered = str(text or "").lower()
    tokens: set[str] = set()
    current = []
    for char in lowered:
        if char.isalnum() or ("\u4e00" <= char <= "\u9fff"):
            current.append(char)
            continue
        if len(current) >= 2:
            tokens.add("".join(current))
        current = []
    if len(current) >= 2:
        tokens.add("".join(current))
    return tokens


def _estimate_overlap(question: str, rows: list[str]) -> float:
    """估算并返回计算结果。
    
    Args:
        question: 用户输入问题文本。
        rows: 输入数据列表。
    
    Returns:
        float: 数值结果。
    """
    question_terms = _extract_terms(question)
    if not question_terms:
        return 0.0
    best = 0.0
    for row in rows:
        row_terms = _extract_terms(row)
        if not row_terms:
            continue
        overlap = len(question_terms.intersection(row_terms))
        best = max(best, float(overlap) / float(max(1, min(len(question_terms), len(row_terms)))))
    return round(best, 4)


def _sanitize_list(value: Any, *, max_items: int = 5) -> list[str]:
    """清洗输入并返回可用结果。
    
    Args:
        value: 待处理输入值。
        max_items: 函数输入参数。
    
    Returns:
        list[str]: 处理后的列表结果。
    """
    if not isinstance(value, list):
        return []
    rows: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        rows.append(text)
        if len(rows) >= max_items:
            break
    return rows


def _normalize_response_mode(mode: str) -> str:
    """规范化输入并返回标准化结果。
    
    Args:
        mode: 函数输入参数。
    
    Returns:
        str: 文本结果。
    """
    return mode if mode in {"direct_answer", "retrieval_required"} else "retrieval_required"


def _answer_source_from_reason(reason: str) -> str:
    token = str(reason or "").strip()
    if token in {"memory_lookup", "repeat_answer", "memory_full_coverage", "memory_unavailable"}:
        return "memory"
    if token == "capability_direct":
        return "capability"
    if token == "context_direct":
        return "generic"
    return "retrieval"


def route_by_memory_capability(
    *,
    question: str,
    resolved_query: dict[str, Any],
    memory_context: dict[str, Any],
) -> dict[str, Any]:
    """根据当前状态计算下一步路由。
    
    Args:
        question: 用户输入问题文本。
        resolved_query: 问题改写后的结构化数据。
        memory_context: 会话记忆上下文。
    
    Returns:
        dict[str, Any]: 结构化结果数据。
    """
    summary = memory_context.get("summary") if isinstance(memory_context.get("summary"), dict) else {}
    retrieval_summary = (
        memory_context.get("retrieval_summary") if isinstance(memory_context.get("retrieval_summary"), dict) else {}
    )
    metadata = memory_context.get("metadata") if isinstance(memory_context.get("metadata"), dict) else {}
    topic_shift = (
        memory_context.get("topic_shift_signal") if isinstance(memory_context.get("topic_shift_signal"), dict) else {}
    )

    preferred_sources = _sanitize_list(resolved_query.get("preferred_sources"), max_items=3)
    question_mode = str(resolved_query.get("question_mode") or "").strip()
    question_scope = str(topic_shift.get("question_scope") or "unknown")

    answered_points = _sanitize_list(summary.get("answered_points"), max_items=5)
    open_questions = _sanitize_list(summary.get("open_questions"), max_items=5)
    evidence_gaps = _sanitize_list(retrieval_summary.get("evidence_gaps"), max_items=5)
    covered_topics = _sanitize_list(retrieval_summary.get("covered_topics"), max_items=5)

    repeat_detected = bool(metadata.get("repeat_question_detected"))
    repeat_candidate = str(metadata.get("repeat_answer_candidate") or "").strip()
    must_cover = _sanitize_list(resolved_query.get("must_cover"), max_items=8)

    memory_supported_points = answered_points[:]
    if repeat_candidate and repeat_candidate not in memory_supported_points:
        memory_supported_points = [repeat_candidate, *memory_supported_points]
    has_memory_direct_signal = bool(memory_supported_points)
    is_memory_lookup = question_mode == "memory_lookup"
    is_repeat_medical = question_mode == "repeat_medical_question"

    candidate_rows = [*answered_points, *covered_topics]
    coverage_hits = 0
    for point in must_cover:
        overlap = _estimate_overlap(point, candidate_rows)
        if overlap >= 0.45:
            coverage_hits += 1
    coverage_ratio = float(coverage_hits) / float(max(1, len(must_cover))) if must_cover else (1.0 if candidate_rows else 0.0)

    memory_missing_points = [point for point in must_cover if point not in covered_topics]
    for point in [*open_questions, *evidence_gaps]:
        if point and point not in memory_missing_points:
            memory_missing_points.append(point)

    response_mode = "retrieval_required"
    direct_reason = ""
    retrieval_reason = "new_medical_question"

    if "capability" in preferred_sources or question_mode == "capability_query":
        response_mode = "direct_answer"
        direct_reason = "capability_direct"
        retrieval_reason = ""
    elif "memory" in preferred_sources or question_mode in {"memory_lookup", "repeat_medical_question"}:
        if is_memory_lookup:
            response_mode = "direct_answer"
            direct_reason = "memory_lookup"
            retrieval_reason = ""
        elif is_repeat_medical and has_memory_direct_signal and (repeat_detected or coverage_ratio > 0.0):
            response_mode = "direct_answer"
            direct_reason = "repeat_answer"
            retrieval_reason = ""
        else:
            response_mode = "retrieval_required"
            retrieval_reason = "memory_insufficient"
    elif repeat_detected and memory_supported_points:
        response_mode = "direct_answer"
        direct_reason = "repeat_answer"
        retrieval_reason = ""
    elif "direct" in preferred_sources or question_scope in {"administrative", "small_talk"}:
        response_mode = "direct_answer"
        direct_reason = "context_direct"
        retrieval_reason = ""
    elif question_scope == "medical_qa" and memory_supported_points:
        if must_cover and coverage_ratio >= 1.0:
            response_mode = "direct_answer"
            direct_reason = "memory_full_coverage"
            retrieval_reason = ""
        elif coverage_ratio > 0.0 or memory_missing_points:
            response_mode = "retrieval_required"
            retrieval_reason = "memory_gap_followup"

    return {
        "response_mode": _normalize_response_mode(response_mode),
        "reason": direct_reason or retrieval_reason,
        "direct_reason": direct_reason,
        "retrieval_reason": retrieval_reason,
        "answer_source": _answer_source_from_reason(direct_reason),
        "memory_supported_points": memory_supported_points[:5],
        "memory_missing_points": memory_missing_points[:5],
        "memory_coverage_ratio": round(coverage_ratio, 4),
        "repeat_question_detected": repeat_detected,
    }
