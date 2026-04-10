"""管理员图谱路由。"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, File, UploadFile

from app.backend.dependencies.admin_auth import require_admin_login
from app.backend.services.graph_admin_service import GraphAdminService


def create_admin_graph_router(service: GraphAdminService) -> APIRouter:
    """创建管理员图谱路由。"""
    router = APIRouter(dependencies=[Depends(require_admin_login)])

    @router.get("/admin/graph/labels")
    async def get_graph_labels() -> dict[str, Any]:
        """获取所有节点标签。"""
        return await service.get_graph_labels()

    @router.get("/admin/graph/relationship-types")
    async def get_graph_relationship_types() -> dict[str, Any]:
        """获取所有关系类型。"""
        return await service.get_graph_relationship_types()

    @router.get("/admin/graph/nodes")
    async def get_graph_nodes(label: str = "", keyword: str = "", limit: int = 100, offset: int = 0) -> dict[str, Any]:
        """获取节点列表。"""
        return await service.get_graph_nodes(label=label, keyword=keyword, limit=limit, offset=offset)

    @router.get("/admin/graph/nodes/{node_id}")
    async def get_graph_node(node_id: int) -> dict[str, Any]:
        """获取单个节点详情。"""
        return await service.get_graph_node(node_id=node_id)

    @router.post("/admin/graph/nodes")
    async def create_graph_node(data: dict[str, Any]) -> dict[str, Any]:
        """创建新节点。"""
        return await service.create_graph_node(data)

    @router.put("/admin/graph/nodes/{node_id}")
    async def update_graph_node(node_id: int, data: dict[str, Any]) -> dict[str, Any]:
        """更新节点。"""
        return await service.update_graph_node(node_id=node_id, data=data)

    @router.delete("/admin/graph/nodes/{node_id}")
    async def delete_graph_node(node_id: int) -> dict[str, Any]:
        """删除节点及关系。"""
        return await service.delete_graph_node(node_id=node_id)

    @router.post("/admin/graph/relationships")
    async def create_graph_relationship(data: dict[str, Any]) -> dict[str, Any]:
        """创建关系。"""
        return await service.create_graph_relationship(data)

    @router.delete("/admin/graph/relationships/{rel_id}")
    async def delete_graph_relationship(rel_id: int) -> dict[str, Any]:
        """删除关系。"""
        return await service.delete_graph_relationship(rel_id=rel_id)

    @router.post("/admin/graph/execute-cypher")
    async def execute_cypher(data: dict[str, Any]) -> dict[str, Any]:
        """执行只读 Cypher。"""
        return await service.execute_cypher(data)

    @router.post("/admin/graph/extract-and-import")
    async def extract_and_import_graph(file: UploadFile = File(...)) -> dict[str, Any]:
        """从上传文件抽取并导入图谱。"""
        return await service.extract_and_import_graph(file=file)

    @router.post("/admin/graph/jobs/extract-import")
    async def submit_extract_import_job(file: UploadFile = File(...)) -> dict[str, Any]:
        """提交文件抽取入图谱后台任务。"""
        return await service.submit_graph_extract_import_job(file=file)

    @router.get("/admin/graph/jobs")
    async def list_graph_jobs(limit: int = 10) -> dict[str, Any]:
        """查询最近图谱后台任务。"""
        return await service.list_graph_jobs(limit=limit)

    @router.get("/admin/graph/jobs/{job_id}")
    async def get_graph_job(job_id: str) -> dict[str, Any]:
        """查询图谱后台任务状态。"""
        return await service.get_graph_job(job_id=job_id)

    @router.post("/admin/graph/embed-to-milvus")
    async def embed_graph_to_milvus(data: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """将知识图谱嵌入到 Milvus。"""
        return await service.embed_graph_to_milvus(data)

    @router.post("/admin/graph/jobs/embed-milvus")
    async def submit_embed_milvus_job(data: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """提交图谱嵌入后台任务。"""
        return await service.submit_graph_embed_milvus_job(data)

    @router.get("/admin/graph/embed-status")
    async def get_graph_embed_status() -> dict[str, Any]:
        """获取知识图谱嵌入状态。"""
        return await service.get_graph_embed_status()

    return router
