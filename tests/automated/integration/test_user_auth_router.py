from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.backend.routers.user_auth import create_user_auth_router
from app.backend.services.user_auth_service import UserAuthService


class LogoutUserAuthService(UserAuthService):
    def __init__(self) -> None:
        self.revoked_tokens: list[str] = []

    async def authenticate_token(self, token: str):
        if token in self.revoked_tokens or token != "valid-token":
            return None
        return {"user_id": "user-1", "username": "alice"}

    async def logout(self, token: str):
        self.revoked_tokens.append(token)
        return {"ok": True, "message": "已退出登录"}


def test_logout_is_idempotent_for_revoked_and_invalid_tokens() -> None:
    service = LogoutUserAuthService()
    app = FastAPI()
    app.state.user_auth_service = service
    app.include_router(create_user_auth_router(service))
    client = TestClient(app)

    first = client.post("/auth/logout", headers={"Authorization": "Bearer valid-token"})
    second = client.post(
        "/auth/logout", headers={"Authorization": "Bearer valid-token"}
    )
    invalid = client.post(
        "/auth/logout", headers={"Authorization": "Bearer invalid-token"}
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert invalid.status_code == 200
    assert first.json()["ok"] is True
    assert second.json()["ok"] is True
    assert invalid.json()["ok"] is True
