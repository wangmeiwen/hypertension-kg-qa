"""
管理员认证模块（服务端会话版）。

本模块提供轻量级管理员登录态管理能力，基于内存会话实现：
- 管理员账号密码校验
- 会话签发与过期清理
- 请求作用域 token 绑定与读取
- 登录态判断与退出登录

主要函数：
- verify_login: 校验凭据并创建会话
- get_current_admin_session: 获取当前请求绑定会话
- is_logged_in: 判断当前请求是否已登录
- logout: 注销当前请求对应会话
"""

import os
import secrets
import time
from contextvars import ContextVar, Token
from typing import Any, Dict, Optional


DEFAULT_ADMIN = {
    "username": os.getenv("ADMIN_USERNAME", ""),
    "password": os.getenv("ADMIN_PASSWORD", ""),
}

SESSION_TTL_SECONDS = int(os.getenv("ADMIN_SESSION_TTL_SECONDS", "28800"))
_admin_sessions: Dict[str, Dict[str, Any]] = {}
_current_admin_token: ContextVar[Optional[str]] = ContextVar("current_admin_token", default=None)


def _purge_expired_sessions() -> None:
    """
    清理已过期的管理员会话。

    Returns:
        None
    """
    now = int(time.time())
    expired = [token for token, session in _admin_sessions.items() if int(session.get("expires_at", 0)) <= now]
    for token in expired:
        _admin_sessions.pop(token, None)


def set_current_admin_token(token: Optional[str]) -> Token[Optional[str]]:
    """
    在请求作用域内绑定当前管理员 token。

    Args:
        token: 当前请求关联的管理员会话 token，可为 None

    Returns:
        Token[Optional[str]]: ContextVar 的 token 句柄，用于后续 reset
    """
    return _current_admin_token.set(token)


def reset_current_admin_token(token_handle: Token[Optional[str]]) -> None:
    """
    重置请求作用域 token 绑定。

    Args:
        token_handle: 由 set_current_admin_token 返回的 token 句柄

    Returns:
        None
    """
    _current_admin_token.reset(token_handle)


def verify_login(username: str, password: str) -> Optional[str]:
    """
    验证登录并创建服务端会话。

    Args:
        username: 管理员用户名
        password: 管理员密码

    Returns:
        Optional[str]: 登录成功返回会话 token，失败返回 None
    """
    _purge_expired_sessions()
    if not DEFAULT_ADMIN["username"] or not DEFAULT_ADMIN["password"]:
        return None
    if username != DEFAULT_ADMIN["username"] or password != DEFAULT_ADMIN["password"]:
        return None

    now = int(time.time())
    token = secrets.token_urlsafe(32)
    _admin_sessions[token] = {
        "username": username,
        "issued_at": now,
        "expires_at": now + SESSION_TTL_SECONDS,
    }
    return token


def get_current_admin_session() -> Optional[Dict[str, Any]]:
    """
    获取当前请求关联的管理员会话。

    Returns:
        Optional[Dict[str, Any]]: 会话信息字典，不存在则返回 None
    """
    _purge_expired_sessions()
    token = _current_admin_token.get()
    if not token:
        return None
    return _admin_sessions.get(token)


def get_current_admin_user() -> Optional[str]:
    """
    获取当前请求关联的管理员用户名。

    Returns:
        Optional[str]: 用户名，未登录时返回 None
    """
    session = get_current_admin_session()
    if not session:
        return None
    return str(session.get("username") or "")


def is_logged_in() -> bool:
    """
    检查当前请求是否已通过管理员认证。

    Returns:
        bool: 已登录返回 True，否则返回 False
    """
    return get_current_admin_session() is not None


def logout() -> bool:
    """
    退出当前请求的管理员会话。

    Returns:
        bool: 成功删除会话返回 True，否则返回 False
    """
    token = _current_admin_token.get()
    if not token:
        return False
    return _admin_sessions.pop(token, None) is not None


