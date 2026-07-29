"""Token 使用量异步管理。"""

from __future__ import annotations

from datetime import datetime

from app.backend.repositories.database import (
    fetch_all,
    fetch_one,
    insert_and_get_last_id,
)
from app.backend.repositories.row_utils import to_iso_timestamp


class TokenUsageManager:
    """封装会话维度的 token 使用记录与统计查询。"""

    @staticmethod
    async def record_usage(
        session_id: str,
        user_id: str,
        prompt_tokens: int,
        completion_tokens: int,
        model_name: str = "",
    ) -> dict:
        """记录一次会话 token 使用情况。

        Args:
            session_id: 会话 ID。
            user_id: 用户 ID。
            prompt_tokens: 输入 token 数。
            completion_tokens: 输出 token 数。
            model_name: 模型名称。

        Returns:
            dict: 新增 token 使用记录摘要。
        """
        total_tokens = int(prompt_tokens or 0) + int(completion_tokens or 0)
        usage_id = await insert_and_get_last_id(
            """
            INSERT INTO token_usage
            (session_id, user_id, prompt_tokens, completion_tokens, total_tokens, model_name)
            VALUES (:session_id, :user_id, :prompt_tokens, :completion_tokens, :total_tokens, :model_name)
            """,
            {
                "session_id": session_id,
                "user_id": user_id,
                "prompt_tokens": int(prompt_tokens or 0),
                "completion_tokens": int(completion_tokens or 0),
                "total_tokens": total_tokens,
                "model_name": model_name,
            },
        )
        return {
            "id": usage_id,
            "session_id": session_id,
            "user_id": user_id,
            "prompt_tokens": int(prompt_tokens or 0),
            "completion_tokens": int(completion_tokens or 0),
            "total_tokens": total_tokens,
            "model_name": model_name,
            "created_at": datetime.now().isoformat(),
        }

    @staticmethod
    async def get_usage_by_session(session_id: str) -> list:
        """查询会话维度的 token 使用情况明细。

        Args:
            session_id: 会话 ID。

        Returns:
            list: 按时间升序排列的 token 记录列表。
        """
        rows = await fetch_all(
            """
            SELECT id, session_id, user_id, prompt_tokens, completion_tokens,
                   total_tokens, model_name, created_at
            FROM token_usage
            WHERE session_id=:session_id
            ORDER BY created_at ASC
            """,
            {"session_id": session_id},
        )
        return [
            {
                "id": row["id"],
                "session_id": row["session_id"],
                "user_id": row["user_id"],
                "prompt_tokens": row["prompt_tokens"],
                "completion_tokens": row["completion_tokens"],
                "total_tokens": row["total_tokens"],
                "model_name": row.get("model_name") or "",
                "created_at": (
                    row["created_at"].isoformat() if row.get("created_at") else None
                ),
            }
            for row in rows
        ]

    @staticmethod
    async def get_daily_stats(days: int = 30) -> list:
        """按天聚合 token 使用情况统计。

        Args:
            days: 回溯统计天数。

        Returns:
            list: 每日聚合统计列表。
        """
        rows = await fetch_all(
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
            WHERE created_at >= DATE_SUB(CURDATE(), INTERVAL :days DAY)
            GROUP BY DATE(created_at)
            ORDER BY date DESC
            """,
            {"days": int(days)},
        )
        return [
            {
                "date": row["date"].isoformat() if row.get("date") else None,
                "request_count": row.get("request_count") or 0,
                "prompt_tokens": row.get("total_prompt_tokens") or 0,
                "completion_tokens": row.get("total_completion_tokens") or 0,
                "total_tokens": row.get("total_tokens") or 0,
                "session_count": row.get("session_count") or 0,
                "user_count": row.get("user_count") or 0,
            }
            for row in rows
        ]

    @staticmethod
    async def get_session_stats(limit: int = 100) -> list:
        """按会话维度聚合 token 消耗排行。

        Args:
            limit: 返回记录上限。

        Returns:
            list: 会话统计列表（按 total_tokens 降序）。
        """
        rows = await fetch_all(
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
            LIMIT :limit
            """,
            {"limit": int(limit)},
        )
        return [
            {
                "session_id": row.get("session_id"),
                "session_title": row.get("session_title") or "未命名会话",
                "user_id": row.get("user_id"),
                "request_count": row.get("request_count") or 0,
                "prompt_tokens": row.get("total_prompt_tokens") or 0,
                "completion_tokens": row.get("total_completion_tokens") or 0,
                "total_tokens": row.get("total_tokens") or 0,
                "first_used": to_iso_timestamp(row.get("first_used")),
                "last_used": to_iso_timestamp(row.get("last_used")),
            }
            for row in rows
        ]

    @staticmethod
    async def get_total_stats() -> dict:
        """获取 token 使用全局汇总。

        Returns:
            dict: 总请求数、总 token、会话数、用户数等指标。
        """
        row = await fetch_one(
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
        return {
            "total_requests": int((row or {}).get("total_requests") or 0),
            "total_prompt_tokens": int((row or {}).get("total_prompt_tokens") or 0),
            "total_completion_tokens": int(
                (row or {}).get("total_completion_tokens") or 0
            ),
            "total_tokens": int((row or {}).get("total_tokens") or 0),
            "total_sessions": int((row or {}).get("total_sessions") or 0),
            "total_users": int((row or {}).get("total_users") or 0),
            "total_days": int((row or {}).get("total_days") or 0),
        }
