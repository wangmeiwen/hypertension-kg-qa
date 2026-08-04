"""普通用户注册、登录与会话认证服务。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import re
import secrets
from typing import Any
import uuid

from app.backend.infrastructure.errors import (
    ConflictError,
    InvalidRequestError,
    UnauthorizedError,
)
from app.backend.repositories.user_auth_repository import UserAuthRepository
from app.backend.security.user_password import (
    hash_user_password,
    verify_user_password,
)

_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{3,32}$")
_SESSION_TTL = timedelta(days=7)
_DUMMY_PASSWORD_HASH = hash_user_password("invalid-password-0000")


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _public_user(user: dict[str, Any]) -> dict[str, str]:
    return {
        "id": str(user["id"] if "id" in user else user["user_id"]),
        "username": str(user["username"]),
    }


class UserAuthService:
    """普通用户账号服务，管理员认证继续使用独立链路。"""

    def __init__(self, repository: UserAuthRepository | None = None) -> None:
        self._repository = (
            repository if repository is not None else UserAuthRepository()
        )

    @staticmethod
    def _validated_credentials(data: dict[str, Any]) -> tuple[str, str, str]:
        username = str(data.get("username") or "").strip()
        password = str(data.get("password") or "")
        if _USERNAME_PATTERN.fullmatch(username) is None:
            raise InvalidRequestError("用户名需为 3-32 位字母、数字或下划线")
        if len(password) < 8 or len(password) > 128:
            raise InvalidRequestError("密码长度需为 8-128 位")
        if not any(char.isalpha() for char in password) or not any(
            char.isdigit() for char in password
        ):
            raise InvalidRequestError("密码必须同时包含字母和数字")
        return username, username.casefold(), password

    async def _issue_session(self, user: dict[str, Any]) -> dict[str, Any]:
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + _SESSION_TTL
        await self._repository.create_session(
            token_hash=_token_hash(token),
            user_id=str(user["id"] if "id" in user else user["user_id"]),
            expires_at=expires_at.replace(tzinfo=None),
        )
        return {
            "ok": True,
            "data": {
                "token": token,
                "expires_at": expires_at.isoformat(),
                "user": _public_user(user),
            },
        }

    async def register(self, data: dict[str, Any]) -> dict[str, Any]:
        """创建用户并直接签发登录会话。"""
        username, username_key, password = self._validated_credentials(data)
        user = {
            "id": str(uuid.uuid4()),
            "username": username,
        }
        created = await self._repository.create_user(
            user_id=user["id"],
            username=username,
            username_key=username_key,
            password_hash=hash_user_password(password),
        )
        if not created:
            raise ConflictError("用户名已存在")
        return await self._issue_session(user)

    async def login(self, data: dict[str, Any]) -> dict[str, Any]:
        """校验账号密码并签发新会话。"""
        username, username_key, password = self._validated_credentials(data)
        user = await self._repository.get_user_by_username_key(username_key)
        password_hash = (
            str(user.get("password_hash") or "")
            if user is not None
            else _DUMMY_PASSWORD_HASH
        )
        password_matches = verify_user_password(password, password_hash)
        if user is None or str(user.get("status")) != "active" or not password_matches:
            raise UnauthorizedError("用户名或密码错误")
        return await self._issue_session(user)

    async def authenticate_token(self, token: str) -> dict[str, Any] | None:
        """解析有效用户 token，不接受管理员 token 代替。"""
        if not token:
            return None
        session = await self._repository.get_active_session(_token_hash(token))
        if session is None:
            return None
        return {
            "user_id": str(session["user_id"]),
            "username": str(session["username"]),
        }

    async def logout(self, token: str) -> dict[str, Any]:
        """只撤销当前用户会话。"""
        await self._repository.revoke_session(_token_hash(token))
        return {"ok": True, "message": "已退出登录"}
