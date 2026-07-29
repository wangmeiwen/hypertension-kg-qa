from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.backend.app_factory import BackendAppTestOverrides, create_app
from app.backend.dependencies.admin_auth import require_admin_access
from app.backend.services.startup_runtime_service import StartupRuntimeBehavior
from app.backend.testing.deterministic_support import (
    DeterministicA2AService,
    DeterministicMcpServer,
    DeterministicSessionMemoryService,
)
from app.core.config import Config
from app.backend.infrastructure.database_health import check_databases
from app.backend.repositories.database import close_database
from app.knowledge_graph.neo4j_manager import neo4j_client
from app.rag_core.kg_milvus_store import KgMilvusStore

pytestmark = [pytest.mark.integration, pytest.mark.db]


def _build_real_fastapi_app(
    *,
    neo4j_override: Any = None,
    get_shared_kg_milvus_store: Any = None,
    get_shared_vector_store: Any = None,
) -> FastAPI:
    """构建使用真实数据库启动路径、但替换 LLM/A2A/MCP 的 FastAPI 应用。"""
    overrides = BackendAppTestOverrides(
        startup_behavior=StartupRuntimeBehavior(
            initialize_database=True,
            check_databases=False,
            prewarm_retrieval_resources=False,
            bootstrap_mcp=True,
            bootstrap_a2a=True,
            close_neo4j_on_shutdown=False,
        ),
        mcp_server=DeterministicMcpServer(),
        a2a_service=DeterministicA2AService(
            agent_name="Hypertension Orchestrator Agent",
            answer_prefix="测试回答：",
        ),
        a2a_graph_service=DeterministicA2AService(
            agent_name="Hypertension Graph Agent",
            answer_prefix="图谱测试：",
        ),
        a2a_retrieval_service=DeterministicA2AService(
            agent_name="Hypertension Retrieval Agent",
            answer_prefix="检索测试：",
        ),
        session_memory_service=DeterministicSessionMemoryService(),
        neo4j_client=neo4j_override,
        get_shared_kg_milvus_store=get_shared_kg_milvus_store,
        get_shared_vector_store=get_shared_vector_store,
    )
    app = create_app(test_overrides=overrides)
    app.dependency_overrides[require_admin_access] = lambda: None
    return app


def _build_real_app(
    *,
    neo4j_override: Any = None,
    get_shared_kg_milvus_store: Any = None,
    get_shared_vector_store: Any = None,
) -> TestClient:
    app = _build_real_fastapi_app(
        neo4j_override=neo4j_override,
        get_shared_kg_milvus_store=get_shared_kg_milvus_store,
        get_shared_vector_store=get_shared_vector_store,
    )
    return TestClient(app)


async def _require_neo4j_and_milvus() -> None:
    """真实 DB 测试前检查 Neo4j 与 Milvus，不满足时跳过用例。"""
    status = await check_databases()
    if not bool((status.get("neo4j") or {}).get("is_running")):
        pytest.skip("Neo4j 未就绪")
    if not bool((status.get("milvus") or {}).get("is_running")):
        pytest.skip("Milvus 未就绪")


@pytest.mark.asyncio
async def test_mysql_session_and_admin_routes_roundtrip(
    test_data_prefix,
    cleanup_mysql_prefix,
    cleanup_session_artifacts,
) -> None:
    user_id = f"{test_data_prefix}user"
    app = _build_real_fastapi_app()
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    session_id = ""
    try:
        created = await client.post(
            "/sessions", json={"user_id": user_id, "title": "测试会话"}
        )
        assert created.status_code == 200
        session_id = created.json()["data"]["id"]

        add_user = await client.post(
            f"/sessions/{session_id}/messages",
            json={"user_id": user_id, "role": "user", "content": "第一问"},
        )
        assert add_user.status_code == 200

        add_assistant = await client.post(
            f"/sessions/{session_id}/messages",
            json={"user_id": user_id, "role": "assistant", "content": "第一答"},
        )
        assert add_assistant.status_code == 200

        messages = await client.get(
            f"/sessions/{session_id}/messages", params={"user_id": user_id}
        )
        assert messages.status_code == 200
        assert len(messages.json()["data"]) == 2

        rollback = await client.post(
            f"/sessions/{session_id}/rollback-to-question",
            json={"user_id": user_id, "message_id": messages.json()["data"][0]["id"]},
        )
        assert rollback.status_code == 200
        assert rollback.json()["ok"] is True

        admin_sessions = await client.get("/admin/sessions")
        assert admin_sessions.status_code == 200
        assert any(row["id"] == session_id for row in admin_sessions.json()["data"])

        admin_messages = await client.get(f"/admin/sessions/{session_id}/messages")
        assert admin_messages.status_code == 200
        assert admin_messages.json()["session"]["id"] == session_id

        deleted = await client.delete(f"/admin/sessions/{session_id}")
        assert deleted.status_code == 200
        assert deleted.json()["ok"] is True
    finally:
        await client.aclose()
        cleanup_session_artifacts(session_id)
        await cleanup_mysql_prefix(test_data_prefix)
        await close_database()


@pytest.mark.asyncio
async def test_neo4j_graph_routes_roundtrip(test_run_id) -> None:
    status = await check_databases()
    if not bool((status.get("neo4j") or {}).get("is_running")):
        pytest.skip("Neo4j 未就绪")

    await neo4j_client.run_cypher(
        """
        CREATE (d:PytestDisease {name: $disease_name, test_run_id: $test_run_id})
        CREATE (r:PytestRisk {name: $risk_name, test_run_id: $test_run_id})
        CREATE (r)-[:RELATED_TO {test_run_id: $test_run_id}]->(d)
        """,
        {
            "disease_name": f"{test_run_id}_高血压",
            "risk_name": f"{test_run_id}_高盐饮食",
            "test_run_id": test_run_id,
        },
    )

    app = _build_real_fastapi_app(neo4j_override=neo4j_client)
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    try:
        graph = await client.get(
            "/graph/visualization", params={"keyword": test_run_id, "limit": 20}
        )
        assert graph.status_code == 200
        graph_nodes = graph.json()["nodes"]
        assert any(
            test_run_id in str(row.get("name") or "")
            or test_run_id
            in str((row.get("properties") or {}).get("test_run_id") or "")
            for row in graph_nodes
        )

        node_id = next(
            row["id"]
            for row in graph_nodes
            if "PytestDisease" in (row.get("labels") or [row.get("label")])
        )
        detail = await client.get(f"/graph/entity/{node_id}")
        assert detail.status_code == 200
        assert str(detail.json()["entity"]["id"]) == str(node_id)
        assert "PytestDisease" in detail.json()["entity"]["labels"]
        assert detail.json()["entity"]["name"].startswith(test_run_id)

        created = await client.post(
            "/admin/graph/nodes",
            json={
                "label": "PytestGuideline",
                "properties": {
                    "name": f"{test_run_id}_指南",
                    "test_run_id": test_run_id,
                },
            },
        )
        assert created.status_code == 200
        new_node_id = created.json()["data"]["id"]

        updated = await client.put(
            f"/admin/graph/nodes/{new_node_id}",
            json={"properties": {"summary": "updated", "test_run_id": test_run_id}},
        )
        assert updated.status_code == 200
        assert updated.json()["ok"] is True

        created_rel = await client.post(
            "/admin/graph/relationships",
            json={
                "source_id": new_node_id,
                "target_id": node_id,
                "rel_type": "SUPPORTS",
                "properties": {"test_run_id": test_run_id},
            },
        )
        assert created_rel.status_code == 200

        cypher = await client.post(
            "/admin/graph/execute-cypher",
            json={"query": "MATCH (n) RETURN count(n) AS total LIMIT 1"},
        )
        assert cypher.status_code == 200
        assert cypher.json()["ok"] is True

        quality_report = await client.get(
            "/admin/graph/quality-report",
            params={"max_nodes": 100000, "max_relationships": 200000},
        )
        assert quality_report.status_code == 200
        assert quality_report.json()["ok"] is True
        assert quality_report.json()["data"]["total_nodes"] >= 2
        assert quality_report.json()["data"]["total_relationships"] >= 1

        deleted_rel = await client.delete(
            f"/admin/graph/relationships/{created_rel.json()['data']['rel_id']}"
        )
        assert deleted_rel.status_code == 200
        deleted_node = await client.delete(f"/admin/graph/nodes/{new_node_id}")
        assert deleted_node.status_code == 200
    finally:
        await client.aclose()
        await neo4j_client.run_cypher(
            "MATCH (n {test_run_id: $test_run_id}) DETACH DELETE n",
            {"test_run_id": test_run_id},
        )


class _MilvusVectorStatsAdapter:
    """只满足文档统计接口需要的 Milvus 向量库适配器。"""

    def __init__(self, collection_name: str) -> None:
        from pymilvus import MilvusClient

        conf = Config()
        self.client = MilvusClient(
            uri=f"http://{conf.MILVUS_HOST}:{conf.MILVUS_PORT}",
            db_name=conf.MILVUS_DATABASE_NAME,
        )
        self.collection_name = collection_name
        if not self.client.has_collection(collection_name=self.collection_name):
            self.client.create_collection(
                collection_name=self.collection_name, dimension=4
            )


@pytest.mark.asyncio
async def test_milvus_stats_routes_roundtrip(test_run_id) -> None:
    await _require_neo4j_and_milvus()

    doc_collection = f"{test_run_id}_docs"
    kg_collection = f"{test_run_id}_kg"
    vector_store = _MilvusVectorStatsAdapter(doc_collection)
    kg_store = KgMilvusStore(collection_name=kg_collection)
    client = _build_real_app(
        neo4j_override=neo4j_client,
        get_shared_vector_store=lambda: vector_store,
        get_shared_kg_milvus_store=lambda: kg_store,
    )

    try:
        document_stats = client.get("/admin/documents/stats")
        assert document_stats.status_code == 200
        assert document_stats.json()["success"] is True
        assert document_stats.json()["collection_name"] == doc_collection

        graph_status = client.get("/admin/graph/embed-status")
        assert graph_status.status_code == 200
        assert graph_status.json()["ok"] is True
        assert graph_status.json()["data"]["collection_name"] == kg_collection
    finally:
        if vector_store.client.has_collection(collection_name=doc_collection):
            vector_store.client.drop_collection(collection_name=doc_collection)
        if kg_store.client.has_collection(collection_name=kg_collection):
            kg_store.client.drop_collection(collection_name=kg_collection)
