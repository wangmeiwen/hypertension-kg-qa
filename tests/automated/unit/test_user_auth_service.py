from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import pytest

from app.backend.infrastructure.errors import (
    ConflictError,
    InvalidRequestError,
    UnauthorizedError,
)
from app.backend.security.user_password import (
    hash_user_password,
    verify_user_password,
)
from app.backend.services.user_auth_service import UserAuthService


class UserAuthRepositoryStub:
    def __init__(self) -> None:
        self.users: dict[str, dict[str, Any]] = {}
        self.sessions: dict[str, dict[str, Any]] = {}

    async def create_user(
        self,
        *,
        user_id: str,
        username: str,
        username_key: str,
        password_hash: str,
    ) -> bool:
        if username_key in self.users:
            return False
        self.users[username_key] = {
            "id": user_id,
            "username": username,
            "username_key": username_key,
            "password_hash": password_hash,
            "status": "active",
        }
        return True

    async def get_user_by_username_key(
        self, username_key: str
    ) -> dict[str, Any] | None:
        return self.users.get(username_key)

    async def create_session(
        self, *, token_hash: str, user_id: str, expires_at: datetime
    ) -> None:
        user = next(row for row in self.users.values() if row["id"] == user_id)
        self.sessions[token_hash] = {
            "token_hash": token_hash,
            "user_id": user_id,
            "username": user["username"],
            "status": user["status"],
            "expires_at": expires_at,
        }

    async def get_active_session(self, token_hash: str) -> dict[str, Any] | None:
        return self.sessions.get(token_hash)

    async def revoke_session(self, token_hash: str) -> bool:
        return self.sessions.pop(token_hash, None) is not None


def test_user_password_hash_is_salted_and_verifiable() -> None:
    first = hash_user_password("password123")
    second = hash_user_password("password123")

    assert first != second
    assert verify_user_password("password123", first) is True
    assert verify_user_password("wrong-password", first) is False
    assert verify_user_password("password123", "broken") is False


def test_user_register_login_authenticate_and_logout() -> None:
    async def scenario() -> None:
        repository = UserAuthRepositoryStub()
        service = UserAuthService(repository)  # type: ignore[arg-type]

        registered = await service.register(
            {"username": "Alice_01", "password": "password123"}
        )
        token = registered["data"]["token"]
        user = await service.authenticate_token(token)
        assert user == {
            "user_id": registered["data"]["user"]["id"],
            "username": "Alice_01",
        }

        logged_in = await service.login(
            {"username": "alice_01", "password": "password123"}
        )
        assert logged_in["data"]["user"] == registered["data"]["user"]

        await service.logout(token)
        assert await service.authenticate_token(token) is None

    asyncio.run(scenario())


def test_user_auth_rejects_invalid_duplicate_and_wrong_credentials() -> None:
    async def scenario() -> None:
        repository = UserAuthRepositoryStub()
        service = UserAuthService(repository)  # type: ignore[arg-type]

        with pytest.raises(InvalidRequestError):
            await service.register({"username": "a", "password": "password123"})
        with pytest.raises(InvalidRequestError):
            await service.register({"username": "alice", "password": "password"})

        await service.register({"username": "alice", "password": "password123"})
        with pytest.raises(ConflictError):
            await service.register({"username": "ALICE", "password": "password123"})
        with pytest.raises(UnauthorizedError):
            await service.login({"username": "alice", "password": "password999"})

    asyncio.run(scenario())
