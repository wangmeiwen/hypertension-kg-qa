from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.backend.routers.sessions_history import create_sessions_router
from app.backend.dependencies.user_auth import require_user_login


class SessionsHistoryServiceStub:
    """覆盖会话历史 router 所需方法的服务桩。"""

    async def create_session(self, data):
        return {"ok": True, "data": data}

    async def list_sessions(self, user_id="", limit=50):
        return {"ok": True, "data": [{"user_id": user_id, "limit": limit}]}

    async def get_session(self, session_id, user_id=""):
        return {"ok": True, "data": {"id": session_id, "user_id": user_id}}

    async def update_session(self, session_id, data):
        return {"ok": True, "data": {"id": session_id, **data}}

    async def delete_session(self, session_id, user_id=""):
        return {"ok": True, "data": {"id": session_id, "user_id": user_id}}

    async def add_message(self, session_id, data):
        return {"ok": True, "data": {"session_id": session_id, **data}}

    async def get_messages(self, session_id, user_id=""):
        return {"ok": True, "data": {"session_id": session_id, "user_id": user_id}}

    async def rollback_to_question(self, session_id, data):
        return {"ok": True, "data": {"session_id": session_id, **data}}

    async def upload_file(self, file, user_id):
        return {
            "ok": True,
            "data": {"filename": file.filename, "owner_user_id": user_id},
        }

    async def cancel_qa_job(self, job_id, data):
        return {"ok": True, "data": {"job_id": job_id, **data}}


def create_client() -> TestClient:
    """构建只挂载会话历史 router 的测试客户端。"""
    app = FastAPI()
    app.include_router(create_sessions_router(SessionsHistoryServiceStub()))
    app.dependency_overrides[require_user_login] = lambda: {
        "user_id": "u1",
        "username": "alice",
        "token": "token-1",
    }
    return TestClient(app)


def test_sessions_history_router_core_routes():
    client = create_client()

    created = client.post(
        "/sessions", json={"user_id": "spoofed-user", "title": "新建会话"}
    )
    assert created.status_code == 200
    assert created.json()["data"]["title"] == "新建会话"
    assert created.json()["data"]["user_id"] == "u1"

    sessions = client.get("/sessions", params={"user_id": "spoofed-user", "limit": 5})
    assert sessions.status_code == 200
    assert sessions.json()["data"][0]["user_id"] == "u1"

    rollback = client.post(
        "/sessions/s1/rollback-to-question", json={"user_id": "u1", "message_id": 8}
    )
    assert rollback.status_code == 200
    assert rollback.json()["data"]["message_id"] == 8

    uploaded = client.post(
        "/upload_file", files={"file": ("note.png", b"abc", "image/png")}
    )
    assert uploaded.status_code == 200
    assert uploaded.json()["data"]["filename"] == "note.png"
    assert uploaded.json()["data"]["owner_user_id"] == "u1"

    cancelled = client.post("/qa/jobs/job-1/cancel", json={"session_id": "s1"})
    assert cancelled.status_code == 200
    assert cancelled.json()["data"]["job_id"] == "job-1"
