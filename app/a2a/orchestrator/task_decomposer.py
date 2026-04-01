"""任务语义拆解模块。"""

from __future__ import annotations

import re
from typing import Any

from app.a2a.models import SubAgentConstraints, SubTask


_COMPARE_HINTS = ("相比", "比较", "优于", "优势", "区别", "差异")
_CONDITION_HINTS = ("如果", "对于", "在", "条件", "人群", "场景")
_OUTCOME_HINTS = ("降压", "靶器官", "蛋白尿", "eGFR", "LVH", "终点")
_VERIFICATION_HINTS = ("证据", "依据", "来源", "原文", "文献")
_LOCATION_HINTS = ("原文", "出处", "第", "段", "页")


def _split_question(question: str) -> list[str]:
    text = str(question or "").strip()
    if not text:
        return []
    chunks = [item.strip() for item in re.split(r"[。！？!?;；\n]+", text) if item and item.strip()]
    if chunks:
        return chunks
    return [text]


def _infer_intent(text: str) -> str:
    q = str(text or "")
    if any(token in q for token in _COMPARE_HINTS):
        return "comparison"
    if any(token in q for token in _LOCATION_HINTS):
        return "source_locating"
    if any(token in q for token in _VERIFICATION_HINTS):
        return "evidence_verification"
    if any(token in q for token in _OUTCOME_HINTS):
        return "multi_outcome"
    if any(token in q for token in _CONDITION_HINTS):
        return "conditional"
    return "direct_answer"


def _extract_entities(text: str) -> list[str]:
    raw = re.findall(r"[A-Za-z0-9\u4e00-\u9fff]{2,}", str(text or ""))
    rows: list[str] = []
    seen: set[str] = set()
    for item in raw:
        token = str(item or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        rows.append(token)
    return rows[:12]


def _build_must_cover(intent: str, text: str) -> list[str]:
    q = str(text or "")
    if intent == "comparison":
        rows = ["intervention", "comparator", "outcomes"]
        if "人群" in q or "患者" in q:
            rows.append("population")
        return rows
    if intent == "conditional":
        return ["condition", "intervention", "outcomes"]
    if intent == "multi_outcome":
        return ["outcomes"]
    if intent == "source_locating":
        return ["quote", "source"]
    if intent == "evidence_verification":
        return ["evidence", "source"]
    return ["answer"]


def _build_evidence_type(intent: str) -> list[str]:
    if intent in {"comparison", "conditional", "multi_outcome"}:
        return ["relation", "text_quote"]
    if intent in {"source_locating", "evidence_verification"}:
        return ["text_quote"]
    return ["relation"]


def _priority(intent: str) -> int:
    if intent == "comparison":
        return 95
    if intent == "conditional":
        return 85
    if intent == "multi_outcome":
        return 80
    if intent in {"source_locating", "evidence_verification"}:
        return 75
    return 60


def decompose_to_tasks(question: str, constraints: SubAgentConstraints) -> list[SubTask]:
    chunks = _split_question(question)
    if not chunks:
        return [
            SubTask(
                subtask_id=1,
                target_agent="kg",
                question=str(question or "").strip(),
                intent="direct_answer",
                entities=[],
                dependency_on=[],
                evidence_type_required=["relation"],
                must_cover=["answer"],
                priority=60,
                attempt_count=0,
                coverage_status="unknown",
                constraints=constraints,
                tool_name="kg_entity_relation_search",
                reason="decompose_direct",
                agent_role="primary",
            )
        ]

    rows: list[SubTask] = []
    base_question = str(question or "").strip()
    for idx, chunk in enumerate(chunks, start=1):
        intent = _infer_intent(chunk)
        evidence_type_required = _build_evidence_type(intent)
        target_agent = "rag" if evidence_type_required == ["text_quote"] else "kg"
        dependency_on: list[int] = []
        if intent in {"source_locating", "evidence_verification"} and idx > 1:
            dependency_on = [idx - 1]
        rows.append(
            SubTask(
                subtask_id=idx,
                target_agent=target_agent,
                question=chunk if chunk else base_question,
                intent=intent,
                entities=_extract_entities(chunk),
                dependency_on=dependency_on,
                evidence_type_required=evidence_type_required,
                must_cover=_build_must_cover(intent, chunk),
                priority=_priority(intent),
                attempt_count=0,
                coverage_status="unknown",
                constraints=constraints,
                tool_name="rag_search" if target_agent == "rag" else "kg_entity_relation_search",
                reason="decompose_task_semantic",
                agent_role="primary",
            )
        )
    return rows


def build_classification(tasks: list[SubTask]) -> dict[str, Any]:
    if not tasks:
        return {"primary_type": "SIMPLE", "types": ["SIMPLE"], "hop_count": 0, "subqueries": []}
    intents = [str(task.intent or "").strip().upper() for task in tasks]
    primary_type = "MULTI_HOP" if len(tasks) > 1 else "SIMPLE"
    return {
        "primary_type": primary_type,
        "types": [primary_type, *[intent for intent in intents if intent]],
        "hop_count": len(tasks),
        "subqueries": [str(task.question or "") for task in tasks],
    }
