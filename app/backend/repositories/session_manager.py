"""session_manager 模块。

提供会话和消息表的异步数据访问能力。
"""


from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from app.backend.repositories.database import fetch_all, fetch_one, execute, insert_and_get_last_id


class SessionManager:
    """封装会话表的创建、查询、更新与删除操作。"""

    @staticmethod
    async def create_session(user_id: str, title: str = "新会话") -> dict:
        """创建新的用户会话记录。

        Args:
            user_id: 会话所属用户 ID。
            title: 会话标题。

        Returns:
            dict: 新建会话信息。
        """
        session_id = str(uuid.uuid4())
        await execute(
            "INSERT INTO sessions (id, user_id, title) VALUES (:id, :user_id, :title)",
            {"id": session_id, "user_id": user_id, "title": title},
        )
        row = await fetch_one(
            "SELECT id, user_id, title, created_at, updated_at FROM sessions WHERE id=:id",
            {"id": session_id},
        )
        return {
            "id": row["id"],
            "user_id": row["user_id"],
            "title": row["title"],
            "created_at": row["created_at"].isoformat() if row and row.get("created_at") else None,
            "updated_at": row["updated_at"].isoformat() if row and row.get("updated_at") else None,
        }

    @staticmethod
    async def get_sessions(user_id: str, limit: int = 50) -> list:
        """查询指定用户的会话列表。

        Args:
            user_id: 用户 ID。
            limit: 返回条数上限。

        Returns:
            list: 会话摘要列表（按更新时间倒序）。
        """
        rows = await fetch_all(
            """
            SELECT id, user_id, title, created_at, updated_at
            FROM sessions
            WHERE user_id=:user_id
            ORDER BY updated_at DESC
            LIMIT :limit
            """,
            {"user_id": user_id, "limit": int(limit)},
        )
        return [
            {
                "id": row["id"],
                "user_id": row["user_id"],
                "title": row["title"],
                "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
                "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
            }
            for row in rows
        ]

    @staticmethod
    async def get_session(session_id: str) -> Optional[dict]:
        """根据会话 ID 查询单条会话信息。

        Args:
            session_id: 会话 ID。

        Returns:
            Optional[dict]: 命中时返回会话字典，否则返回 None。
        """
        row = await fetch_one(
            "SELECT id, user_id, title, created_at, updated_at FROM sessions WHERE id=:id",
            {"id": session_id},
        )
        if not row:
            return None
        return {
            "id": row["id"],
            "user_id": row["user_id"],
            "title": row["title"],
            "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
            "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
        }

    @staticmethod
    async def update_session_title(session_id: str, title: str) -> bool:
        """更新指定会话的标题。

        Args:
            session_id: 会话 ID。
            title: 新标题。

        Returns:
            bool: 更新成功返回 True。
        """
        affected = await execute(
            "UPDATE sessions SET title=:title WHERE id=:id",
            {"title": title, "id": session_id},
        )
        return affected > 0

    @staticmethod
    async def delete_session(session_id: str) -> bool:
        """删除指定会话及其级联消息。

        Args:
            session_id: 会话 ID。

        Returns:
            bool: 删除成功返回 True。
        """
        affected = await execute("DELETE FROM sessions WHERE id=:id", {"id": session_id})
        return affected > 0

    @staticmethod
    async def update_session_time(session_id: str) -> None:
        """刷新会话更新时间，用于列表排序和活跃度展示。

        Args:
            session_id: 会话 ID。

        Returns:
            None: 无返回值。
        """
        await execute(
            "UPDATE sessions SET updated_at=CURRENT_TIMESTAMP WHERE id=:id",
            {"id": session_id},
        )

    @staticmethod
    async def get_all_sessions(limit: int = 100, offset: int = 0) -> list:
        """分页查询全量会话及消息计数。

        Args:
            limit: 返回条数上限。
            offset: 分页偏移量。

        Returns:
            list: 会话与消息数量的聚合列表。
        """
        rows = await fetch_all(
            """
            SELECT s.id, s.user_id, s.title, s.created_at, s.updated_at,
                   COUNT(m.id) as message_count
            FROM sessions s
            LEFT JOIN messages m ON s.id = m.session_id
            GROUP BY s.id
            ORDER BY s.updated_at DESC
            LIMIT :limit OFFSET :offset
            """,
            {"limit": int(limit), "offset": int(offset)},
        )
        return [
            {
                "id": row["id"],
                "user_id": row["user_id"],
                "title": row["title"],
                "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
                "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
                "message_count": row.get("message_count") or 0,
            }
            for row in rows
        ]

    @staticmethod
    async def get_total_sessions_count() -> int:
        """统计会话总数。

        Returns:
            int: 会话总数。
        """
        row = await fetch_one("SELECT COUNT(*) as count FROM sessions")
        return int((row or {}).get("count") or 0)


class MessageManager:
    """封装会话消息的写入、查询与回滚操作。"""

    @staticmethod
    async def add_message(session_id: str, role: str, content: str, think_content: str = "") -> dict:
        """向会话追加一条消息并更新时间。

        Args:
            session_id: 会话 ID。
            role: 消息角色（user/assistant 等）。
            content: 消息正文。
            think_content: 可选思考内容。

        Returns:
            dict: 新增消息摘要。
        """
        message_id = await insert_and_get_last_id(
            """
            INSERT INTO messages (session_id, role, content, think_content)
            VALUES (:session_id, :role, :content, :think_content)
            """,
            {
                "session_id": session_id,
                "role": role,
                "content": content,
                "think_content": think_content,
            },
        )
        await SessionManager.update_session_time(session_id)
        return {
            "id": message_id,
            "session_id": session_id,
            "role": role,
            "content": content,
            "think_content": think_content,
            "created_at": datetime.now().isoformat(),
        }

    @staticmethod
    async def get_messages(session_id: str) -> list:
        """查询会话下全部消息（按时间正序）。

        Args:
            session_id: 会话 ID。

        Returns:
            list: 消息列表。
        """
        rows = await fetch_all(
            """
            SELECT id, session_id, role, content, think_content, created_at
            FROM messages WHERE session_id=:session_id ORDER BY created_at ASC
            """,
            {"session_id": session_id},
        )
        return [
            {
                "id": row["id"],
                "session_id": row["session_id"],
                "role": row["role"],
                "content": row["content"],
                "think_content": row.get("think_content") or "",
                "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
            }
            for row in rows
        ]

    @staticmethod
    async def count_messages(session_id: str) -> int:
        """统计会话下已持久化消息数量。"""
        row = await fetch_one(
            "SELECT COUNT(*) as count FROM messages WHERE session_id=:session_id",
            {"session_id": session_id},
        )
        return int((row or {}).get("count") or 0)

    @staticmethod
    async def delete_messages(session_id: str) -> bool:
        """删除指定会话下的所有消息。

        Args:
            session_id: 会话 ID。

        Returns:
            bool: 删除成功返回 True。
        """
        affected = await execute("DELETE FROM messages WHERE session_id=:session_id", {"session_id": session_id})
        return affected > 0

    @staticmethod
    async def rollback_last_question(session_id: str) -> dict:
        """回滚到最近一条用户问题之前。

        会删除“最近 user 消息及其后续消息”，用于撤销最近一轮问答。

        Args:
            session_id: 会话 ID。

        Returns:
            dict: 回滚结果，包含是否成功、删除数量和问题文本。
        """
        row = await fetch_one(
            """
            SELECT id, content
            FROM messages
            WHERE session_id=:session_id AND role='user'
            ORDER BY id DESC
            LIMIT 1
            """,
            {"session_id": session_id},
        )
        if not row:
            return {"ok": False, "removed_count": 0, "question": ""}
        last_user_id = int(row["id"])
        question = str(row.get("content") or "")
        removed = await execute(
            "DELETE FROM messages WHERE session_id=:session_id AND id>=:last_user_id",
            {"session_id": session_id, "last_user_id": last_user_id},
        )
        await SessionManager.update_session_time(session_id)
        return {"ok": removed > 0, "removed_count": int(removed or 0), "question": question}

    @staticmethod
    async def rollback_to_question(session_id: str, question_message_id: int) -> dict:
        """回滚到指定问题消息位置。

        Args:
            session_id: 会话 ID。
            question_message_id: 目标用户问题消息 ID。

        Returns:
            dict: 回滚结果，包含是否成功、删除数量和问题文本。
        """
        row = await fetch_one(
            """
            SELECT id, content
            FROM messages
            WHERE session_id=:session_id AND id=:id AND role='user'
            LIMIT 1
            """,
            {"session_id": session_id, "id": int(question_message_id)},
        )
        if not row:
            return {"ok": False, "removed_count": 0, "question": ""}
        removed = await execute(
            "DELETE FROM messages WHERE session_id=:session_id AND id>=:id",
            {"session_id": session_id, "id": int(row["id"])},
        )
        await SessionManager.update_session_time(session_id)
        return {
            "ok": removed > 0,
            "removed_count": int(removed or 0),
            "question": str(row.get("content") or ""),
        }
