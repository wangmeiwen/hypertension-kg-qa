"""三类存储 schema 的只读诊断报告。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.backend.migrations.mysql import (
    MYSQL_MIGRATIONS,
    MYSQL_SCHEMA_VERSION,
    plan_mysql_migrations,
)
from app.backend.migrations.neo4j import (
    NEO4J_MARKER_PREFIX,
    NEO4J_MIGRATIONS,
    NEO4J_SCHEMA_VERSION,
    plan_neo4j_migrations,
)
from app.rag_core.milvus_schema import (
    KG_MILVUS_SCHEMA,
    describe_and_validate_milvus_schema,
    document_milvus_schema,
)


class StorageSchemaService:
    """汇总 MySQL、Neo4j 和 Milvus schema 兼容状态。"""

    def __init__(
        self,
        *,
        fetch_mysql_migrations: Callable[..., Awaitable[list[dict[str, Any]]]],
        neo4j_client: Any,
        milvus_client_factory: Callable[[], Any],
        document_collection_name: str,
        graph_collection_name: str,
        document_dense_dim: int,
    ) -> None:
        self._fetch_mysql_migrations = fetch_mysql_migrations
        self._neo4j_client = neo4j_client
        self._milvus_client_factory = milvus_client_factory
        self._document_collection_name = document_collection_name
        self._graph_collection_name = graph_collection_name
        self._document_dense_dim = document_dense_dim

    async def _mysql_report(self) -> dict[str, Any]:
        rows = await self._fetch_mysql_migrations(
            "SELECT version, name, checksum, applied_at "
            "FROM schema_migrations ORDER BY version"
        )
        pending = plan_mysql_migrations(rows)
        return {
            "status": "compatible" if not pending else "migration_pending",
            "expected_version": MYSQL_SCHEMA_VERSION,
            "actual_version": max((int(row["version"]) for row in rows), default=0),
            "pending_versions": [migration.version for migration in pending],
            "migrations": [
                {
                    "version": migration.version,
                    "name": migration.name,
                    "checksum": migration.checksum,
                }
                for migration in MYSQL_MIGRATIONS
            ],
        }

    async def _neo4j_report(self) -> dict[str, Any]:
        rows = await self._neo4j_client.run_cypher(
            """
            SHOW INDEXES YIELD name
            WHERE name STARTS WITH $prefix
            RETURN name
            ORDER BY name
            """,
            {"prefix": NEO4J_MARKER_PREFIX},
        )
        markers = [str(row["name"]) for row in rows if row.get("name")]
        pending = plan_neo4j_migrations(markers)
        applied_versions = [
            migration.version
            for migration in NEO4J_MIGRATIONS
            if migration.marker_name in markers
        ]
        return {
            "status": "compatible" if not pending else "migration_pending",
            "expected_version": NEO4J_SCHEMA_VERSION,
            "actual_version": max(applied_versions, default=0),
            "pending_versions": [migration.version for migration in pending],
            "markers": markers,
        }

    def _milvus_report_blocking(self) -> dict[str, Any]:
        client = self._milvus_client_factory()
        document_contract = document_milvus_schema(self._document_dense_dim)
        document_fingerprint = describe_and_validate_milvus_schema(
            client,
            collection_name=self._document_collection_name,
            contract=document_contract,
        )
        graph_fingerprint = describe_and_validate_milvus_schema(
            client,
            collection_name=self._graph_collection_name,
            contract=KG_MILVUS_SCHEMA,
        )
        return {
            "status": "compatible",
            "collections": {
                "document": {
                    "name": self._document_collection_name,
                    "version": document_contract.version,
                    "fingerprint": document_fingerprint,
                },
                "knowledge_graph": {
                    "name": self._graph_collection_name,
                    "version": KG_MILVUS_SCHEMA.version,
                    "fingerprint": graph_fingerprint,
                },
            },
        }

    @staticmethod
    async def _capture(check) -> dict[str, Any]:
        try:
            return await check()
        except Exception as exc:
            return {
                "status": "incompatible",
                "error_type": type(exc).__name__,
                "error": str(exc),
            }

    async def get_schema_report(self) -> dict[str, Any]:
        """并发生成三类存储的只读 schema 报告。"""
        mysql, neo4j, milvus = await asyncio.gather(
            self._capture(self._mysql_report),
            self._capture(self._neo4j_report),
            self._capture(lambda: asyncio.to_thread(self._milvus_report_blocking)),
        )
        stores = {"mysql": mysql, "neo4j": neo4j, "milvus": milvus}
        compatible = all(
            store.get("status") == "compatible" for store in stores.values()
        )
        return {
            "ok": True,
            "data": {
                "status": "compatible" if compatible else "incompatible",
                "stores": stores,
                "generated_at": datetime.now(timezone.utc).isoformat(),
            },
        }
