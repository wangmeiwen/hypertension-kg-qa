"""门控机制适配模块。"""

from __future__ import annotations

from typing import Any

from app.a2a.agent.gating_mechanism import evaluate_and_gate


def run_gate(
    *,
    traces: list[dict[str, Any]],
    classification: dict[str, Any],
    backtrack_count: int,
    max_backtrack: int,
    is_degraded: bool,
) -> dict[str, Any]:
    return evaluate_and_gate(
        traces=traces,
        classification=classification,
        backtrack_count=int(backtrack_count),
        max_backtrack=int(max_backtrack),
        is_degraded=bool(is_degraded),
    )
