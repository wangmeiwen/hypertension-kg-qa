"""普通用户账号与登录会话仓储。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.backend.repositories.database import execute, fetch_one


class UserAuthRepository:
    """封装用户和服务端登录会话的 MySQL 操作。"""

    async def create_user(
        self,
        *,
        user_id: str,
        username: str,
        username_key: str,
        password_hash: str,
    ) -> bool:
        affected = await execute(
            """
            INSERT IGNORE INTO users (
                id, username, username_key, password_hash, status
            ) VALUES (
                :id, :username, :username_key, :password_hash, 'active'
            )
            """,
            {
                "id": user_id,
                "username": username,
                "username_key": username_key,
                "password_hash": password_hash,
            },
        )
        return affected == 1

    async def get_user_by_username_key(
        self, username_key: str
    ) -> dict[str, Any] | None:
        return await fetch_one(
            """
            SELECT id, username, username_key, password_hash, status,
                   created_at, updated_at
            FROM users
            WHERE username_key = :username_key
            LIMIT 1
            """,
            {"username_key": username_key},
        )

    async def create_session(
        self,
        *,
        token_hash: str,
        user_id: str,
        expires_at: datetime,
    ) -> None:
        await execute(
            """
            INSERT INTO user_sessions (token_hash, user_id, expires_at)
            VALUES (:token_hash, :user_id, :expires_at)
            """,
            {
                "token_hash": token_hash,
                "user_id": user_id,
                "expires_at": expires_at,
            },
        )

    async def get_active_session(self, token_hash: str) -> dict[str, Any] | None:
        return await fetch_one(
            """
            SELECT s.token_hash, s.user_id, s.expires_at,
                   u.username, u.status
            FROM user_sessions AS s
            JOIN users AS u ON u.id = s.user_id
            WHERE s.token_hash = :token_hash
              AND s.revoked_at IS NULL
              AND s.expires_at > UTC_TIMESTAMP(6)
              AND u.status = 'active'
            LIMIT 1
            """,
            {"token_hash": token_hash},
        )

    async def revoke_session(self, token_hash: str) -> bool:
        affected = await execute(
            """
            UPDATE user_sessions
            SET revoked_at = UTC_TIMESTAMP(6)
            WHERE token_hash = :token_hash AND revoked_at IS NULL
            """,
            {"token_hash": token_hash},
        )
        return affected == 1
