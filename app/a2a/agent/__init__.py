"""Agent 执行模块包。"""

from __future__ import annotations

import threading
from typing import Any, Optional

from app.a2a.agent.graph_subagent import GraphSubAgent
from app.a2a.agent.iterative_agent import IterativeAgent
from app.a2a.agent.rag_subagent import RagSubAgent

_iterative_agent_singleton: Optional[IterativeAgent] = None
_iterative_agent_lock = threading.Lock()


def get_iterative_agent() -> IterativeAgent:
    """获取 iterative_agent。
    
    Returns:
        执行结果。
    """
    global _iterative_agent_singleton
    with _iterative_agent_lock:
        if _iterative_agent_singleton is None:
            _iterative_agent_singleton = IterativeAgent()
        return _iterative_agent_singleton


async def process_query(
    *,
    input_text: str,
    session_id: str = "",
    user_id: str = "",
    strategy: str = "iterative",
    max_turns: int | None = None,
    cancel_check=None,
    progress_callback=None,
) -> dict[str, Any]:
    """统一主代理查询入口。"""

    safe_strategy = str(strategy or "iterative").strip().lower() or "iterative"

    # 当前阶段统一复用迭代式主代理。
    _ = safe_strategy
    agent = get_iterative_agent()

    return await agent.run(
        input_text=str(input_text or "").strip(),
        session_id=str(session_id or "").strip(),
        user_id=str(user_id or "").strip(),
        strategy=safe_strategy,
        max_turns=max_turns,
        cancel_check=cancel_check,
        progress_callback=progress_callback,
    )


__all__ = [
    "GraphSubAgent",
    "RagSubAgent",
    "IterativeAgent",
    "process_query",
    "get_iterative_agent",
]
