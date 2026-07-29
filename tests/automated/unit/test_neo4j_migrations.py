from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.backend.migrations.neo4j import (
    NEO4J_MARKER_PREFIX,
    NEO4J_MIGRATIONS,
    NEO4J_SCHEMA_VERSION,
    apply_neo4j_migrations,
    plan_neo4j_migrations,
)


def test_neo4j_migration_contract_is_ordered_and_checksummed() -> None:
    assert [migration.version for migration in NEO4J_MIGRATIONS] == list(
        range(1, NEO4J_SCHEMA_VERSION + 1)
    )
    assert plan_neo4j_migrations([]) == NEO4J_MIGRATIONS
    assert (
        plan_neo4j_migrations([migration.marker_name for migration in NEO4J_MIGRATIONS])
        == ()
    )


def test_neo4j_migration_contract_rejects_drift_and_newer_database() -> None:
    migration = NEO4J_MIGRATIONS[0]
    drifted_marker = f"{NEO4J_MARKER_PREFIX}{migration.version}_{'0' * 12}"
    with pytest.raises(RuntimeError, match="neo4j_migration_checksum_mismatch:1"):
        plan_neo4j_migrations([drifted_marker])

    future_marker = f"{NEO4J_MARKER_PREFIX}{NEO4J_SCHEMA_VERSION + 1}_{'f' * 12}"
    with pytest.raises(RuntimeError, match="neo4j_schema_newer_than_application"):
        plan_neo4j_migrations([future_marker])

    with pytest.raises(RuntimeError, match="duplicate_neo4j_schema_marker:1"):
        plan_neo4j_migrations([migration.marker_name, migration.marker_name])


class _Neo4jStub:
    def __init__(self, marker_names: list[str]) -> None:
        self.marker_names = marker_names
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def run_cypher(
        self,
        query: str,
        parameters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        self.calls.append((query, dict(parameters) if parameters is not None else {}))
        if "SHOW INDEXES" in query:
            return [{"name": name} for name in self.marker_names]
        return []


def test_apply_neo4j_migrations_writes_marker_after_statements() -> None:
    client = _Neo4jStub([])

    applied = asyncio.run(apply_neo4j_migrations(client))

    migration = NEO4J_MIGRATIONS[0]
    assert applied == (1,)
    assert [query for query, _params in client.calls[1:-1]] == list(
        migration.statements
    )
    assert migration.marker_name in client.calls[-1][0]


def test_apply_neo4j_migrations_is_idempotent() -> None:
    client = _Neo4jStub([NEO4J_MIGRATIONS[0].marker_name])

    applied = asyncio.run(apply_neo4j_migrations(client))

    assert applied == ()
    assert len(client.calls) == 1
