"""管理员运行时路由。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.backend.dependencies.admin_auth import require_admin_access
from app.backend.services.admin_runtime_service import AdminRuntimeService


def create_admin_runtime_router(service: AdminRuntimeService) -> APIRouter:
    """创建管理员运行时路由。"""
    router = APIRouter(dependencies=[Depends(require_admin_access)])

    @router.get("/admin/runtime/flags")
    async def admin_runtime_flags() -> dict[str, Any]:
        """查看运行时特性开关。"""
        return await service.admin_runtime_flags()

    @router.get("/admin/perf/baseline")
    async def admin_perf_baseline(days: int = 1) -> dict[str, Any]:
        """生成最近 N 天性能基线报告。"""
        return await service.admin_perf_baseline(days=days)

    @router.get("/admin/qa/queue/status")
    async def admin_qa_queue_status() -> dict[str, Any]:
        """获取 A2A 任务状态快照。"""
        return await service.admin_qa_queue_status()

    @router.post("/admin/qa/jobs/{job_id}/cancel")
    async def admin_cancel_qa_job(job_id: str) -> dict[str, Any]:
        """取消 A2A 任务。"""
        return await service.admin_cancel_qa_job(job_id=job_id)

    @router.get("/admin/stats")
    async def admin_get_stats() -> dict[str, Any]:
        """获取统计数据。"""
        return await service.admin_get_stats()

    return router
