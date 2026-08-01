"""异步调用辅助"""

from __future__ import annotations

from typing import Any, Callable


async def run_in_mysql_executor(
    func: Callable[..., Any], *args: Any, **kwargs: Any
) -> Any:
    """在 MySQL 逻辑入口执行函数调用。

    历史上此处可接线程池执行器；当前实现为直接调用，
    用于保持调用层 API 不变，便于后续恢复隔离执行策略。

    Args:
        func: 需要执行的目标函数。
        *args: 传递给目标函数的位置参数。
        **kwargs: 传递给目标函数的关键字参数。

    Returns:
        Any: 目标函数返回结果。
    """
    return func(*args, **kwargs)


async def run_in_neo4j_executor(
    func: Callable[..., Any], *args: Any, **kwargs: Any
) -> Any:
    """在 Neo4j 逻辑入口执行函数调用。

    与 `run_in_mysql_executor` 一样，当前不切换执行器，
    仅作为统一抽象层保留，减少业务代码与底层策略耦合。

    Args:
        func: 需要执行的目标函数。
        *args: 传递给目标函数的位置参数。
        **kwargs: 传递给目标函数的关键字参数。

    Returns:
        Any: 目标函数返回结果。
    """
    return func(*args, **kwargs)


async def run_in_milvus_executor(
    func: Callable[..., Any], *args: Any, **kwargs: Any
) -> Any:
    """在 Milvus 逻辑入口执行函数调用。

    该函数用于统一 Milvus 调用路径，当前实现为直连调用，
    便于未来按需替换为线程池/任务队列方案。

    Args:
        func: 需要执行的目标函数。
        *args: 传递给目标函数的位置参数。
        **kwargs: 传递给目标函数的关键字参数。

    Returns:
        Any: 目标函数返回结果。
    """
    return func(*args, **kwargs)


async def run_storage_io(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """统一存储 IO 调用入口。

    适用于需要保持异步调用形态、但底层暂不做执行器切换的场景。

    Args:
        func: 需要执行的目标函数。
        *args: 传递给目标函数的位置参数。
        **kwargs: 传递给目标函数的关键字参数。

    Returns:
        Any: 目标函数返回结果。
    """
    return func(*args, **kwargs)
