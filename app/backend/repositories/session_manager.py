"""
会话和消息管理模块。

本模块提供会话和消息的 CRUD 操作，支持：
- 会话创建、查询、更新、删除
- 消息添加、查询、删除
- 消息回撤功能

主要类：
- SessionManager: 会话管理器
- MessageManager: 消息管理器

依赖：
- app.backend.repositories.database: 数据库连接管理
"""

import uuid
from datetime import datetime
from typing import Optional

from app.backend.repositories.database import get_db


class SessionManager:
    """
    会话管理器。

    提供会话的创建、查询、更新、删除等操作。
    """

    @staticmethod
    def create_session(user_id: str, title: str = "新会话") -> dict:
        """
        创建新会话。

        Args:
            user_id: 用户 ID
            title: 会话标题，默认为 "新会话"

        Returns:
            会话信息字典
        """
        session_id = str(uuid.uuid4())
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO sessions (id, user_id, title) VALUES (%s, %s, %s)",
                    (session_id, user_id, title)
                )
                conn.commit()

                cursor.execute(
                    "SELECT id, user_id, title, created_at, updated_at FROM sessions WHERE id = %s",
                    (session_id,)
                )
                row = cursor.fetchone()
                return {
                    "id": row["id"],
                    "user_id": row["user_id"],
                    "title": row["title"],
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                    "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None
                }

    @staticmethod
    def get_sessions(user_id: str, limit: int = 50) -> list:
        """
        获取用户的所有会话列表。

        Args:
            user_id: 用户 ID
            limit: 返回数量限制，默认 50

        Returns:
            会话信息列表
        """
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, user_id, title, created_at, updated_at
                    FROM sessions
                    WHERE user_id = %s
                    ORDER BY updated_at DESC
                    LIMIT %s
                    """,
                    (user_id, limit)
                )
                rows = cursor.fetchall()
                return [
                    {
                        "id": row["id"],
                        "user_id": row["user_id"],
                        "title": row["title"],
                        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None
                    }
                    for row in rows
                ]

    @staticmethod
    def get_session(session_id: str) -> Optional[dict]:
        """
        获取单个会话详情。

        Args:
            session_id: 会话 ID

        Returns:
            会话信息字典，不存在则返回 None
        """
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT id, user_id, title, created_at, updated_at FROM sessions WHERE id = %s",
                    (session_id,)
                )
                row = cursor.fetchone()
                if row:
                    return {
                        "id": row["id"],
                        "user_id": row["user_id"],
                        "title": row["title"],
                        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None
                    }
                return None

    @staticmethod
    def update_session_title(session_id: str, title: str) -> bool:
        """
        更新会话标题。

        Args:
            session_id: 会话 ID
            title: 新标题

        Returns:
            是否更新成功
        """
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "UPDATE sessions SET title = %s WHERE id = %s",
                    (title, session_id)
                )
                conn.commit()
                return cursor.rowcount > 0

    @staticmethod
    def delete_session(session_id: str) -> bool:
        """
        删除会话及其所有消息（级联删除）。

        Args:
            session_id: 会话 ID

        Returns:
            是否删除成功
        """
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM sessions WHERE id = %s", (session_id,))
                conn.commit()
                return cursor.rowcount > 0

    @staticmethod
    def update_session_time(session_id: str) -> None:
        """
        更新会话的更新时间。

        Args:
            session_id: 会话 ID
        """
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "UPDATE sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = %s",
                    (session_id,)
                )
                conn.commit()

    @staticmethod
    def get_all_sessions(limit: int = 100, offset: int = 0) -> list:
        """
        获取所有会话（用于管理员后台）。

        Args:
            limit: 返回数量限制，默认 100
            offset: 偏移量，默认 0

        Returns:
            会话信息列表
        """
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT s.id, s.user_id, s.title, s.created_at, s.updated_at,
                           COUNT(m.id) as message_count
                    FROM sessions s
                    LEFT JOIN messages m ON s.id = m.session_id
                    GROUP BY s.id
                    ORDER BY s.updated_at DESC
                    LIMIT %s OFFSET %s
                    """,
                    (limit, offset)
                )
                rows = cursor.fetchall()
                return [
                    {
                        "id": row["id"],
                        "user_id": row["user_id"],
                        "title": row["title"],
                        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
                        "message_count": row["message_count"]
                    }
                    for row in rows
                ]

    @staticmethod
    def get_total_sessions_count() -> int:
        """
        获取会话总数。

        Returns:
            会话总数
        """
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) as count FROM sessions")
                result = cursor.fetchone()
                return result["count"] if result else 0


class MessageManager:
    """
    消息管理器。

    提供消息的添加、查询、删除、回撤等操作。
    """

    @staticmethod
    def add_message(session_id: str, role: str, content: str, think_content: str = "") -> dict:
        """
        添加消息到会话。

        Args:
            session_id: 会话 ID
            role: 消息角色（user/assistant）
            content: 消息内容
            think_content: 思考内容，可选

        Returns:
            消息信息字典
        """
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO messages (session_id, role, content, think_content)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (session_id, role, content, think_content)
                )
                conn.commit()
                message_id = cursor.lastrowid

                SessionManager.update_session_time(session_id)

                return {
                    "id": message_id,
                    "session_id": session_id,
                    "role": role,
                    "content": content,
                    "think_content": think_content,
                    "created_at": datetime.now().isoformat()
                }

    @staticmethod
    def get_messages(session_id: str) -> list:
        """
        获取会话的所有消息。

        Args:
            session_id: 会话 ID

        Returns:
            消息信息列表
        """
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, session_id, role, content, think_content, created_at
                    FROM messages
                    WHERE session_id = %s
                    ORDER BY created_at ASC
                    """,
                    (session_id,)
                )
                rows = cursor.fetchall()
                return [
                    {
                        "id": row["id"],
                        "session_id": row["session_id"],
                        "role": row["role"],
                        "content": row["content"],
                        "think_content": row["think_content"] or "",
                        "created_at": row["created_at"].isoformat() if row["created_at"] else None
                    }
                    for row in rows
                ]

    @staticmethod
    def delete_messages(session_id: str) -> bool:
        """
        删除会话的所有消息。

        Args:
            session_id: 会话 ID

        Returns:
            是否删除成功
        """
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM messages WHERE session_id = %s", (session_id,))
                conn.commit()
                return cursor.rowcount > 0

    @staticmethod
    def rollback_last_question(session_id: str) -> dict:
        """
        回撤会话中最后一个用户问题及其后续回答。

        Args:
            session_id: 会话 ID

        Returns:
            回撤结果字典，包含 ok、removed_count、question 字段
        """
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, content
                    FROM messages
                    WHERE session_id = %s AND role = 'user'
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (session_id,)
                )
                last_user_row = cursor.fetchone()
                if not last_user_row:
                    return {"ok": False, "removed_count": 0, "question": ""}

                last_user_id = int(last_user_row["id"])
                last_question = str(last_user_row.get("content") or "")

                cursor.execute(
                    "DELETE FROM messages WHERE session_id = %s AND id >= %s",
                    (session_id, last_user_id)
                )
                removed_count = int(cursor.rowcount or 0)
                conn.commit()

                SessionManager.update_session_time(session_id)
                return {
                    "ok": removed_count > 0,
                    "removed_count": removed_count,
                    "question": last_question,
                }

    @staticmethod
    def rollback_to_question(session_id: str, question_message_id: int) -> dict:
        """
        回撤到指定用户问题，删除该问题及之后的所有消息。

        Args:
            session_id: 会话 ID
            question_message_id: 用户问题消息 ID

        Returns:
            回撤结果字典，包含 ok、removed_count、question 字段
        """
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, content
                    FROM messages
                    WHERE session_id = %s AND id = %s AND role = 'user'
                    LIMIT 1
                    """,
                    (session_id, question_message_id)
                )
                row = cursor.fetchone()
                if not row:
                    return {"ok": False, "removed_count": 0, "question": ""}

                cursor.execute(
                    "DELETE FROM messages WHERE session_id = %s AND id >= %s",
                    (session_id, int(row["id"]))
                )
                removed_count = int(cursor.rowcount or 0)
                conn.commit()

                SessionManager.update_session_time(session_id)
                return {
                    "ok": removed_count > 0,
                    "removed_count": removed_count,
                    "question": str(row.get("content") or ""),
                }

