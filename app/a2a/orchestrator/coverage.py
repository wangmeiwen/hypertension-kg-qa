"""任务覆盖度评估模块。"""

from __future__ import annotations

from typing import Any

from app.a2a.models import SubTask


_SOURCE_POINT_HINTS = ("source", "citation", "quote", "evidence_source", "原文", "出处", "引用", "文献", "来源")
_OUTCOME_POINT_HINTS = (
    "outcome",
    "efficacy",
    "safety",
    "protection",
    "renal",
    "cardio",
    "疗效",
    "效果",
    "降压",
    "靶器官",
    "保护",
    "安全",
    "不良",
    "风险",
    "结局",
)


def _evidence_text(row: dict[str, Any]) -> str:
    """执行evidence_text流程并返回结果。
    
    Args:
        row: 输入数据项。
    
    Returns:
        str: 文本结果。
    """
    if not isinstance(row, dict):
        return ""
    entity = row.get("entity") if isinstance(row.get("entity"), dict) else {}
    if entity:
        return str(entity.get("text") or entity.get("name") or "").strip()
    relation = row.get("relationship") if isinstance(row.get("relationship"), dict) else {}
    if relation:
        src = str(relation.get("source_name") or "").strip()
        rel = str(relation.get("relation_type") or "").strip()
        tgt = str(relation.get("target_name") or "").strip()
        text = str(relation.get("text") or "").strip()
        return f"{src} {rel} {tgt} {text}".strip()
    hit = row.get("hit") if isinstance(row.get("hit"), dict) else {}
    if hit:
        return str(hit.get("text") or hit.get("parent_content") or hit.get("content") or "").strip()
    record = row.get("record") if isinstance(row.get("record"), dict) else {}
    if record:
        return str(record)
    return ""


def _is_source_point(point: str) -> bool:
    """判断条件是否成立。
    
    Args:
        point: 函数输入参数。
    
    Returns:
        bool: 是否满足条件。
    """
    token = str(point or "").strip().lower()
    if not token:
        return False
    return any(hint in token for hint in _SOURCE_POINT_HINTS)


def _is_outcome_point(point: str) -> bool:
    """判断条件是否成立。
    
    Args:
        point: 函数输入参数。
    
    Returns:
        bool: 是否满足条件。
    """
    token = str(point or "").strip().lower()
    if not token:
        return False
    return any(hint in token for hint in _OUTCOME_POINT_HINTS)


def _clean_tokens(values: list[str]) -> list[str]:
    """执行clean_tokens流程并返回结果。
    
    Args:
        values: 函数输入参数。
    
    Returns:
        list[str]: 处理后的列表结果。
    """
    rows: list[str] = []
    for value in values:
        token = str(value or "").strip()
        if not token:
            continue
        rows.append(token)
    return rows


def _contains_any(text: str, tokens: list[str]) -> bool:
    """执行contains_any流程并返回结果。
    
    Args:
        text: 输入文本。
        tokens: 函数输入参数。
    
    Returns:
        bool: 是否满足条件。
    """
    if not tokens:
        return False
    return any(token and token in text for token in tokens)


def _resolve_expected_points(task: SubTask, must_cover: list[str]) -> list[str]:
    """解析并确定expected_points相关信息并返回结果。
    
    Args:
        task: 当前处理任务。
        must_cover: 函数输入参数。
    
    Returns:
        list[str]: 处理后的列表结果。
    """
    required = set(task.evidence_type_required)
    if "relation" in required and "text_quote" not in required:
        return [point for point in must_cover if not _is_source_point(point)]
    return must_cover


def evaluate_task_coverage(task: SubTask, evidence_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """执行evaluate_task_coverage流程并返回结果。
    
    Args:
        task: 当前处理任务。
        evidence_rows: 函数输入参数。
    
    Returns:
        dict[str, Any]: 结构化结果数据。
    """
    must_cover = [str(item or "").strip() for item in task.must_cover if str(item or "").strip()]
    expected_points = _resolve_expected_points(task, must_cover)
    texts = [_evidence_text(row) for row in evidence_rows if _evidence_text(row)]
    retry_context = task.retry_context if isinstance(task.retry_context, dict) else {}
    must_cover_mapping = str(retry_context.get("must_cover_mapping") or "").strip()

    core_entities = _clean_tokens(list(task.entities or []))
    key_points = _clean_tokens(expected_points)
    query_terms = _clean_tokens(list(task.query_terms or []))
    comparison_mode = str(task.intent or "").strip().lower() == "comparison"

    outcome_points = _clean_tokens([
        point for point in [*must_cover, *query_terms] if _is_outcome_point(point)
    ])
    comparator_terms = core_entities if core_entities else query_terms
    if comparison_mode and not outcome_points:
        outcome_points = key_points if key_points else query_terms

    covered: list[str] = []
    for point in expected_points:
        if any(point in text for text in texts):
            covered.append(point)

    high_evidence_count = 0
    for text in texts:
        if comparison_mode:
            comparator_hit = _contains_any(text, comparator_terms)
            outcome_hit = _contains_any(text, outcome_points)
            if comparator_hit and outcome_hit:
                high_evidence_count += 1
        else:
            entity_ok = _contains_any(text, core_entities) if core_entities else False
            key_point_ok = _contains_any(text, key_points) if key_points else False
            if core_entities and key_points:
                if entity_ok and key_point_ok:
                    high_evidence_count += 1
            elif key_points and key_point_ok:
                high_evidence_count += 1
            elif core_entities and entity_ok:
                high_evidence_count += 1
            else:
                # 兜底：无结构化约束时至少命中两个 query terms
                hit_count = sum(1 for token in query_terms if token and token in text)
                if hit_count >= 2:
                    high_evidence_count += 1

    direct_support = high_evidence_count > 0

    if not expected_points and must_cover_mapping == "unmapped":
        status = "partial" if bool(texts) else "none"
        binding_insufficient = bool(texts) and high_evidence_count == 0
        low_signal = bool(texts) and (binding_insufficient or not direct_support)
        return {
            "coverage_status": status,
            "coverage_ratio": 0.0,
            "covered_points": covered,
            "missing_points": [],
            "expected_points": expected_points,
            "direct_support": direct_support,
            "high_evidence_count": int(high_evidence_count),
            "binding_insufficient": bool(binding_insufficient),
            "low_signal": bool(low_signal),
            "evidence_count": len(evidence_rows),
        }

    coverage_ratio = float(len(covered)) / float(len(expected_points)) if expected_points else 1.0
    missing = [point for point in expected_points if point not in covered]

    if coverage_ratio >= 1.0 and direct_support:
        status = "strong"
    elif coverage_ratio > 0.0 or bool(texts):
        status = "partial"
    else:
        status = "none"

    binding_insufficient = bool(texts) and status != "strong" and high_evidence_count == 0
    low_signal = bool(texts) and (binding_insufficient or not direct_support)

    return {
        "coverage_status": status,
        "coverage_ratio": round(coverage_ratio, 4),
        "covered_points": covered,
        "missing_points": missing,
        "expected_points": expected_points,
        "direct_support": direct_support,
        "high_evidence_count": int(high_evidence_count),
        "binding_insufficient": bool(binding_insufficient),
        "low_signal": bool(low_signal),
        "evidence_count": len(evidence_rows),
    }
