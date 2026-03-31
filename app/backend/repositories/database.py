"""
数据库连接管理模块。

本模块提供 MySQL 数据库连接管理和初始化功能，支持：
- 数据库连接配置管理
- 数据库初始化（创建表结构）
- 连接池上下文管理

主要函数：
- get_db: 获取数据库连接的上下文管理器
- init_db: 初始化数据库表结构
- initialize_database: 显式初始化数据库

"""

import os
import pymysql  # type: ignore[import-untyped]
from pymysql.cursors import DictCursor  # type: ignore[import-untyped]
from contextlib import contextmanager
from typing import Any, Callable

from app.common.storage_executors import run_in_mysql_executor


def _get_mysql_config() -> dict:
    """
    从环境变量获取 MySQL 配置，缺失时抛出异常。

    Returns:
        MySQL 配置字典

    Raises:
        RuntimeError: 当 MYSQL_PASSWORD 环境变量未设置时
    """
    password = os.getenv("MYSQL_PASSWORD")
    if not password:
        raise RuntimeError(
            "环境变量 MYSQL_PASSWORD 未设置。"
            "请在 .env 文件或系统环境中设置 MySQL 密码。"
        )
    return {
        "host": os.getenv("MYSQL_HOST", "localhost"),
        "user": os.getenv("MYSQL_USER", "root"),
        "password": password,
        "database": os.getenv("MYSQL_DATABASE", "hypertension_kg"),
        "charset": "utf8mb4",
        "cursorclass": DictCursor
    }


# MySQL 配置（延迟加载，确保环境变量已设置）
MYSQL_CONFIG = _get_mysql_config()


def init_db():
    """初始化数据库，创建数据库和表"""
    # 先连接MySQL服务器（不指定数据库）
    conn = pymysql.connect(
        host=MYSQL_CONFIG["host"],
        user=MYSQL_CONFIG["user"],
        password=MYSQL_CONFIG["password"],
        charset=MYSQL_CONFIG["charset"]
    )
    try:
        with conn.cursor() as cursor:
            # 创建数据库
            cursor.execute("CREATE DATABASE IF NOT EXISTS hypertension_kg CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        conn.commit()
    finally:
        conn.close()
    
    # 连接到新创建的数据库，创建表
    conn = pymysql.connect(**MYSQL_CONFIG)
    try:
        with conn.cursor() as cursor:
            # 会话表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id VARCHAR(36) PRIMARY KEY,
                    user_id VARCHAR(64) NOT NULL,
                    title VARCHAR(255) DEFAULT '新会话',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    INDEX idx_user_id (user_id),
                    INDEX idx_updated_at (updated_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)
            
            # 消息表
            cursor.execute("""
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
            """)

            # 会话记忆摘要表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS session_memory (
                    session_id VARCHAR(36) PRIMARY KEY,
                    summary_text LONGTEXT,
                    retrieval_summary_text LONGTEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)

            cursor.execute(
                """
                SELECT COUNT(*) AS col_count
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'session_memory'
                  AND COLUMN_NAME = 'retrieval_summary_text'
                """
            )
            row = cursor.fetchone()
            if int((row or {}).get("col_count") or 0) == 0:
                cursor.execute("ALTER TABLE session_memory ADD COLUMN retrieval_summary_text LONGTEXT")
            
            # Token 使用量统计表
            cursor.execute("""
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
            """)
            
            # OCR 文档上传历史记录表
            cursor.execute("""
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
            """)
            
            # OCR 文档解析结果表
            cursor.execute("""
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
            """)
            
            # OCR Token 使用量统计表
            cursor.execute("""
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
            """)
        conn.commit()
    finally:
        conn.close()


@contextmanager
def get_db():
    """
    获取数据库连接的上下文管理器。

    Yields:
        pymysql 数据库连接对象
    """
    conn = pymysql.connect(**MYSQL_CONFIG)
    try:
        yield conn
    finally:
        conn.close()


async def run_db_io(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """
    在 MySQL 专用执行器中运行阻塞数据库操作。
    """
    return await run_in_mysql_executor(func, *args, **kwargs)


from app.backend.repositories.ocr_manager import OcrDocumentManager, OcrTokenUsageManager
from app.backend.repositories.session_manager import MessageManager, SessionManager
from app.backend.repositories.session_memory_manager import SessionMemoryManager
from app.backend.repositories.token_usage_manager import TokenUsageManager


# 数据库初始化标志，由应用启动时显式调用
_db_initialized = False


def initialize_database(force: bool = False) -> bool:
    """
    显式初始化数据库，应在应用启动时调用。

    Args:
        force: 是否强制重新初始化（删除并重建表）

    Returns:
        初始化是否成功
    """
    global _db_initialized
    
    if _db_initialized and not force:
        return True
    
    try:
        init_db()
        _db_initialized = True
        print("MySQL数据库初始化成功")
        return True
    except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as e:
        print(f"MySQL数据库初始化失败: {e}")
        return False

