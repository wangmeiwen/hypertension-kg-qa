from __future__ import annotations

import json

import pytest

from app.backend.testing.deterministic_support import (
    build_deterministic_test_overrides,
)

pytestmark = pytest.mark.integration


def test_user_routes_reject_missing_login(app_client_factory) -> None:
    overrides = build_deterministic_test_overrides()
    overrides.authenticated_user = None
    client = app_client_factory(overrides=overrides)

    response = client.get("/sessions")

    assert response.status_code == 401
    assert response.json()["error"] == "用户未登录或登录已过期"


def test_create_app_mounts_real_routes_and_deterministic_overrides(
    admin_app_client,
) -> None:
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

    agent_run = admin_app_client.post(
        "/agent/run", json={"input": "高血压患者需要注意什么？", "user_id": "user-1"}
    )
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
    response = admin_app_client.post(
        "/process", json={"input": "如何控制血压？", "user_id": "user-stream"}
    )
    assert response.status_code == 200
    rows = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    assert rows[0]["type"] == "job"
    assert any(row["type"] == "reply" for row in rows)
    assert rows[-1]["type"] == "done"


def test_session_reconnect_recovers_persisted_stream_message_ids(
    admin_app_client,
) -> None:
    """流结束后重新读取会话时，消息 ID 应与 persisted 事件一致。"""
    user_id = "user-reconnect"
    response = admin_app_client.post(
        "/process_with_session",
        json={"input": "如何控制血压？", "user_id": user_id},
    )
    assert response.status_code == 200
    assert response.headers["x-stream-schema-version"] == "1.0.0"
    rows = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    session_event = next(row for row in rows if row["type"] == "session")
    persisted_event = next(row for row in rows if row["type"] == "persisted")
    assert rows[-1]["type"] == "done"

    detail = admin_app_client.get(
        f"/sessions/{session_event['session_id']}",
        params={"user_id": user_id},
    )
    assert detail.status_code == 200
    messages = detail.json()["data"]["messages"]
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[0]["id"] == persisted_event["user_message_id"]
    assert messages[1]["id"] == persisted_event["assistant_message_id"]


def test_create_app_admin_login_route_uses_real_auth_service(
    monkeypatch, app_client_factory
) -> None:
    """验证应用工厂中的管理员登录路由接入真实认证服务。"""
    import app.backend.security.admin_auth as auth_module

    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "secret")
    monkeypatch.setattr(
        auth_module, "DEFAULT_ADMIN", {"username": "admin", "password": "secret"}
    )
    client = app_client_factory()

    failed = client.post("/admin/login", json={"username": "admin", "password": "bad"})
    assert failed.status_code == 200
    assert failed.json()["ok"] is False

    login = client.post(
        "/admin/login", json={"username": "admin", "password": "secret"}
    )
    assert login.status_code == 200
    assert login.json()["ok"] is True
