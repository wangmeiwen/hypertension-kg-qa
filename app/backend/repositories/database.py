"""数据库连接与异步仓储基础能力。"""

from __future__ import annotations

import logging
import os
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncConnection, create_async_engine

logger = logging.getLogger(__name__)


def _build_async_mysql_url(database: str | None = None) -> URL:
    """构建异步 MySQL 连接 URL。

    读取环境变量中的 MySQL 配置并生成 SQLAlchemy URL 对象。
    若 `MYSQL_PASSWORD` 未配置，直接抛出异常以便启动阶段快速失败。

    Args:
        database: 指定数据库名；为 None 时使用环境变量 MYSQL_DATABASE。

    Returns:
        URL: SQLAlchemy 可用的异步 MySQL 连接地址对象。
    """
    password = os.getenv("MYSQL_PASSWORD")
    if not password:
        raise RuntimeError(
            "环境变量 MYSQL_PASSWORD 未设置。请在 .env 文件或系统环境中设置 MySQL 密码。"
        )
    return URL.create(
        drivername="mysql+asyncmy",
        username=os.getenv("MYSQL_USER", "root"),
        password=password,
        host=os.getenv("MYSQL_HOST", "localhost"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        database=os.getenv("MYSQL_DATABASE", "hypertension_kg") if database is None else database,
    )


_DB_NAME = os.getenv("MYSQL_DATABASE", "hypertension_kg")
_ENGINE: AsyncEngine | None = None
_db_initialized = False


def _get_engine() -> AsyncEngine:
    """获取（或延迟创建）全局异步 Engine。 

    Returns:
        AsyncEngine: 复用的 SQLAlchemy 异步引擎实例。
    """
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = create_async_engine(
            _build_async_mysql_url(_DB_NAME),
            pool_pre_ping=True,
            pool_recycle=1800,
        )
    return _ENGINE


async def get_async_conn() -> AsyncConnection:
    """获取一个异步数据库连接。

    Returns:
        AsyncConnection: 可用于执行 SQL 的异步连接对象。
    """
    return await _get_engine().connect()


async def execute(statement: str, params: dict[str, Any] | None = None) -> int:
    """执行写操作 SQL 并返回影响行数。

    Args:
        statement: SQL 语句（支持命名参数）。
        params: SQL 参数字典。

    Returns:
        int: 受影响行数。
    """
    async with _get_engine().begin() as conn:
        result = await conn.execute(text(statement), params or {})
        return int(result.rowcount or 0)


async def fetch_one(statement: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """查询单行记录。

    Args:
        statement: SQL 查询语句。
        params: SQL 参数字典。

    Returns:
        dict[str, Any] | None: 命中记录时返回字典，未命中返回 None。
    """
    async with _get_engine().connect() as conn:
        result = await conn.execute(text(statement), params or {})
        row = result.mappings().first()
        return dict(row) if row is not None else None


async def fetch_all(statement: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """查询多行记录。

    Args:
        statement: SQL 查询语句。
        params: SQL 参数字典。

    Returns:
        list[dict[str, Any]]: 结果行字典列表。
    """
    async with _get_engine().connect() as conn:
        result = await conn.execute(text(statement), params or {})
        return [dict(row) for row in result.mappings().all()]


async def insert_and_get_last_id(statement: str, params: dict[str, Any] | None = None) -> int:
    """执行插入 SQL 并返回 LAST_INSERT_ID。

    Args:
        statement: 插入语句。
        params: 插入参数字典。

    Returns:
        int: 新增记录自增主键 ID。
    """
    async with _get_engine().begin() as conn:
        await conn.execute(text(statement), params or {})
        last = await conn.execute(text("SELECT LAST_INSERT_ID() AS id"))
        row = last.mappings().first()
        return int((row or {}).get("id") or 0)


async def init_db() -> None:
    # 先确保数据库存在（异步连接 mysql server，不指定默认 database）
    """初始化数据库及核心业务表结构。

    初始化流程：
    1) 先创建数据库（若不存在）；
    2) 再按 DDL 清单创建所需表结构。

    Returns:
        None: 无返回值。
    """
    server_engine = create_async_engine(
        _build_async_mysql_url(database=None).set(database=""),
        pool_pre_ping=True,
        pool_recycle=1800,
    )
    try:
        async with server_engine.begin() as conn:
            await conn.execute(
                text(
                    "CREATE DATABASE IF NOT EXISTS hypertension_kg "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
            )
    finally:
        await server_engine.dispose()

    ddl_list = [
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id VARCHAR(36) PRIMARY KEY,
            user_id VARCHAR(64) NOT NULL,
            title VARCHAR(255) DEFAULT '新会话',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            INDEX idx_user_id (user_id),
            INDEX idx_updated_at (updated_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INT AUTO_INCREMENT PRIMARY KEY,
            session_id VARCHAR(36) NOT NULL,
            role VARCHAR(20) NOT NULL,
            content TEXT NOT NULL,
            think_content TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_session_id (session_id),
            FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS session_memory (
            session_id VARCHAR(36) PRIMARY KEY,
            summary_text LONGTEXT,
            retrieval_summary_text LONGTEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS token_usage (
            id INT AUTO_INCREMENT PRIMARY KEY,
            session_id VARCHAR(36) NOT NULL,
            user_id VARCHAR(64) NOT NULL,
            prompt_tokens INT DEFAULT 0,
            completion_tokens INT DEFAULT 0,
            total_tokens INT DEFAULT 0,
            model_name VARCHAR(50) DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_session_id (session_id),
            INDEX idx_user_id (user_id),
            INDEX idx_created_at (created_at),
            FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS ocr_documents (
            id VARCHAR(36) PRIMARY KEY,
            user_id VARCHAR(64) NOT NULL,
            file_name VARCHAR(255) NOT NULL,
            file_type VARCHAR(20) NOT NULL,
            file_size INT DEFAULT 0,
            page_count INT DEFAULT 0,
            chunk_count INT DEFAULT 0,
            status VARCHAR(20) DEFAULT 'pending',
            error_message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            INDEX idx_user_id (user_id),
            INDEX idx_status (status),
            INDEX idx_created_at (created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS ocr_document_pages (
            id INT AUTO_INCREMENT PRIMARY KEY,
            document_id VARCHAR(36) NOT NULL,
            page_num INT NOT NULL,
            text_content LONGTEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_document_id (document_id),
            INDEX idx_page_num (page_num),
            FOREIGN KEY (document_id) REFERENCES ocr_documents(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS ocr_token_usage (
            id INT AUTO_INCREMENT PRIMARY KEY,
            document_id VARCHAR(36) NOT NULL,
            user_id VARCHAR(64) NOT NULL,
            prompt_tokens INT DEFAULT 0,
            completion_tokens INT DEFAULT 0,
            total_tokens INT DEFAULT 0,
            page_count INT DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_document_id (document_id),
            INDEX idx_user_id (user_id),
            INDEX idx_created_at (created_at),
            FOREIGN KEY (document_id) REFERENCES ocr_documents(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS graph_import_runs (
            run_id VARCHAR(64) PRIMARY KEY,
            file_hash VARCHAR(64) NOT NULL,
            file_name VARCHAR(255) NOT NULL,
            file_size INT DEFAULT 0,
            status VARCHAR(20) NOT NULL DEFAULT 'running',
            parser_used VARCHAR(64) DEFAULT '',
            parse_warning TEXT,
            parsed_text_length INT DEFAULT 0,
            total_chars INT DEFAULT 0,
            processed_chars INT DEFAULT 0,
            total_chunks INT DEFAULT 0,
            completed_chunks INT DEFAULT 0,
            entity_count INT DEFAULT 0,
            relation_count INT DEFAULT 0,
            chunk_limit INT DEFAULT 0,
            chunk_strategy_version INT DEFAULT 0,
            docling_failed_pages LONGTEXT,
            pdf_fallback_used TINYINT(1) DEFAULT 0,
            last_error TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            finished_at TIMESTAMP NULL DEFAULT NULL,
            INDEX idx_graph_import_file_hash (file_hash),
            INDEX idx_graph_import_status (status),
            INDEX idx_graph_import_file_hash_status (file_hash, status),
            INDEX idx_graph_import_updated_at (updated_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS graph_import_chunks (
            id INT AUTO_INCREMENT PRIMARY KEY,
            run_id VARCHAR(64) NOT NULL,
            chunk_index INT NOT NULL,
            chunk_chars INT DEFAULT 0,
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            entity_count INT DEFAULT 0,
            relation_count INT DEFAULT 0,
            node_batches INT DEFAULT 0,
            relation_batches INT DEFAULT 0,
            error_message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            started_at TIMESTAMP NULL DEFAULT NULL,
            finished_at TIMESTAMP NULL DEFAULT NULL,
            UNIQUE KEY uk_graph_import_chunk (run_id, chunk_index),
            INDEX idx_graph_import_chunk_status (status),
            FOREIGN KEY (run_id) REFERENCES graph_import_runs(run_id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
    ]

    async with _get_engine().begin() as conn:
        for ddl in ddl_list:
            await conn.execute(text(ddl))
        column_patch_list = [
            {
                "table_name": "graph_import_runs",
                "column_name": "chunk_limit",
                "ddl": "ALTER TABLE graph_import_runs ADD COLUMN chunk_limit INT DEFAULT 0",
            },
            {
                "table_name": "graph_import_runs",
                "column_name": "chunk_strategy_version",
                "ddl": "ALTER TABLE graph_import_runs ADD COLUMN chunk_strategy_version INT DEFAULT 0",
            },
        ]
        for patch in column_patch_list:
            exists_result = await conn.execute(
                text(
                    """
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_schema = :table_schema
                      AND table_name = :table_name
                      AND column_name = :column_name
                    LIMIT 1
                    """
                ),
                {
                    "table_schema": _DB_NAME,
                    "table_name": patch["table_name"],
                    "column_name": patch["column_name"],
                },
            )
            if exists_result.scalar() is None:
                await conn.execute(text(str(patch["ddl"])))


async def initialize_database(force: bool = False) -> bool:
    """初始化数据库（带幂等缓存）。

    默认情况下同一进程仅初始化一次；`force=True` 可强制重新执行。

    Args:
        force: 是否忽略已初始化标记并强制重建初始化流程。

    Returns:
        bool: 初始化成功返回 True，失败返回 False。
    """
    global _db_initialized
    if _db_initialized and not force:
        return True
    try:
        await init_db()
        _db_initialized = True
        logger.info("MySQL 数据库初始化成功")
        return True
    except Exception as exc:
        logger.exception("MySQL 数据库初始化失败: error_type=%s error=%s", type(exc).__name__, exc)
        return False


from app.backend.repositories.ocr_manager import OcrDocumentManager, OcrTokenUsageManager
from app.backend.repositories.session_manager import MessageManager, SessionManager
from app.backend.repositories.session_memory_manager import SessionMemoryManager
from app.backend.repositories.token_usage_manager import TokenUsageManager
