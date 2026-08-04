from __future__ import annotations

import asyncio
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError

import app.backend.repositories.user_auth_repository as repository_module
from app.backend.repositories.user_auth_repository import UserAuthRepository


class MysqlError(Exception):
    pass


def _integrity_error(code: int) -> IntegrityError:
    return IntegrityError(
        "INSERT INTO users",
        {},
        MysqlError(code, "database constraint failed"),
    )


def test_create_user_only_converts_duplicate_key_to_conflict(monkeypatch) -> None:
    statements: list[str] = []

    async def duplicate_execute(statement: str, params: dict[str, Any]) -> int:
        _ = params
        statements.append(statement)
        raise _integrity_error(1062)

    monkeypatch.setattr(repository_module, "execute", duplicate_execute)
    repository = UserAuthRepository()
    created = asyncio.run(
        repository.create_user(
            user_id="user-1",
            username="Alice",
            username_key="alice",
            password_hash="hash",
        )
    )

    assert created is False
    assert "INSERT IGNORE" not in statements[0]

    async def invalid_execute(statement: str, params: dict[str, Any]) -> int:
        _ = statement, params
        raise _integrity_error(1406)

    monkeypatch.setattr(repository_module, "execute", invalid_execute)
    with pytest.raises(IntegrityError):
        asyncio.run(
            repository.create_user(
                user_id="user-2",
                username="Bob",
                username_key="bob",
                password_hash="hash",
            )
        )


def test_active_session_query_requires_active_user(monkeypatch) -> None:
    captured_sql = ""

    async def fake_fetch_one(statement: str, params: dict[str, Any]):
        nonlocal captured_sql
        _ = params
        captured_sql = statement
        return None

    monkeypatch.setattr(repository_module, "fetch_one", fake_fetch_one)
    asyncio.run(UserAuthRepository().get_active_session("token-hash"))

    assert "JOIN users AS u" in captured_sql
    assert "u.status = 'active'" in captured_sql
