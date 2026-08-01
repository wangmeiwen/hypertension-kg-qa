"""管理员运行时服务。"""

from __future__ import annotations

from datetime import datetime
import threading
from typing import Any, Awaitable, Callable

from app.backend.repositories.database import fetch_one
from app.backend.services.perf_baseline_service import generate_api_baseline_report
from app.core.feature_flags import get_runtime_flag_snapshot


class AdminRuntimeService:
    """封装管理员运行时与队列状态相关业务。"""

    def __init__(
        self,
        measure_event_loop_lag: Callable[..., Awaitable[dict[str, Any]]],
        build_a2a_runtime_snapshot: Callable[..., Awaitable[dict[str, Any]]],
        get_a2a: Callable[[], Any],
    ) -> None:
        """初始化管理员运行时服务并注入监控依赖。

        Args:
            measure_event_loop_lag: 采样事件循环延迟的异步函数。
            build_a2a_runtime_snapshot: 构建 A2A 队列和任务快照的异步函数。
            get_a2a: 获取主 A2A 服务实例的函数。

        Returns:
            None: 无返回值。
        """
        self._measure_event_loop_lag = measure_event_loop_lag
        self._build_a2a_runtime_snapshot = build_a2a_runtime_snapshot
        self._get_a2a = get_a2a

    async def admin_runtime_flags(self) -> dict[str, Any]:
        """查看当前进程内运行时特性开关快照。"""
        return {
            "ok": True,
            "data": {
                "flags": get_runtime_flag_snapshot(),
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            },
        }

    async def admin_perf_baseline(self, days: int = 1) -> dict[str, Any]:
        """生成最近 N 天性能基线报告。

        Args:
            days: 统计窗口天数，服务内部会限制在 1 到 7 天。

        Returns:
            包含 API 延迟、事件循环延迟、A2A 队列和运行时开关的数据。
        """
        safe_days = max(1, min(int(days), 7))
        target_paths = [
            "/agent/run",
            "/process_with_session",
            "/mcp/invoke",
            "/admin/graph/",
        ]
        api_report = generate_api_baseline_report(safe_days, target_paths)
        event_loop_lag = await self._measure_event_loop_lag(samples=20, interval_ms=20)
        queue_snapshot = await self._build_a2a_runtime_snapshot(limit_per_service=200)
        runtime_snapshot = get_runtime_flag_snapshot()

        return {
            "ok": True,
            "data": {
                "window_days": safe_days,
                "api_latency": api_report,
                "event_loop_lag_ms": event_loop_lag,
                "queue": queue_snapshot,
                "runtime_flags": runtime_snapshot,
                "active_threads": threading.active_count(),
                "generated_at": datetime.now().isoformat(timespec="seconds"),
            },
        }

    async def admin_qa_queue_status(self) -> dict[str, Any]:
        """获取 A2A 任务状态快照。"""
        return {
            "ok": True,
            "data": await self._build_a2a_runtime_snapshot(limit_per_service=200),
        }

    async def admin_cancel_qa_job(self, job_id: str) -> dict[str, Any]:
        """取消指定 A2A 问答任务。

        Args:
            job_id: A2A 任务 ID。

        Returns:
            取消请求结果和任务最新状态。
        """
        service = self._get_a2a()
        result = await service.acancel_task(job_id, "manual_cancel")
        if (
            not bool(result.get("ok"))
            and str(result.get("error") or "") == "task_not_found"
        ):
            return {"ok": False, "error": "job_not_found_or_finished"}
        state = str(
            (((result.get("task") or {}).get("status") or {}).get("state")) or ""
        )
        return {
            "ok": True,
            "message": "cancel_requested",
            "job_id": job_id,
            "status": state,
        }

    async def admin_get_stats(self) -> dict[str, Any]:
        """获取会话和消息数量统计数据。"""
        sessions_row = await fetch_one("SELECT COUNT(*) as count FROM sessions")
        messages_row = await fetch_one("SELECT COUNT(*) as count FROM messages")
        today_sessions_row = await fetch_one(
            "SELECT COUNT(*) as count FROM sessions WHERE DATE(created_at) = CURDATE()"
        )
        today_messages_row = await fetch_one(
            "SELECT COUNT(*) as count FROM messages WHERE DATE(created_at) = CURDATE()"
        )
        stats = {
            "session_count": int((sessions_row or {}).get("count") or 0),
            "message_count": int((messages_row or {}).get("count") or 0),
            "today_sessions": int((today_sessions_row or {}).get("count") or 0),
            "today_messages": int((today_messages_row or {}).get("count") or 0),
        }

        return {
            "ok": True,
            "data": {
                "total_sessions": stats["session_count"],
                "total_messages": stats["message_count"],
                "today_sessions": stats["today_sessions"],
                "today_messages": stats["today_messages"],
            },
        }
