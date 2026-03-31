"""异步调用辅助（已移除线程池执行器）。"""

from __future__ import annotations

from typing import Any, Callable


async def run_in_mysql_executor(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    return func(*args, **kwargs)


async def run_in_neo4j_executor(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    return func(*args, **kwargs)


async def run_in_milvus_executor(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    return func(*args, **kwargs)


async def run_storage_io(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    return func(*args, **kwargs)
