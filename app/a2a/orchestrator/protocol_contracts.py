"""主控与子代理协议字段定义。"""

from __future__ import annotations

REQUEST_FIELDS = [
    "subtask_id",
    "intent",
    "question",
    "query_focus",
    "query_terms",
    "evidence_type_required",
    "must_cover",
    "constraints",
    "retry_context",
    "agent_role",
]

RESPONSE_FIELDS = [
    "subtask_id",
    "queries_used",
    "signal",
    "coverage",
    "missing_points",
    "evidence",
    "direct_support",
    "suggested_next_route",
]
