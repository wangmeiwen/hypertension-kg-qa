"""
Token 使用量管理模块。

本模块提供 LLM Token 使用记录的管理功能，支持：
- Token 使用记录的创建
- 按会话查询使用记录
- 按天统计使用量
- 按会话统计使用量
- 总体统计信息

主要类：
- TokenUsageManager: Token 使用量管理器

"""

from datetime import datetime

from app.backend.repositories.database import get_db


class TokenUsageManager:
    """
    Token 使用量管理器。

    提供 LLM Token 使用记录的创建、查询、统计等操作。
    """

    @staticmethod
    def record_usage(session_id: str, user_id: str, prompt_tokens: int,
                     completion_tokens: int, model_name: str = "") -> dict:
        """
        记录 Token 使用量。

        Args:
            session_id: 会话 ID
            user_id: 用户 ID
            prompt_tokens: 提示词 Token 数
            completion_tokens: 完成内容 Token 数
            model_name: 模型名称，可选

        Returns:
            使用记录信息字典
        """
        total_tokens = prompt_tokens + completion_tokens
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO token_usage
                    (session_id, user_id, prompt_tokens, completion_tokens, total_tokens, model_name)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (session_id, user_id, prompt_tokens, completion_tokens, total_tokens, model_name)
                )
                conn.commit()
                usage_id = cursor.lastrowid

                return {
                    "id": usage_id,
                    "session_id": session_id,
                    "user_id": user_id,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": total_tokens,
                    "model_name": model_name,
                    "created_at": datetime.now().isoformat()
                }

    @staticmethod
    def get_usage_by_session(session_id: str) -> list:
        """
        获取指定会话的 Token 使用记录。

        Args:
            session_id: 会话 ID

        Returns:
            Token 使用记录列表
        """
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, session_id, user_id, prompt_tokens, completion_tokens,
                           total_tokens, model_name, created_at
                    FROM token_usage
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
                        "user_id": row["user_id"],
                        "prompt_tokens": row["prompt_tokens"],
                        "completion_tokens": row["completion_tokens"],
                        "total_tokens": row["total_tokens"],
                        "model_name": row["model_name"],
                        "created_at": row["created_at"].isoformat() if row["created_at"] else None
                    }
                    for row in rows
                ]

    @staticmethod
    def get_daily_stats(days: int = 30) -> list:
        """
        获取按天统计的 Token 使用量。

        Args:
            days: 统计天数，默认 30

        Returns:
            每日统计列表
        """
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        DATE(created_at) as date,
                        COUNT(*) as request_count,
                        SUM(prompt_tokens) as total_prompt_tokens,
                        SUM(completion_tokens) as total_completion_tokens,
                        SUM(total_tokens) as total_tokens,
                        COUNT(DISTINCT session_id) as session_count,
                        COUNT(DISTINCT user_id) as user_count
                    FROM token_usage
                    WHERE created_at >= DATE_SUB(CURDATE(), INTERVAL %s DAY)
                    GROUP BY DATE(created_at)
                    ORDER BY date DESC
                    """,
                    (days,)
                )
                rows = cursor.fetchall()
                return [
                    {
                        "date": row["date"].isoformat() if row["date"] else None,
                        "request_count": row["request_count"],
                        "prompt_tokens": row["total_prompt_tokens"] or 0,
                        "completion_tokens": row["total_completion_tokens"] or 0,
                        "total_tokens": row["total_tokens"] or 0,
                        "session_count": row["session_count"],
                        "user_count": row["user_count"]
                    }
                    for row in rows
                ]

    @staticmethod
    def get_session_stats(limit: int = 100) -> list:
        """
        获取按会话统计的 Token 使用量。

        Args:
            limit: 返回数量限制，默认 100

        Returns:
            会话统计列表
        """
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        t.session_id,
                        s.title as session_title,
                        t.user_id,
                        COUNT(*) as request_count,
                        SUM(t.prompt_tokens) as total_prompt_tokens,
                        SUM(t.completion_tokens) as total_completion_tokens,
                        SUM(t.total_tokens) as total_tokens,
                        MIN(t.created_at) as first_used,
                        MAX(t.created_at) as last_used
                    FROM token_usage t
                    LEFT JOIN sessions s ON t.session_id = s.id
                    GROUP BY t.session_id, s.title, t.user_id
                    ORDER BY total_tokens DESC
                    LIMIT %s
                    """,
                    (limit,)
                )
                rows = cursor.fetchall()
                return [
                    {
                        "session_id": row["session_id"],
                        "session_title": row["session_title"] or "未命名会话",
                        "user_id": row["user_id"],
                        "request_count": row["request_count"],
                        "prompt_tokens": row["total_prompt_tokens"] or 0,
                        "completion_tokens": row["total_completion_tokens"] or 0,
                        "total_tokens": row["total_tokens"] or 0,
                        "first_used": row["first_used"].isoformat() if row["first_used"] else None,
                        "last_used": row["last_used"].isoformat() if row["last_used"] else None
                    }
                    for row in rows
                ]

    @staticmethod
    def get_total_stats() -> dict:
        """
        获取总体统计信息。

        Returns:
            总体统计字典
        """
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        COUNT(*) as total_requests,
                        SUM(prompt_tokens) as total_prompt_tokens,
                        SUM(completion_tokens) as total_completion_tokens,
                        SUM(total_tokens) as total_tokens,
                        COUNT(DISTINCT session_id) as total_sessions,
                        COUNT(DISTINCT user_id) as total_users,
                        COUNT(DISTINCT DATE(created_at)) as total_days
                    FROM token_usage
                    """
                )
                row = cursor.fetchone()
                return {
                    "total_requests": row["total_requests"] or 0,
                    "total_prompt_tokens": row["total_prompt_tokens"] or 0,
                    "total_completion_tokens": row["total_completion_tokens"] or 0,
                    "total_tokens": row["total_tokens"] or 0,
                    "total_sessions": row["total_sessions"] or 0,
                    "total_users": row["total_users"] or 0,
                    "total_days": row["total_days"] or 0
                }

