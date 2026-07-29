"""MySQL 版本化 schema 迁移。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Iterable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


@dataclass(frozen=True)
class MySQLColumnPatch:
    """仅在目标列缺失时执行的兼容迁移。"""

    table_name: str
    column_name: str
    ddl: str


@dataclass(frozen=True)
class MySQLMigration:
    """一条不可变的 MySQL schema 迁移。"""

    version: int
    name: str
    statements: tuple[str, ...] = ()
    column_patches: tuple[MySQLColumnPatch, ...] = ()

    @property
    def checksum(self) -> str:
        """返回迁移内容的稳定校验和。"""
        parts = [str(self.version), self.name, *self.statements]
        parts.extend(
            f"{patch.table_name}:{patch.column_name}:{patch.ddl}"
            for patch in self.column_patches
        )
        return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


MYSQL_BASELINE_TABLES = (
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
    """
    CREATE TABLE IF NOT EXISTS operation_locks (
        lock_key VARCHAR(191) PRIMARY KEY,
        job_id VARCHAR(128) DEFAULT '',
        owner VARCHAR(128) NOT NULL,
        expires_at TIMESTAMP NOT NULL,
        heartbeat_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        INDEX idx_operation_locks_expires_at (expires_at),
        INDEX idx_operation_locks_job_id (job_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
)

MYSQL_MIGRATIONS = (
    MySQLMigration(
        version=1,
        name="baseline_tables",
        statements=MYSQL_BASELINE_TABLES,
    ),
    MySQLMigration(
        version=2,
        name="graph_import_resume_contract",
        column_patches=(
            MySQLColumnPatch(
                table_name="graph_import_runs",
                column_name="chunk_limit",
                ddl=(
                    "ALTER TABLE graph_import_runs "
                    "ADD COLUMN chunk_limit INT DEFAULT 0"
                ),
            ),
            MySQLColumnPatch(
                table_name="graph_import_runs",
                column_name="chunk_strategy_version",
                ddl=(
                    "ALTER TABLE graph_import_runs "
                    "ADD COLUMN chunk_strategy_version INT DEFAULT 0"
                ),
            ),
        ),
    ),
    MySQLMigration(
        version=3,
        name="graph_import_observability_contract",
        column_patches=(
            MySQLColumnPatch(
                table_name="graph_import_runs",
                column_name="trace_id",
                ddl=(
                    "ALTER TABLE graph_import_runs "
                    "ADD COLUMN trace_id VARCHAR(64) NOT NULL DEFAULT '' "
                    "AFTER run_id"
                ),
            ),
            MySQLColumnPatch(
                table_name="graph_import_runs",
                column_name="duration_ms",
                ddl=(
                    "ALTER TABLE graph_import_runs "
                    "ADD COLUMN duration_ms INT DEFAULT 0 "
                    "AFTER finished_at"
                ),
            ),
        ),
    ),
)
MYSQL_SCHEMA_VERSION = MYSQL_MIGRATIONS[-1].version

SCHEMA_MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INT PRIMARY KEY,
    name VARCHAR(191) NOT NULL,
    checksum CHAR(64) NOT NULL,
    applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""


def plan_mysql_migrations(
    applied_rows: Iterable[dict[str, Any]],
) -> tuple[MySQLMigration, ...]:
    """校验已应用迁移并返回待执行迁移。"""
    applied = {int(row["version"]): row for row in applied_rows}
    if applied and max(applied) > MYSQL_SCHEMA_VERSION:
        raise RuntimeError(
            "mysql_schema_newer_than_application:"
            f"database={max(applied)}:application={MYSQL_SCHEMA_VERSION}"
        )
    if applied and set(applied) != set(range(1, max(applied) + 1)):
        raise RuntimeError("mysql_migration_history_gap")
    for migration in MYSQL_MIGRATIONS:
        row = applied.get(migration.version)
        if row is None:
            continue
        if str(row["name"]) != migration.name:
            raise RuntimeError(f"mysql_migration_name_mismatch:{migration.version}")
        if str(row["checksum"]) != migration.checksum:
            raise RuntimeError(f"mysql_migration_checksum_mismatch:{migration.version}")
    return tuple(
        migration for migration in MYSQL_MIGRATIONS if migration.version not in applied
    )


async def _column_exists(
    conn: AsyncConnection,
    *,
    database_name: str,
    patch: MySQLColumnPatch,
) -> bool:
    result = await conn.execute(
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
            "table_schema": database_name,
            "table_name": patch.table_name,
            "column_name": patch.column_name,
        },
    )
    return result.scalar() is not None


async def apply_mysql_migrations(
    conn: AsyncConnection,
    *,
    database_name: str,
) -> tuple[int, ...]:
    """执行所有待应用迁移并记录版本与校验和。"""
    await conn.execute(text(SCHEMA_MIGRATIONS_DDL))
    result = await conn.execute(
        text(
            "SELECT version, name, checksum " "FROM schema_migrations ORDER BY version"
        )
    )
    pending = plan_mysql_migrations(dict(row) for row in result.mappings().all())
    applied_versions: list[int] = []
    for migration in pending:
        for statement in migration.statements:
            await conn.execute(text(statement))
        for patch in migration.column_patches:
            if not await _column_exists(
                conn,
                database_name=database_name,
                patch=patch,
            ):
                await conn.execute(text(patch.ddl))
        await conn.execute(
            text(
                """
                INSERT INTO schema_migrations (version, name, checksum)
                VALUES (:version, :name, :checksum)
                """
            ),
            {
                "version": migration.version,
                "name": migration.name,
                "checksum": migration.checksum,
            },
        )
        applied_versions.append(migration.version)
    return tuple(applied_versions)
