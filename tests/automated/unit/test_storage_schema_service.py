from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.backend.dependencies.admin_auth import require_admin_login
from app.backend.migrations.mysql import MYSQL_MIGRATIONS
from app.backend.migrations.neo4j import NEO4J_MIGRATIONS
from app.backend.routers.admin_storage import create_admin_storage_router
from app.backend.services.storage_schema_service import StorageSchemaService
from app.rag_core.milvus_schema import (
    KG_MILVUS_SCHEMA,
    MilvusSchemaContract,
    document_milvus_schema,
)


def _description(contract: MilvusSchemaContract) -> dict[str, Any]:
    return {
        "auto_id": contract.auto_id,
        "enable_dynamic_field": contract.enable_dynamic_field,
        "fields": [
            {
                "name": field.name,
                "type": field.type_code,
                "params": dict(field.params),
                "is_primary": field.is_primary,
                "auto_id": field.auto_id,
            }
            for field in contract.fields
        ],
    }


class _Neo4jStub:
    async def run_cypher(
        self,
        _query: str,
        _parameters: dict[str, Any],
    ) -> list[dict[str, str]]:
        return [{"name": migration.marker_name} for migration in NEO4J_MIGRATIONS]


class _MilvusStub:
    def describe_collection(self, *, collection_name: str) -> dict[str, Any]:
        if collection_name == "documents":
            return _description(document_milvus_schema(1024))
        if collection_name == "knowledge_graph":
            return _description(KG_MILVUS_SCHEMA)
        raise AssertionError(collection_name)


def _service() -> StorageSchemaService:
    async def fetch_mysql(_statement: str) -> list[dict[str, Any]]:
        return [
            {
                "version": migration.version,
                "name": migration.name,
                "checksum": migration.checksum,
                "applied_at": "2026-07-29T00:00:00Z",
            }
            for migration in MYSQL_MIGRATIONS
        ]

    return StorageSchemaService(
        fetch_mysql_migrations=fetch_mysql,
        neo4j_client=_Neo4jStub(),
        milvus_client_factory=_MilvusStub,
        document_collection_name="documents",
        graph_collection_name="knowledge_graph",
        document_dense_dim=1024,
    )


def test_storage_schema_report_marks_all_compatible_contracts() -> None:
    report = asyncio.run(_service().get_schema_report())

    assert report["data"]["status"] == "compatible"
    assert report["data"]["stores"]["mysql"]["actual_version"] == 2
    assert report["data"]["stores"]["neo4j"]["actual_version"] == 1
    assert (
        len(
            report["data"]["stores"]["milvus"]["collections"]["document"]["fingerprint"]
        )
        == 64
    )


def test_storage_schema_route_returns_report_for_admin() -> None:
    app = FastAPI()
    app.include_router(create_admin_storage_router(_service()))
    app.dependency_overrides[require_admin_login] = lambda: None
    client = TestClient(app)
    response = client.get("/admin/storage/schema-report")

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "compatible"


def test_storage_schema_report_isolates_store_failures() -> None:
    service = _service()
    service._milvus_client_factory = lambda: (_ for _ in ()).throw(
        RuntimeError("milvus unavailable")
    )

    report = asyncio.run(service.get_schema_report())

    assert report["data"]["status"] == "incompatible"
    assert report["data"]["stores"]["mysql"]["status"] == "compatible"
    assert report["data"]["stores"]["milvus"]["error_type"] == "RuntimeError"
