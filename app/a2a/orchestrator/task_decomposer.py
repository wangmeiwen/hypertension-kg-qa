"""任务语义拆解模块（基于 ResolvedQuery）。"""

from __future__ import annotations

from typing import Any

from app.a2a.models import SubAgentConstraints, SubTask

_SOURCE_HINTS = ("原文", "出处", "证据", "引用", "文献", "来源")
_RELATION_HINTS = ("关系", "机制", "通路", "实体", "关联", "适应症")
_SUPPORTED_INTENTS = {
    "direct_answer",
    "comparison",
    "conditional",
    "multi_outcome",
    "source_locating",
    "evidence_verification",
}
_INTENT_ALIASES = {
    "direct": "direct_answer",
    "qa": "direct_answer",
    "compare": "comparison",
    "comaprison": "comparison",
    "comparision": "comparison",
    "source": "source_locating",
    "verification": "evidence_verification",
    "verify": "evidence_verification",
    "比较": "comparison",
    "对比": "comparison",
    "对照": "comparison",
    "来源": "source_locating",
    "原文": "source_locating",
    "出处": "source_locating",
    "证据": "evidence_verification",
    "验证": "evidence_verification",
    "核实": "evidence_verification",
}


_INTENT_COMPARISON_HINTS = ("compare", "comparison", "vs", "versus", "比较", "对比", "相比", "差异", "优于", "劣于")
_INTENT_SOURCE_HINTS = ("source", "citation", "quote", "原文", "出处", "来源", "文献", "引用")
_INTENT_VERIFY_HINTS = ("verify", "verification", "evidence", "核实", "验证", "求证", "证据")
_SOURCE_POINT_HINTS = ("source", "citation", "quote", "evidence_source", "原文", "出处", "引用", "文献", "来源")
_SAFETY_HINTS = ("safety", "risk", "adverse", "ae", "安全", "不良")
_EFFICACY_HINTS = ("efficacy", "effect", "outcome", "benefit", "疗效", "结局", "获益", "降压", "保护")


def _unique(values: list[str]) -> list[str]:
    rows: list[str] = []
    seen: set[str] = set()
    for item in values:
        token = str(item or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        rows.append(token)
    return rows


def _is_source_cover_point(point: str) -> bool:
    token = str(point or "").strip().lower()
    if not token:
        return False
    return any(hint in token for hint in _SOURCE_POINT_HINTS)


def _question_semantic(question: str) -> str:
    token = str(question or "").strip().lower()
    if not token:
        return ""
    if any(hint in token for hint in _SAFETY_HINTS):
        return "safety"
    if any(hint in token for hint in _EFFICACY_HINTS):
        return "efficacy"
    return ""


def _pick_semantic_cover_points(question: str, points: list[str]) -> list[str]:
    semantic = _question_semantic(question)
    if semantic == "safety":
        safety_points = [point for point in points if any(hint in str(point).lower() for hint in _SAFETY_HINTS)]
        if safety_points:
            return _unique(safety_points)

    if semantic == "efficacy":
        efficacy_points = [point for point in points if any(hint in str(point).lower() for hint in _EFFICACY_HINTS)]
        if efficacy_points:
            return _unique(efficacy_points)

    return []


def _resolve_task_specific_must_cover(subquery: str, target_agent: str, must_cover: list[str]) -> tuple[list[str], bool]:
    if not must_cover:
        return ["answer"], False

    question = str(subquery or "").strip().lower()
    matched = [point for point in must_cover if point and (str(point).lower() in question or question in str(point).lower())]
    if matched:
        return _unique(matched), False

    source_points = [point for point in must_cover if _is_source_cover_point(point)]
    non_source_points = [point for point in must_cover if point not in source_points]
    question_semantic = _question_semantic(question)
    semantic_points = _pick_semantic_cover_points(question, non_source_points)

    if target_agent == "rag" or _is_source_query(subquery):
        mapped = _unique(source_points or must_cover)
        return mapped, not bool(source_points)

    if semantic_points:
        return semantic_points, False

    if question_semantic in {"safety", "efficacy"}:
        return [], True

    if _is_relation_query(subquery):
        mapped = _unique(non_source_points or must_cover)
        return mapped, True

    mapped = _unique(non_source_points or must_cover)
    return mapped, True


def _sanitize_list(value: Any, max_items: int = 8, max_len: int = 200) -> list[str]:
    if not isinstance(value, list):
        return []
    rows: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if not text:
            continue
        rows.append(text[:max_len])
        if len(rows) >= max_items:
            break
    return rows


def _normalize_preferred_sources(value: Any) -> list[str]:
    rows: list[str] = []
    for item in _sanitize_list(value, max_items=2, max_len=8):
        source = str(item or "").strip().lower()
        if source in {"kg", "rag"} and source not in rows:
            rows.append(source)
    if not rows:
        rows = ["kg"]
    return rows


def _is_source_query(text: str) -> bool:
    token = str(text or "")
    return any(hint in token for hint in _SOURCE_HINTS)


def _is_relation_query(text: str) -> bool:
    token = str(text or "")
    return any(hint in token for hint in _RELATION_HINTS)


def _pick_target_agent(subquery: str, intent: str, preferred_sources: list[str]) -> str:
    if _is_source_query(subquery) or intent == "source_locating":
        return "rag"

    has_kg = "kg" in preferred_sources
    has_rag = "rag" in preferred_sources

    if has_kg and not has_rag:
        return "kg"
    if has_rag and not has_kg:
        return "rag"

    if _is_relation_query(subquery):
        return "kg"
    return "kg"


def _normalize_intent(intent: str) -> str:
    token = str(intent or "").strip().lower()
    if token in _SUPPORTED_INTENTS:
        return token
    if token in _INTENT_ALIASES:
        return _INTENT_ALIASES[token]
    if any(hint in token for hint in _INTENT_COMPARISON_HINTS):
        return "comparison"
    if any(hint in token for hint in _INTENT_SOURCE_HINTS):
        return "source_locating"
    if any(hint in token for hint in _INTENT_VERIFY_HINTS):
        return "evidence_verification"
    return "direct_answer"


def _build_evidence_type(target_agent: str, intent: str) -> list[str]:
    if target_agent == "rag":
        return ["text_quote"]
    if intent in {"comparison", "evidence_verification"}:
        return ["relation", "text_quote"]
    return ["relation"]


def _priority(intent: str, index: int) -> int:
    base = 60
    if intent == "comparison":
        base = 90
    elif intent == "conditional":
        base = 85
    elif intent == "multi_outcome":
        base = 80
    elif intent in {"source_locating", "evidence_verification"}:
        base = 75
    return max(1, base - (index - 1) * 2)


def decompose_to_tasks(resolved_query: dict[str, Any], constraints: SubAgentConstraints) -> list[SubTask]:
    source = resolved_query if isinstance(resolved_query, dict) else {}
    standalone_question = str(source.get("standalone_question") or "").strip()
    intent = _normalize_intent(str(source.get("intent") or "direct_answer"))

    subqueries = _sanitize_list(source.get("subqueries"), max_items=8, max_len=240)
    if not subqueries and standalone_question:
        subqueries = [standalone_question]
    if not subqueries:
        subqueries = ["请补充问题"]

    must_cover = _sanitize_list(source.get("must_cover"), max_items=8, max_len=80)
    if not must_cover:
        must_cover = ["answer"]

    entities = _sanitize_list(source.get("entities"), max_items=8, max_len=80)
    filters = _sanitize_list(source.get("filters"), max_items=8, max_len=80)
    preferred_sources = _normalize_preferred_sources(source.get("preferred_sources"))
    query_terms = list(dict.fromkeys([*entities, *filters]))

    tasks: list[SubTask] = []
    for idx, subquery in enumerate(subqueries, start=1):
        target_agent = _pick_target_agent(subquery, intent, preferred_sources)
        task_must_cover, must_cover_unmapped = _resolve_task_specific_must_cover(subquery, target_agent, must_cover)
        tasks.append(
            SubTask(
                subtask_id=idx,
                target_agent="rag" if target_agent == "rag" else "kg",
                question=str(subquery or standalone_question or "请补充问题"),
                intent=intent,
                entities=entities,
                dependency_on=[],
                evidence_type_required=_build_evidence_type(target_agent, intent),
                must_cover=task_must_cover,
                priority=_priority(intent, idx),
                attempt_count=0,
                coverage_status="unknown",
                constraints=constraints,
                tool_name="rag_search" if target_agent == "rag" else "kg_entity_relation_search",
                query_terms=query_terms,
                retry_context={"must_cover_mapping": "unmapped"} if must_cover_unmapped else {},
                reason="decompose_from_resolved_query",
                agent_role="primary",
            )
        )
    return tasks


def build_classification(tasks: list[SubTask]) -> dict[str, Any]:
    if not tasks:
        return {"primary_type": "SIMPLE", "types": ["SIMPLE"], "hop_count": 0, "subqueries": []}

    intent_types = [str(task.intent or "").strip().upper() for task in tasks if str(task.intent or "").strip()]
    primary_type = "MULTI_HOP" if len(tasks) > 1 else "SIMPLE"
    return {
        "primary_type": primary_type,
        "types": [primary_type, *intent_types],
        "hop_count": len(tasks),
        "subqueries": [str(task.question or "") for task in tasks],
    }
