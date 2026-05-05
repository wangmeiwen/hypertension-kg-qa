"""session_memory_manager 模块。

提供会话记忆摘要和检索摘要的异步数据访问能力。
"""


from __future__ import annotations

from app.backend.repositories.database import fetch_one, execute


class SessionMemoryManager:
    """封装会话摘要与检索摘要的持久化读写操作。"""

    @staticmethod
    async def get_summary(session_id: str) -> str:
        """读取会话主摘要文本。

        Args:
            session_id: 会话 ID。

        Returns:
            str: 摘要文本；不存在时返回空字符串。
        """
        row = await fetch_one(
            "SELECT summary_text FROM session_memory WHERE session_id=:session_id",
            {"session_id": session_id},
        )
        return str((row or {}).get("summary_text") or "")

    @staticmethod
    async def upsert_summary(session_id: str, summary_text: str) -> bool:
        """写入或更新会话主摘要。

        Args:
            session_id: 会话 ID。
            summary_text: 摘要文本。

        Returns:
            bool: 写入成功返回 True。
        """
        affected = await execute(
            """
            INSERT INTO session_memory (session_id, summary_text)
            VALUES (:session_id, :summary_text) AS new
            ON DUPLICATE KEY UPDATE summary_text=new.summary_text
            """,
            {"session_id": session_id, "summary_text": summary_text},
        )
        return affected > 0

    @staticmethod
    async def get_retrieval_summary(session_id: str) -> str:
        """读取会话检索摘要文本。

        Args:
            session_id: 会话 ID。

        Returns:
            str: 检索摘要文本；不存在时返回空字符串。
        """
        row = await fetch_one(
            "SELECT retrieval_summary_text FROM session_memory WHERE session_id=:session_id",
            {"session_id": session_id},
        )
        return str((row or {}).get("retrieval_summary_text") or "")

    @staticmethod
    async def upsert_retrieval_summary(session_id: str, retrieval_summary_text: str) -> bool:
        """写入或更新会话检索摘要。

        Args:
            session_id: 会话 ID。
            retrieval_summary_text: 检索摘要文本。

        Returns:
            bool: 写入成功返回 True。
        """
        affected = await execute(
            """
            INSERT INTO session_memory (session_id, retrieval_summary_text)
            VALUES (:session_id, :retrieval_summary_text) AS new
            ON DUPLICATE KEY UPDATE retrieval_summary_text=new.retrieval_summary_text
            """,
            {"session_id": session_id, "retrieval_summary_text": retrieval_summary_text},
        )
        return affected > 0

    @staticmethod
    async def clear_summary(session_id: str) -> bool:
        """清理会话摘要记录。

        Args:
            session_id: 会话 ID。

        Returns:
            bool: 删除成功返回 True。
        """
        affected = await execute(
            "DELETE FROM session_memory WHERE session_id=:session_id",
            {"session_id": session_id},
        )
        return affected > 0
