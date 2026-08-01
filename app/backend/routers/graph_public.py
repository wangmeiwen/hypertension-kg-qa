"""公开图谱查询路由。"""

from __future__ import annotations

from fastapi import APIRouter

from app.backend.services.graph_public_service import GraphPublicService


def create_graph_public_router(service: GraphPublicService) -> APIRouter:
    """创建公开图谱路由。"""
    router = APIRouter()

    @router.get("/graph/visualization")
    async def graph_visualization(
        keyword: str = "", limit: int = 200
    ) -> dict[str, object]:
        """查询图谱可视化节点与边数据。"""
        return await service.graph_visualization(keyword=keyword, limit=limit)

    @router.get("/graph/entity/{entity_id}")
    async def get_entity_detail(entity_id: str) -> dict[str, object]:
        """获取实体详细信息。"""
        return await service.get_entity_detail(entity_id=entity_id)

    return router
