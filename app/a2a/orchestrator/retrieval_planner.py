"""主控检索方向规划模块。"""

from __future__ import annotations

from app.a2a.models import SubTask


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
    intent = str(task.intent or "")
    entities = [str(item or "").strip() for item in task.entities]
    must_cover = [str(item or "").strip() for item in task.must_cover]

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

    task.query_terms = _unique([*entities, *must_cover])

    if "text_quote" in task.evidence_type_required:
        task.target_agent = "rag"
        task.tool_name = "rag_search"
    elif "relation" in task.evidence_type_required:
        task.target_agent = "kg"
        task.tool_name = "kg_entity_relation_search"

    if not task.agent_role:
        task.agent_role = "primary"
    return task
