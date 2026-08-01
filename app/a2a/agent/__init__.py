"""agent 模块。

提供 A2A 主代理和子代理的统一导出、单例管理与查询入口。
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from app.a2a.agent.graph_subagent import GraphSubAgent
    from app.a2a.agent.iterative_agent import IterativeAgent
    from app.a2a.agent.rag_subagent import RagSubAgent

_iterative_agent_singleton: Optional["IterativeAgent"] = None
_iterative_agent_lock = threading.Lock()


def get_iterative_agent() -> "IterativeAgent":
    """获取进程内复用的迭代式主代理单例。

    Returns:
        已初始化的 IterativeAgent 实例。
    """
    global _iterative_agent_singleton
    with _iterative_agent_lock:
        if _iterative_agent_singleton is None:
            from app.a2a.agent.iterative_agent import IterativeAgent

            _iterative_agent_singleton = IterativeAgent()
        return _iterative_agent_singleton


async def close_iterative_agent() -> None:
    """关闭并清空 iterative_agent 单例。"""
    global _iterative_agent_singleton
    with _iterative_agent_lock:
        agent = _iterative_agent_singleton
        _iterative_agent_singleton = None
    if agent is not None:
        await agent.aclose()


async def process_query(
    *,
    input_text: str,
    session_id: str = "",
    user_id: str = "",
    trace_id: str = "",
    strategy: str = "iterative",
    max_turns: int | None = None,
    image_data: str = "",
    file_id: str = "",
    original_input_text: str = "",
    memory_context: dict[str, Any] | None = None,
    cancel_check=None,
    progress_callback=None,
) -> dict[str, Any]:
    """统一主代理查询入口。

    Args:
        input_text: 用户提交的原始问题文本。
        session_id: 会话 ID。
        user_id: 用户 ID。
        trace_id: 当前请求的端到端追踪 ID。
        strategy: 代理策略标识，目前统一走 iterative 入口。
        max_turns: 最大迭代轮数限制，None 表示使用系统默认策略。
        image_data: 可选图片数据，透传给支持视觉输入的执行链路。
        file_id: 可选阿里 File API 文件 ID，透传给支持文件输入的执行链路。
        original_input_text: 前置改写前的原始输入文本。
        memory_context: 会话记忆上下文。
        cancel_check: 可选取消检查回调，返回 True 时提前终止执行。
        progress_callback: 可选进度回调，用于向外部推送中间状态。

    Returns:
        Dict[str, Any]: 代理执行结果，包含回答内容与执行元数据。
    """

    safe_strategy = str(strategy or "iterative").strip().lower() or "iterative"

    # 当前阶段统一复用迭代式主代理。
    _ = safe_strategy
    agent = get_iterative_agent()

    return await agent.run(
        input_text=str(input_text or "").strip(),
        session_id=str(session_id or "").strip(),
        user_id=str(user_id or "").strip(),
        trace_id=str(trace_id or "").strip(),
        strategy=safe_strategy,
        max_turns=max_turns,
        image_data=str(image_data or "").strip(),
        file_id=str(file_id or "").strip(),
        original_input_text=str(original_input_text or "").strip(),
        memory_context=memory_context if isinstance(memory_context, dict) else {},
        cancel_check=cancel_check,
        progress_callback=progress_callback,
    )


def __getattr__(name: str):
    """按名称惰性加载子模块类，减少初始化导入成本。"""
    if name == "GraphSubAgent":
        from app.a2a.agent.graph_subagent import GraphSubAgent

        return GraphSubAgent
    if name == "RagSubAgent":
        from app.a2a.agent.rag_subagent import RagSubAgent

        return RagSubAgent
    if name == "IterativeAgent":
        from app.a2a.agent.iterative_agent import IterativeAgent

        return IterativeAgent
    raise AttributeError(name)


__all__ = [
    "GraphSubAgent",
    "RagSubAgent",
    "IterativeAgent",
    "process_query",
    "get_iterative_agent",
    "close_iterative_agent",
]
