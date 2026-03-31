"""
会话记忆摘要管理模块。

本模块提供会话记忆摘要的存储和检索功能，支持：
- 会话摘要的获取和更新
- 检索摘要的获取和更新
- 会话记忆的清除

主要类：
- SessionMemoryManager: 会话记忆摘要管理器

"""

from app.backend.repositories.database import get_db


class SessionMemoryManager:
    """
    会话记忆摘要管理器。

    提供会话摘要和检索摘要的存储、检索、清除等操作。
    """

    @staticmethod
    def get_summary(session_id: str) -> str:
        """
        获取会话摘要。

        Args:
            session_id: 会话 ID

        Returns:
            会话摘要文本，不存在则返回空字符串
        """
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT summary_text FROM session_memory WHERE session_id = %s",
                    (session_id,)
                )
                row = cursor.fetchone()
                return str((row or {}).get("summary_text") or "")

    @staticmethod
    def upsert_summary(session_id: str, summary_text: str) -> bool:
        """
        插入或更新会话摘要。

        Args:
            session_id: 会话 ID
            summary_text: 摘要文本

        Returns:
            是否操作成功
        """
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO session_memory (session_id, summary_text)
                    VALUES (%s, %s)
                    ON DUPLICATE KEY UPDATE summary_text = VALUES(summary_text)
                    """,
                    (session_id, summary_text)
                )
                conn.commit()
                return cursor.rowcount > 0

    @staticmethod
    def get_retrieval_summary(session_id: str) -> str:
        """
        获取检索摘要。

        Args:
            session_id: 会话 ID

        Returns:
            检索摘要文本，不存在则返回空字符串
        """
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT retrieval_summary_text FROM session_memory WHERE session_id = %s",
                    (session_id,)
                )
                row = cursor.fetchone()
                return str((row or {}).get("retrieval_summary_text") or "")

    @staticmethod
    def upsert_retrieval_summary(session_id: str, retrieval_summary_text: str) -> bool:
        """
        插入或更新检索摘要。

        Args:
            session_id: 会话 ID
            retrieval_summary_text: 检索摘要文本

        Returns:
            是否操作成功
        """
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO session_memory (session_id, retrieval_summary_text)
                    VALUES (%s, %s)
                    ON DUPLICATE KEY UPDATE retrieval_summary_text = VALUES(retrieval_summary_text)
                    """,
                    (session_id, retrieval_summary_text)
                )
                conn.commit()
                return cursor.rowcount > 0

    @staticmethod
    def clear_summary(session_id: str) -> bool:
        """
        清除会话记忆。

        Args:
            session_id: 会话 ID

        Returns:
            是否清除成功
        """
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM session_memory WHERE session_id = %s",
                    (session_id,)
                )
                conn.commit()
                return cursor.rowcount > 0

