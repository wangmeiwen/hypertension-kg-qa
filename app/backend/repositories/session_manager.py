"""会话与消息异步管理。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from app.backend.repositories.database import fetch_all, fetch_one, execute, insert_and_get_last_id


class SessionManager:
    @staticmethod
    async def create_session(user_id: str, title: str = "新会话") -> dict:
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
        affected = await execute(
            "UPDATE sessions SET title=:title WHERE id=:id",
            {"title": title, "id": session_id},
        )
        return affected > 0

    @staticmethod
    async def delete_session(session_id: str) -> bool:
        affected = await execute("DELETE FROM sessions WHERE id=:id", {"id": session_id})
        return affected > 0

    @staticmethod
    async def update_session_time(session_id: str) -> None:
        await execute(
            "UPDATE sessions SET updated_at=CURRENT_TIMESTAMP WHERE id=:id",
            {"id": session_id},
        )

    @staticmethod
    async def get_all_sessions(limit: int = 100, offset: int = 0) -> list:
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
        row = await fetch_one("SELECT COUNT(*) as count FROM sessions")
        return int((row or {}).get("count") or 0)


class MessageManager:
    @staticmethod
    async def add_message(session_id: str, role: str, content: str, think_content: str = "") -> dict:
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
    async def delete_messages(session_id: str) -> bool:
        affected = await execute("DELETE FROM messages WHERE session_id=:session_id", {"session_id": session_id})
        return affected > 0

    @staticmethod
    async def rollback_last_question(session_id: str) -> dict:
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
