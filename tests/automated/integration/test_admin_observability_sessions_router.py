from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.backend.dependencies.admin_auth import require_admin_login
from app.backend.routers.admin_observability import create_admin_observability_router
from app.backend.routers.admin_sessions import create_admin_sessions_router


class AdminSessionsServiceStub:
    """覆盖后台会话 router 所需方法的服务桩。"""

    async def admin_get_sessions(self, limit=100, offset=0):
        return {"ok": True, "data": [{"id": "s1"}], "total": 1}

    async def admin_get_session_messages(self, session_id):
        return {"ok": True, "data": {"session_id": session_id}}

    async def admin_get_session_observability(self, session_id):
        return {"ok": True, "data": {"turns": [{"turn_id": "t1", "session_id": session_id}]}}

    async def admin_get_session_memory(self, session_id):
        return {"ok": True, "data": {"session_id": session_id}}

    async def admin_rebuild_session_memory(self, session_id):
        return {"ok": True, "data": {"session_id": session_id, "rebuilt": True}}

    async def admin_clear_session_messages(self, session_id):
        return {"ok": True, "data": {"session_id": session_id, "cleared": True}}

    async def admin_delete_session(self, session_id):
        return {"ok": True, "data": {"session_id": session_id, "deleted": True}}


class AdminObservabilityServiceStub:
    """覆盖后台观测 router 所需方法的服务桩。"""

    async def admin_agent_observability_summary(self, limit=200):
        return {"ok": True, "data": {"total_runs": limit}}

    async def admin_agent_observability_runs(self, limit=50):
        return {"ok": True, "data": [{"session_id": "s1", "limit": limit}]}


def create_client() -> TestClient:
    """构建同时挂载后台会话与观测 router 的测试客户端。"""
    app = FastAPI()
    app.include_router(create_admin_sessions_router(AdminSessionsServiceStub()))
    app.include_router(create_admin_observability_router(AdminObservabilityServiceStub()))
    app.dependency_overrides[require_admin_login] = lambda: None
    return TestClient(app)


def test_admin_sessions_and_observability_routes():
    client = create_client()

    sessions = client.get("/admin/sessions")
    assert sessions.status_code == 200
    assert sessions.json()["data"][0]["id"] == "s1"

    detail = client.get("/admin/sessions/s1/observability")
    assert detail.status_code == 200
    assert detail.json()["data"]["turns"][0]["turn_id"] == "t1"

    summary = client.get("/admin/agent/observability-summary", params={"limit": 10})
    assert summary.status_code == 200
    assert summary.json()["data"]["total_runs"] == 10

    runs = client.get("/admin/agent/observability-runs", params={"limit": 3})
    assert runs.status_code == 200
    assert runs.json()["data"][0]["limit"] == 3
