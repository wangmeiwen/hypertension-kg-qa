from __future__ import annotations

import json

import pytest


pytestmark = pytest.mark.integration


def test_create_app_mounts_real_routes_and_deterministic_overrides(admin_app_client) -> None:
    """验证应用工厂挂载真实路由，并使用确定性依赖返回稳定响应。"""
    readiness = admin_app_client.get("/health/readiness")
    assert readiness.status_code == 200
    assert readiness.json()["ok"] is True

    tools = admin_app_client.get("/mcp/tools")
    assert tools.status_code == 200
    assert tools.json()["tools"][0]["name"] == "kg_entity_relation_search"

    graph = admin_app_client.get("/graph/visualization")
    assert graph.status_code == 200
    assert graph.json()["nodes"][0]["properties"]["name"] == "高血压"

    entity = admin_app_client.get("/graph/entity/100")
    assert entity.status_code == 200
    assert entity.json()["entity"]["properties"]["code"] == "I10"

    agent_run = admin_app_client.post("/agent/run", json={"input": "高血压患者需要注意什么？", "user_id": "user-1"})
    assert agent_run.status_code == 200
    assert agent_run.json()["ok"] is True
    assert "基于当前测试知识库" in agent_run.json()["answer"]

    graph_labels = admin_app_client.get("/admin/graph/labels")
    assert graph_labels.status_code == 200
    assert "Disease" in graph_labels.json()["data"]

    document_stats = admin_app_client.get("/admin/documents/stats")
    assert document_stats.status_code == 200
    assert document_stats.json()["row_count"] == 12


def test_create_app_process_stream_uses_single_backend_entry(admin_app_client) -> None:
    """验证 /process 流式入口统一输出 job/reply/done 事件序列。"""
    response = admin_app_client.post("/process", json={"input": "如何控制血压？", "user_id": "user-stream"})
    assert response.status_code == 200
    rows = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    assert rows[0]["type"] == "job"
    assert any(row["type"] == "reply" for row in rows)
    assert rows[-1]["type"] == "done"


def test_create_app_admin_login_route_uses_real_auth_service(monkeypatch, app_client_factory) -> None:
    """验证应用工厂中的管理员登录路由接入真实认证服务。"""
    import app.backend.security.admin_auth as auth_module

    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "secret")
    monkeypatch.setattr(auth_module, "DEFAULT_ADMIN", {"username": "admin", "password": "secret"})
    client = app_client_factory()

    failed = client.post("/admin/login", json={"username": "admin", "password": "bad"})
    assert failed.status_code == 200
    assert failed.json()["ok"] is False

    login = client.post("/admin/login", json={"username": "admin", "password": "secret"})
    assert login.status_code == 200
    assert login.json()["ok"] is True
