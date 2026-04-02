"""主控检索方向规划模块。"""

from __future__ import annotations

from app.a2a.models import SubTask


_SUPPORTED_INTENTS = {
    "direct_answer",
    "comparison",
    "conditional",
    "multi_outcome",
    "source_locating",
    "evidence_verification",
}


_INTENT_COMPARISON_HINTS = ("compare", "comparison", "vs", "versus", "比较", "对比", "相比", "差异", "优于", "劣于")
_INTENT_SOURCE_HINTS = ("source", "citation", "quote", "原文", "出处", "来源", "文献", "引用")
_INTENT_VERIFY_HINTS = ("verify", "verification", "evidence", "核实", "验证", "求证", "证据")


def _normalize_intent(intent: str) -> str:
    token = str(intent or "").strip().lower()
    if token in _SUPPORTED_INTENTS:
        return token
    if token in {"comaprison", "comparision"}:
        return "comparison"
    if any(hint in token for hint in _INTENT_COMPARISON_HINTS):
        return "comparison"
    if any(hint in token for hint in _INTENT_SOURCE_HINTS):
        return "source_locating"
    if any(hint in token for hint in _INTENT_VERIFY_HINTS):
        return "evidence_verification"
    if not token:
        return "direct_answer"
    return "direct_answer"


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


def plan_task(task: SubTask) -> SubTask:
    raw_intent = str(task.intent or "")
    intent = _normalize_intent(raw_intent)
    if intent != raw_intent:
        task.retry_context = {**(task.retry_context or {}), "intent_normalized_from": raw_intent}
    task.intent = intent
    entities = [str(item or "").strip() for item in task.entities]
    existing_terms = [str(item or "").strip() for item in task.query_terms]

    if intent == "comparison":
        task.query_focus = "direct_comparison"
        task.query_directions = [
            "relationship_constraint",
            "direct_evidence",
            "outcome_limited",
            "population_condition",
        ]
    elif intent == "conditional":
        task.query_focus = "condition_intervention_outcome"
        task.query_directions = ["condition_filter", "effect_evidence", "risk_constraint"]
    elif intent == "multi_outcome":
        task.query_focus = "multi_outcomes"
        task.query_directions = ["outcome_split", "endpoint_alignment"]
    elif intent == "source_locating":
        task.query_focus = "source_quote"
        task.query_directions = ["quote_locating", "citation_alignment"]
    elif intent == "evidence_verification":
        task.query_focus = "evidence_verification"
        task.query_directions = ["claim_support", "source_validation"]
    else:
        task.query_focus = "direct_answer"
        task.query_directions = ["key_fact"]

    task.query_terms = _unique([*existing_terms, *entities])

    has_text_quote = "text_quote" in task.evidence_type_required
    has_relation = "relation" in task.evidence_type_required
    if has_text_quote and not has_relation:
        task.target_agent = "rag"
        task.tool_name = "rag_search"
    elif has_relation and not has_text_quote:
        task.target_agent = "kg"
        task.tool_name = "kg_entity_relation_search"
    elif has_relation and has_text_quote:
        # dual-evidence 场景只设置首轮主路径，另一类证据交给 supplement/reroute。
        retry_context = task.retry_context if isinstance(task.retry_context, dict) else {}
        if task.target_agent == "rag":
            task.tool_name = "rag_search"
            task.retry_context = {
                **retry_context,
                "primary_evidence_type": "text_quote",
                "secondary_evidence_type": "relation",
            }
        else:
            task.target_agent = "kg"
            task.tool_name = "kg_entity_relation_search"
            task.retry_context = {
                **retry_context,
                "primary_evidence_type": "relation",
                "secondary_evidence_type": "text_quote",
            }

    if not task.agent_role:
        task.agent_role = "primary"
    return task
