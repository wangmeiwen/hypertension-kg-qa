"""管理员认证服务。"""

from __future__ import annotations

from typing import Any

from app.common.auth_simple import get_current_admin_user, is_logged_in, logout, verify_login


class AdminAuthService:
    """封装管理员登录状态相关业务。"""

    async def admin_login(self, data: dict[str, Any]) -> dict[str, Any]:
        """校验管理员账号密码并返回登录 token。

        Args:
            data: 包含 username 和 password 的登录 payload。

        Returns:
            dict[str, Any]: 登录成功时包含 token，失败时返回错误信息。
        """
        username = str(data.get("username") or "").strip()
        password = str(data.get("password") or "")
        token = verify_login(username, password)
        if token:
            return {"ok": True, "message": "登录成功", "token": token}
        return {"ok": False, "error": "用户名或密码错误"}

    async def admin_status(self) -> dict[str, Any]:
        """检查当前管理员登录状态。

        Returns:
            dict[str, Any]: 登录状态和当前管理员用户信息。
        """
        return {"ok": True, "logged_in": is_logged_in(), "user": get_current_admin_user()}

    async def admin_logout(self) -> dict[str, Any]:
        """退出当前管理员登录状态。

        Returns:
            dict[str, Any]: 退出结果。
        """
        logout()
        return {"ok": True, "message": "已退出登录"}
