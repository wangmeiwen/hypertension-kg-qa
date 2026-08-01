"""数据库连接与异步仓储基础能力。"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from app.backend.migrations.mysql import (
    MYSQL_SCHEMA_VERSION,
    apply_mysql_migrations,
)

logger = logging.getLogger(__name__)


def _build_async_mysql_url(database: str | None = None) -> URL:
    """构建异步 MySQL 连接 URL。"""
    password = os.getenv("MYSQL_PASSWORD")
    if not password:
        raise RuntimeError(
            "环境变量 MYSQL_PASSWORD 未设置。请在 .env 文件或系统环境中设置 MySQL 密码。"
        )
    return URL.create(
        drivername="mysql+aiomysql",
        username=os.getenv("MYSQL_USER", "root"),
        password=password,
        host=os.getenv("MYSQL_HOST", "localhost"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        database=(
            os.getenv("MYSQL_DATABASE", "hypertension_kg")
            if database is None
            else database
        ),
    )


_DB_NAME = os.getenv("MYSQL_DATABASE", "hypertension_kg")
_ENGINE: AsyncEngine | None = None
_db_initialized = False


def _quote_database_name(database_name: str) -> str:
    """校验并引用 MySQL database 标识符。"""
    if re.fullmatch(r"[A-Za-z0-9_]+", database_name) is None:
        raise RuntimeError(f"invalid_mysql_database_name:{database_name}")
    return f"`{database_name}`"


def _get_engine() -> AsyncEngine:
    """获取或延迟创建全局异步 Engine。"""
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = create_async_engine(
            _build_async_mysql_url(_DB_NAME),
            pool_pre_ping=True,
            pool_recycle=1800,
        )
    return _ENGINE


async def get_async_conn() -> AsyncConnection:
    """获取异步数据库连接。"""
    return await _get_engine().connect()


async def execute(statement: str, params: dict[str, Any] | None = None) -> int:
    """执行写操作 SQL 并返回影响行数。"""
    async with _get_engine().begin() as conn:
        result = await conn.execute(text(statement), params or {})
        return int(result.rowcount or 0)


async def fetch_one(
    statement: str, params: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """查询单行记录。"""
    async with _get_engine().connect() as conn:
        result = await conn.execute(text(statement), params or {})
        row = result.mappings().first()
        return dict(row) if row is not None else None


async def fetch_all(
    statement: str, params: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """查询多行记录。"""
    async with _get_engine().connect() as conn:
        result = await conn.execute(text(statement), params or {})
        return [dict(row) for row in result.mappings().all()]


async def insert_and_get_last_id(
    statement: str, params: dict[str, Any] | None = None
) -> int:
    """执行插入 SQL 并返回 LAST_INSERT_ID。"""
    async with _get_engine().begin() as conn:
        await conn.execute(text(statement), params or {})
        last = await conn.execute(text("SELECT LAST_INSERT_ID() AS id"))
        row = last.mappings().first()
        if row is None:
            raise RuntimeError("last_insert_id_missing")
        return int(row["id"])


async def init_db() -> None:
    """创建目标数据库并应用所有待执行 MySQL migration。"""
    database_identifier = _quote_database_name(_DB_NAME)
    server_engine = create_async_engine(
        _build_async_mysql_url(database=""),
        pool_pre_ping=True,
        pool_recycle=1800,
    )
    try:
        async with server_engine.begin() as conn:
            await conn.execute(
                text(
                    f"CREATE DATABASE IF NOT EXISTS {database_identifier} "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
            )
    finally:
        await server_engine.dispose()

    async with _get_engine().begin() as conn:
        applied_versions = await apply_mysql_migrations(
            conn,
            database_name=_DB_NAME,
        )
    logger.info(
        "MySQL schema migration 完成: schema_version=%s applied_versions=%s",
        MYSQL_SCHEMA_VERSION,
        list(applied_versions),
    )


async def initialize_database(force: bool = False) -> bool:
    """初始化数据库；同一进程默认只执行一次。"""
    global _db_initialized
    if _db_initialized and not force:
        return True
    try:
        await init_db()
        _db_initialized = True
        logger.info("MySQL 数据库初始化成功: schema_version=%s", MYSQL_SCHEMA_VERSION)
        return True
    except Exception as exc:
        logger.exception(
            "MySQL 数据库初始化失败: error_type=%s error=%s",
            type(exc).__name__,
            exc,
        )
        return False


async def close_database() -> None:
    """释放全局异步 Engine，并重置初始化状态。"""
    global _ENGINE, _db_initialized
    engine = _ENGINE
    _ENGINE = None
    _db_initialized = False
    if engine is not None:
        await engine.dispose()
