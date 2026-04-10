"""管理员文档路由。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, UploadFile

from app.backend.dependencies.admin_auth import require_admin_login
from app.backend.services.admin_documents_service import AdminDocumentsService


def create_admin_documents_router(service: AdminDocumentsService) -> APIRouter:
    """创建管理员文档路由。"""
    router = APIRouter(dependencies=[Depends(require_admin_login)])

    @router.post("/admin/documents/upload")
    async def upload_document(file: UploadFile = File(...)) -> dict[str, Any]:
        """上传 DOCX 文档并嵌入向量库。"""
        return await service.upload_document(file)

    @router.get("/admin/documents/stats")
    async def get_document_stats() -> dict[str, Any]:
        """获取文档向量库统计。"""
        return await service.get_document_stats()

    @router.post("/admin/text-embedding/jobs/embed")
    async def submit_text_embedding_job(file: UploadFile = File(...)) -> dict[str, Any]:
        """提交文本嵌入后台任务。"""
        return await service.submit_text_embedding_job(file)

    @router.get("/admin/text-embedding/jobs")
    async def list_text_embedding_jobs(limit: int = 10) -> dict[str, Any]:
        """查询最近文本嵌入后台任务。"""
        return await service.list_text_embedding_jobs(limit=limit)

    @router.get("/admin/text-embedding/jobs/{job_id}")
    async def get_text_embedding_job(job_id: str) -> dict[str, Any]:
        """查询文本嵌入后台任务状态。"""
        return await service.get_text_embedding_job(job_id=job_id)

    return router
