"""任务语义拆解模块（基于 ResolvedQuery）。"""

from __future__ import annotations

from typing import Any

from app.a2a.models import SubAgentConstraints, SubTask

_SOURCE_HINTS = ("原文", "出处", "证据", "引用", "文献", "来源")
_RELATION_HINTS = ("关系", "机制", "通路", "实体", "关联", "适应症")


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
    has_kg = "kg" in preferred_sources
    has_rag = "rag" in preferred_sources

    if has_kg and not has_rag:
        return "kg"
    if has_rag and not has_kg:
        return "rag"

    if _is_source_query(subquery) or intent == "source_locating":
        return "rag"
    if _is_relation_query(subquery):
        return "kg"
    return "kg"


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
    intent = str(source.get("intent") or "direct_answer").strip() or "direct_answer"

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
        tasks.append(
            SubTask(
                subtask_id=idx,
                target_agent="rag" if target_agent == "rag" else "kg",
                question=str(subquery or standalone_question or "请补充问题"),
                intent=intent,
                entities=entities,
                dependency_on=[],
                evidence_type_required=_build_evidence_type(target_agent, intent),
                must_cover=must_cover,
                priority=_priority(intent, idx),
                attempt_count=0,
                coverage_status="unknown",
                constraints=constraints,
                tool_name="rag_search" if target_agent == "rag" else "kg_entity_relation_search",
                query_terms=query_terms,
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
