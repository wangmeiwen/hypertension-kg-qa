"""管理员存储 schema 诊断路由。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.backend.dependencies.admin_auth import require_admin_login
from app.backend.services.storage_schema_service import StorageSchemaService


def create_admin_storage_router(service: StorageSchemaService) -> APIRouter:
    """创建只读存储诊断路由。"""
    router = APIRouter(dependencies=[Depends(require_admin_login)])

    @router.get("/admin/storage/schema-report")
    async def get_storage_schema_report() -> dict[str, Any]:
        """返回 MySQL、Neo4j 和 Milvus schema 兼容状态。"""
        return await service.get_schema_report()

    return router
