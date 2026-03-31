"""
存储 I/O 执行器路由模块。

用于将 MySQL / Neo4j / Milvus 的同步调用统一分流到受控线程池，
避免默认线程池被混用导致不可控竞争。
"""

from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable


def _resolve_workers(env_name: str, default: int, min_value: int, max_value: int) -> int:
    raw = str(os.getenv(env_name, str(default))).strip()
    try:
        value = int(raw)
    except ValueError:
        value = default
    if value < min_value:
        return min_value
    if value > max_value:
        return max_value
    return value


MYSQL_EXECUTOR = ThreadPoolExecutor(
    max_workers=_resolve_workers("MYSQL_IO_MAX_WORKERS", 8, 1, 64),
    thread_name_prefix="mysql-io",
)
NEO4J_EXECUTOR = ThreadPoolExecutor(
    max_workers=_resolve_workers("NEO4J_IO_MAX_WORKERS", 6, 1, 64),
    thread_name_prefix="neo4j-io",
)
MILVUS_EXECUTOR = ThreadPoolExecutor(
    max_workers=_resolve_workers("MILVUS_IO_MAX_WORKERS", 6, 1, 64),
    thread_name_prefix="milvus-io",
)
GENERIC_EXECUTOR = ThreadPoolExecutor(
    max_workers=_resolve_workers("GENERIC_IO_MAX_WORKERS", 8, 1, 64),
    thread_name_prefix="generic-io",
)


def _classify_storage_target(func: Callable[..., Any]) -> str:
    module = str(getattr(func, "__module__", "") or "")
    qualname = str(getattr(func, "__qualname__", "") or "")
    text = f"{module}.{qualname}".lower()

    if module.startswith("app.backend.repositories"):
        return "mysql"
    if "neo4j_manager" in text or "knowledgegraphtool" in text:
        return "neo4j"
    if any(
        token in text
        for token in [
            "rag_core.vector_store",
            "rag_core.kg_milvus_store",
            "rag_core.document_embedding",
            "rag_core.runtime_resources",
            "ragsearchtool",
            "kgentityrelationsearchtool",
        ]
    ):
        return "milvus"
    return "generic"


def _pick_executor(target: str) -> ThreadPoolExecutor:
    if target == "mysql":
        return MYSQL_EXECUTOR
    if target == "neo4j":
        return NEO4J_EXECUTOR
    if target == "milvus":
        return MILVUS_EXECUTOR
    return GENERIC_EXECUTOR


async def run_storage_io(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """
    根据函数来源自动路由到对应存储执行器。
    """
    target = _classify_storage_target(func)
    executor = _pick_executor(target)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(executor, lambda: func(*args, **kwargs))


async def run_in_mysql_executor(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(MYSQL_EXECUTOR, lambda: func(*args, **kwargs))


async def run_in_neo4j_executor(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(NEO4J_EXECUTOR, lambda: func(*args, **kwargs))


async def run_in_milvus_executor(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(MILVUS_EXECUTOR, lambda: func(*args, **kwargs))

