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
    return mode if mode in {
        "memory_answer",
        "memory_augmented_medical",
        "assistant_capability",
        "generic_direct",
        "medical_retrieval",
    } else "medical_retrieval"


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

    response_mode = "medical_retrieval"
    reason = "default_medical_retrieval"

    if "assistant_capability" in preferred_sources or question_mode == "assistant_capability":
        response_mode = "assistant_capability"
        reason = "preferred_assistant_capability"
    elif "memory" in preferred_sources or question_mode in {"memory_lookup", "repeat_medical_question"}:
        response_mode = "memory_answer"
        reason = "preferred_memory"
    elif repeat_detected and memory_supported_points:
        response_mode = "memory_answer"
        reason = "repeat_question_short_circuit"
    elif "direct" in preferred_sources or question_scope in {"administrative", "small_talk"}:
        response_mode = "generic_direct"
        reason = "preferred_direct"
    elif question_scope == "medical_qa" and memory_supported_points:
        if coverage_ratio >= 0.7:
            response_mode = "memory_answer"
            reason = "memory_covers_medical_question"
        elif coverage_ratio > 0.0 or memory_missing_points:
            response_mode = "memory_augmented_medical"
            reason = "memory_partial_medical_support"

    return {
        "response_mode": _normalize_response_mode(response_mode),
        "reason": reason,
        "memory_supported_points": memory_supported_points[:5],
        "memory_missing_points": memory_missing_points[:5],
        "memory_coverage_ratio": round(coverage_ratio, 4),
        "repeat_question_detected": repeat_detected,
    }
