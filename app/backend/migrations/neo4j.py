"""Neo4j 版本化 schema 迁移。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Any, Iterable


NEO4J_MARKER_PREFIX = "hypertension_schema_v"


@dataclass(frozen=True)
class Neo4jMigration:
    """一条可重复执行的 Neo4j schema 迁移。"""

    version: int
    name: str
    statements: tuple[str, ...]

    @property
    def checksum(self) -> str:
        """返回迁移内容的稳定校验和。"""
        value = "\n".join((str(self.version), self.name, *self.statements))
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @property
    def marker_name(self) -> str:
        """返回持久化 schema 版本的哨兵索引名。"""
        return f"{NEO4J_MARKER_PREFIX}{self.version}_{self.checksum[:12]}"


NEO4J_MIGRATIONS = (
    Neo4jMigration(
        version=1,
        name="knowledge_entity_indexes",
        statements=(
            "MATCH (n) WHERE NOT n:KnowledgeEntity SET n:KnowledgeEntity",
            (
                "CREATE INDEX hypertension_entity_identity_v1 IF NOT EXISTS "
                "FOR (n:KnowledgeEntity) ON (n.entity_type, n.name)"
            ),
            (
                "CREATE INDEX hypertension_entity_canonical_name_v1 IF NOT EXISTS "
                "FOR (n:KnowledgeEntity) ON (n.canonical_name)"
            ),
        ),
    ),
)
NEO4J_SCHEMA_VERSION = NEO4J_MIGRATIONS[-1].version


def _parse_marker(marker_name: str) -> tuple[int, str]:
    match = re.fullmatch(rf"{NEO4J_MARKER_PREFIX}(\d+)_([0-9a-f]{{12}})", marker_name)
    if match is None:
        raise RuntimeError(f"invalid_neo4j_schema_marker:{marker_name}")
    return int(match.group(1)), match.group(2)


def plan_neo4j_migrations(marker_names: Iterable[str]) -> tuple[Neo4jMigration, ...]:
    """校验 Neo4j schema 哨兵并返回待执行迁移。"""
    applied: dict[int, str] = {}
    for name in marker_names:
        version, checksum = _parse_marker(name)
        if version in applied:
            raise RuntimeError(f"duplicate_neo4j_schema_marker:{version}")
        applied[version] = checksum
    if applied and max(applied) > NEO4J_SCHEMA_VERSION:
        raise RuntimeError(
            "neo4j_schema_newer_than_application:"
            f"database={max(applied)}:application={NEO4J_SCHEMA_VERSION}"
        )
    if applied and set(applied) != set(range(1, max(applied) + 1)):
        raise RuntimeError("neo4j_migration_history_gap")
    for migration in NEO4J_MIGRATIONS:
        applied_checksum = applied.get(migration.version)
        if applied_checksum is not None and applied_checksum != migration.checksum[:12]:
            raise RuntimeError(f"neo4j_migration_checksum_mismatch:{migration.version}")
    return tuple(
        migration for migration in NEO4J_MIGRATIONS if migration.version not in applied
    )


async def apply_neo4j_migrations(client: Any) -> tuple[int, ...]:
    """执行所有待应用迁移，以命名哨兵索引持久化版本。"""
    rows = await client.run_cypher(
        """
        SHOW INDEXES YIELD name
        WHERE name STARTS WITH $prefix
        RETURN name
        ORDER BY name
        """,
        {"prefix": NEO4J_MARKER_PREFIX},
    )
    pending = plan_neo4j_migrations(str(row["name"]) for row in rows if row.get("name"))
    applied_versions: list[int] = []
    for migration in pending:
        for statement in migration.statements:
            await client.run_cypher(statement)
        await client.run_cypher(
            (
                f"CREATE INDEX {migration.marker_name} IF NOT EXISTS "
                "FOR (n:KnowledgeEntity) ON (n.schema_version_marker)"
            )
        )
        applied_versions.append(migration.version)
    return tuple(applied_versions)
