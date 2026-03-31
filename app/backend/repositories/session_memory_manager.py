"""会话记忆摘要异步管理。"""

from __future__ import annotations

from app.backend.repositories.database import fetch_one, execute


class SessionMemoryManager:
    @staticmethod
    async def get_summary(session_id: str) -> str:
        row = await fetch_one(
            "SELECT summary_text FROM session_memory WHERE session_id=:session_id",
            {"session_id": session_id},
        )
        return str((row or {}).get("summary_text") or "")

    @staticmethod
    async def upsert_summary(session_id: str, summary_text: str) -> bool:
        affected = await execute(
            """
            INSERT INTO session_memory (session_id, summary_text)
            VALUES (:session_id, :summary_text)
            ON DUPLICATE KEY UPDATE summary_text=VALUES(summary_text)
            """,
            {"session_id": session_id, "summary_text": summary_text},
        )
        return affected > 0

    @staticmethod
    async def get_retrieval_summary(session_id: str) -> str:
        row = await fetch_one(
            "SELECT retrieval_summary_text FROM session_memory WHERE session_id=:session_id",
            {"session_id": session_id},
        )
        return str((row or {}).get("retrieval_summary_text") or "")

    @staticmethod
    async def upsert_retrieval_summary(session_id: str, retrieval_summary_text: str) -> bool:
        affected = await execute(
            """
            INSERT INTO session_memory (session_id, retrieval_summary_text)
            VALUES (:session_id, :retrieval_summary_text)
            ON DUPLICATE KEY UPDATE retrieval_summary_text=VALUES(retrieval_summary_text)
            """,
            {"session_id": session_id, "retrieval_summary_text": retrieval_summary_text},
        )
        return affected > 0

    @staticmethod
    async def clear_summary(session_id: str) -> bool:
        affected = await execute(
            "DELETE FROM session_memory WHERE session_id=:session_id",
            {"session_id": session_id},
        )
        return affected > 0
