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

    @router.post("/admin/docling/parse")
    async def docling_parse_document(file: UploadFile = File(...)) -> dict[str, Any]:
        """使用 Docling 解析文档。"""
        return await service.docling_parse_document(file)

    @router.post("/admin/docling/parse-and-embed")
    async def docling_parse_and_embed(file: UploadFile = File(...)) -> dict[str, Any]:
        """Docling 解析并嵌入向量库。"""
        return await service.docling_parse_and_embed(file)

    return router
