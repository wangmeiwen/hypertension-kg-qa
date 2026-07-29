"""管理员可观测性路由。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.backend.dependencies.admin_auth import require_admin_access
from app.backend.services.admin_observability_service import AdminObservabilityService


def create_admin_observability_router(service: AdminObservabilityService) -> APIRouter:
    """创建管理员可观测性路由。"""
    router = APIRouter(dependencies=[Depends(require_admin_access)])

    @router.get("/admin/agent/observability-summary")
    async def admin_agent_observability_summary(limit: int = 200) -> dict[str, Any]:
        """获取 Agent 可观测性汇总。"""
        return await service.admin_agent_observability_summary(limit=limit)

    @router.get("/admin/agent/observability-runs")
    async def admin_agent_observability_runs(limit: int = 50) -> dict[str, Any]:
        """获取 Agent 近期运行明细。"""
        return await service.admin_agent_observability_runs(limit=limit)

    return router
