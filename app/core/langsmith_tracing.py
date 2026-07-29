"""
LangSmith 链路追踪模块

用于集成 LangSmith 平台，记录和追踪 LLM 调用、工具执行等操作。
需要设置环境变量 LANGSMITH_TRACING=1 和 LANGSMITH_API_KEY 才能启用。
"""

import logging
import os
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


def _is_tracing_enabled() -> bool:
    """检查是否启用 LangSmith 链路追踪。

    Returns:
        bool: tracing 开关打开且 API key 存在时返回 True。
    """
    raw = str(os.getenv("LANGSMITH_TRACING") or "").strip().lower()
    if raw not in {"1", "true", "yes", "on"}:
        return False
    return bool(str(os.getenv("LANGSMITH_API_KEY") or "").strip())


_traceable_impl: Optional[Callable[..., Any]]

# 尝试导入 LangSmith 的 traceable 装饰器，导入失败时提供空实现
try:
    from langsmith import traceable as _langsmith_traceable

    _traceable_impl = _langsmith_traceable
except (
    RuntimeError,
    ValueError,
    TypeError,
    KeyError,
    AttributeError,
    OSError,
    ImportError,
):
    _traceable_impl = None


def _noop_traceable(
    *args: Any, **kwargs: Any
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """提供 LangSmith 不可用时的空装饰器工厂。"""
    _ = args, kwargs

    def _decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        return func

    return _decorator


def traceable(
    *args: Any, **kwargs: Any
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """装饰器：标记函数需要被 LangSmith 追踪。

    用法:
        @traceable(run_type="llm", name="my_llm_call")
        def my_function():
            ...

    Args:
        *args: 透传给 LangSmith traceable 的位置参数。
        **kwargs: 透传给 LangSmith traceable 的关键字参数。

    Returns:
        Callable[[Callable[..., Any]], Callable[..., Any]]: LangSmith traceable 装饰器或降级空装饰器。
    """
    if _traceable_impl is None:
        return _noop_traceable(*args, **kwargs)
    return _traceable_impl(*args, **kwargs)


def set_trace_metadata(
    metadata: Optional[Dict[str, Any]] = None, tags: Optional[list[str]] = None
) -> None:
    """设置当前追踪运行的元数据和标签。

    Args:
        metadata: 元数据字典，如 {"session_id": "xxx", "user_id": "yyy"}
        tags: 标签列表，如 ["agent", "iterative"]

    Returns:
        None: 无返回值。

    注意：仅在 LANGSMITH_TRACING=1 且设置了 LANGSMITH_API_KEY 时生效
    """
    if not _is_tracing_enabled():
        return
    metadata_obj = metadata if isinstance(metadata, dict) else {}
    tags_obj = tags if isinstance(tags, list) else []
    try:
        from langsmith import get_current_run_tree
        from langsmith.run_helpers import set_run_metadata

        payload: Dict[str, Any] = {}
        if metadata_obj:
            payload.update(metadata_obj)
        if tags_obj:
            payload["tags"] = tags_obj
        if payload:
            # 没有活动 trace 时无需设置 metadata，避免 SDK 输出噪音告警
            if get_current_run_tree() is None:
                return
            set_run_metadata(**payload)
    except (
        RuntimeError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        OSError,
        ImportError,
    ) as e:
        logger.warning("设置 LangSmith 追踪元数据失败: %s", e)
        return
