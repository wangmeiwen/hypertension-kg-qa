"""数据库连接与异步仓储基础能力。"""

from __future__ import annotations

import os
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncConnection, create_async_engine


def _build_async_mysql_url(database: str | None = None) -> URL:
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
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = create_async_engine(
            _build_async_mysql_url(_DB_NAME),
            pool_pre_ping=True,
            pool_recycle=1800,
        )
    return _ENGINE


async def get_async_conn() -> AsyncConnection:
    return await _get_engine().connect()


async def execute(statement: str, params: dict[str, Any] | None = None) -> int:
    async with _get_engine().begin() as conn:
        result = await conn.execute(text(statement), params or {})
        return int(result.rowcount or 0)


async def fetch_one(statement: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
    async with _get_engine().connect() as conn:
        result = await conn.execute(text(statement), params or {})
        row = result.mappings().first()
        return dict(row) if row is not None else None


async def fetch_all(statement: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    async with _get_engine().connect() as conn:
        result = await conn.execute(text(statement), params or {})
        return [dict(row) for row in result.mappings().all()]


async def insert_and_get_last_id(statement: str, params: dict[str, Any] | None = None) -> int:
    async with _get_engine().begin() as conn:
        await conn.execute(text(statement), params or {})
        last = await conn.execute(text("SELECT LAST_INSERT_ID() AS id"))
        row = last.mappings().first()
        return int((row or {}).get("id") or 0)


async def init_db() -> None:
    # 先确保数据库存在（异步连接 mysql server，不指定默认 database）
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
    ]

    async with _get_engine().begin() as conn:
        for ddl in ddl_list:
            await conn.execute(text(ddl))


async def initialize_database(force: bool = False) -> bool:
    global _db_initialized
    if _db_initialized and not force:
        return True
    try:
        await init_db()
        _db_initialized = True
        print("MySQL数据库初始化成功")
        return True
    except Exception as exc:
        print(f"MySQL数据库初始化失败: {exc}")
        return False


from app.backend.repositories.ocr_manager import OcrDocumentManager, OcrTokenUsageManager
from app.backend.repositories.session_manager import MessageManager, SessionManager
from app.backend.repositories.session_memory_manager import SessionMemoryManager
from app.backend.repositories.token_usage_manager import TokenUsageManager
