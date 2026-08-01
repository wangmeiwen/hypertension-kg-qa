from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.backend.migrations.mysql import (
    MYSQL_MIGRATIONS,
    MYSQL_SCHEMA_VERSION,
    apply_mysql_migrations,
    plan_mysql_migrations,
)


def _applied_row(version: int) -> dict[str, Any]:
    migration = MYSQL_MIGRATIONS[version - 1]
    return {
        "version": migration.version,
        "name": migration.name,
        "checksum": migration.checksum,
    }


def test_mysql_migration_contract_is_ordered_and_checksummed() -> None:
    assert [migration.version for migration in MYSQL_MIGRATIONS] == list(
        range(1, MYSQL_SCHEMA_VERSION + 1)
    )
    assert all(len(migration.checksum) == 64 for migration in MYSQL_MIGRATIONS)
    assert plan_mysql_migrations([]) == MYSQL_MIGRATIONS
    assert plan_mysql_migrations([_applied_row(1)]) == MYSQL_MIGRATIONS[1:]
    assert (
        plan_mysql_migrations(
            [_applied_row(version) for version in range(1, MYSQL_SCHEMA_VERSION + 1)]
        )
        == ()
    )


def test_mysql_migration_contract_rejects_drift_and_newer_database() -> None:
    checksum_drift = _applied_row(1)
    checksum_drift["checksum"] = "0" * 64
    with pytest.raises(RuntimeError, match="mysql_migration_checksum_mismatch:1"):
        plan_mysql_migrations([checksum_drift])

    name_drift = _applied_row(1)
    name_drift["name"] = "renamed"
    with pytest.raises(RuntimeError, match="mysql_migration_name_mismatch:1"):
        plan_mysql_migrations([name_drift])

    with pytest.raises(RuntimeError, match="mysql_schema_newer_than_application"):
        plan_mysql_migrations(
            [
                {
                    "version": MYSQL_SCHEMA_VERSION + 1,
                    "name": "future",
                    "checksum": "f" * 64,
                }
            ]
        )

    with pytest.raises(RuntimeError, match="mysql_migration_history_gap"):
        plan_mysql_migrations([_applied_row(2)])


class _MappingsResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def all(self) -> list[dict[str, Any]]:
        return self._rows


class _Result:
    def __init__(
        self,
        *,
        rows: list[dict[str, Any]] | None = None,
        scalar_value: Any = None,
    ) -> None:
        self._rows = rows if rows is not None else []
        self._scalar_value = scalar_value

    def mappings(self) -> _MappingsResult:
        return _MappingsResult(self._rows)

    def scalar(self) -> Any:
        return self._scalar_value


class _ConnectionStub:
    def __init__(
        self,
        *,
        applied_rows: list[dict[str, Any]],
        existing_columns: set[tuple[str, str]],
    ) -> None:
        self.applied_rows = applied_rows
        self.existing_columns = existing_columns
        self.executed: list[tuple[str, dict[str, Any]]] = []

    async def execute(
        self,
        statement: Any,
        params: dict[str, Any] | None = None,
    ) -> _Result:
        sql = str(statement)
        resolved_params = dict(params) if params is not None else {}
        self.executed.append((sql, resolved_params))
        if "FROM schema_migrations ORDER BY version" in sql:
            return _Result(rows=self.applied_rows)
        if "FROM information_schema.columns" in sql:
            key = (
                str(resolved_params["table_name"]),
                str(resolved_params["column_name"]),
            )
            return _Result(scalar_value=1 if key in self.existing_columns else None)
        return _Result()


def test_apply_mysql_migrations_upgrades_legacy_schema_once() -> None:
    connection = _ConnectionStub(
        applied_rows=[_applied_row(1)],
        existing_columns=set(),
    )

    applied = asyncio.run(
        apply_mysql_migrations(connection, database_name="hypertension_kg")  # type: ignore[arg-type]
    )

    executed_sql = [sql for sql, _params in connection.executed]
    assert applied == (2, 3)
    assert sum("ALTER TABLE graph_import_runs" in sql for sql in executed_sql) == 4
    insert_params = [
        params
        for sql, params in connection.executed
        if "INSERT INTO schema_migrations" in sql
    ]
    assert insert_params == [
        {
            "version": migration.version,
            "name": migration.name,
            "checksum": migration.checksum,
        }
        for migration in MYSQL_MIGRATIONS[1:]
    ]
