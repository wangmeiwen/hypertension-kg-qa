"""管理员文档服务。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Awaitable, Callable

from fastapi import UploadFile


class AdminDocumentsService:
    """封装管理员文档上传、解析与嵌入业务。"""

    def __init__(
        self,
        await_call: Callable[..., Awaitable[Any]],
        get_shared_vector_store: Callable[[], Any],
        log_api_error: Callable[..., None],
    ) -> None:
        self._await_call = await_call
        self._get_shared_vector_store = get_shared_vector_store
        self._log_api_error = log_api_error

    async def upload_document(self, file: UploadFile) -> dict[str, Any]:
        """上传 DOCX 文档并嵌入向量库。"""
        if not file.filename:
            return {"ok": False, "error": "文件名不能为空"}
        if not file.filename.endswith(".docx"):
            return {"ok": False, "error": "只支持DOCX格式的文件"}

        try:
            content = await file.read()
            from app.rag_core.document_embedding import embed_docx_bytes

            vector_store = await self._await_call(self._get_shared_vector_store)
            result = await self._await_call(embed_docx_bytes, content, file.filename, vector_store)
            return {"ok": True, "data": result}
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            self._log_api_error("/admin/documents/upload", "doc_embed", exc, filename=file.filename)
            return {"ok": False, "error": f"文档处理失败: {str(exc)}"}

    async def get_document_stats(self) -> dict[str, Any]:
        """获取文档向量库统计。"""
        try:
            from app.rag_core.document_embedding import DocumentEmbedder

            vector_store = await self._await_call(self._get_shared_vector_store)
            embedder = DocumentEmbedder(vector_store=vector_store)
            stats = await self._await_call(embedder.get_collection_stats)
            return stats
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            self._log_api_error("/admin/documents/stats", "get_collection_stats", exc)
            return {"ok": False, "error": f"获取统计信息失败: {str(exc)}"}

    async def docling_parse_document(self, file: UploadFile) -> dict[str, Any]:
        """使用 Docling 解析文档。"""
        if not file.filename:
            return {"ok": False, "error": "文件名不能为空"}

        allowed_extensions = {
            ".pdf",
            ".docx",
            ".doc",
            ".pptx",
            ".ppt",
            ".html",
            ".htm",
            ".txt",
            ".md",
            ".jpg",
            ".jpeg",
            ".png",
        }
        file_ext = Path(file.filename).suffix.lower()
        if file_ext not in allowed_extensions:
            return {"ok": False, "error": f"不支持的文件格式: {file_ext}。支持的格式: {', '.join(allowed_extensions)}"}

        try:
            content = await file.read()
            from app.document_parser.docling_parser import DoclingParser

            parser = await self._await_call(DoclingParser)
            result = await self._await_call(parser.parse_bytes, content, file.filename)
            return {
                "ok": True,
                "data": {
                    "filename": result.source,
                    "format": result.format,
                    "markdown": result.markdown,
                    "text": result.text,
                    "pages": result.pages,
                    "tables_count": len(result.tables),
                    "images_count": len(result.images),
                    "metadata": result.metadata,
                },
            }
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            self._log_api_error("/admin/docling/parse", "docling_parse", exc, filename=file.filename)
            return {"ok": False, "error": f"Docling 解析失败: {str(exc)}"}

    async def docling_parse_and_embed(self, file: UploadFile) -> dict[str, Any]:
        """Docling 解析并嵌入向量库。"""
        if not file.filename:
            return {"ok": False, "error": "文件名不能为空"}

        allowed_extensions = {".pdf", ".docx", ".doc", ".pptx", ".ppt", ".html", ".htm", ".txt", ".md"}
        file_ext = Path(file.filename).suffix.lower()
        if file_ext not in allowed_extensions:
            return {"ok": False, "error": f"不支持的文件格式: {file_ext}。支持的格式: {', '.join(allowed_extensions)}"}

        try:
            content = await file.read()
            from app.common.config import Config
            from app.document_parser.docling_parser import DoclingParser
            from app.document_parser.document_segmentation import DocumentSegmentation
            from app.rag_core.document_embedding import DocumentEmbedder

            parser = await self._await_call(DoclingParser)
            parsed = await self._await_call(parser.parse_bytes, content, file.filename)

            conf = Config()
            chunker = DocumentSegmentation(
                parent_chunk_size=conf.PARENT_CHUNK_SIZE,
                sub_chunk_size=conf.SUB_CHUNK_SIZE,
                parent_chunk_overlap=conf.PARENT_CHUNK_OVERLAP,
                sub_chunk_overlap=conf.SUB_CHUNK_OVERLAP,
            )
            chunk_source_text = (parsed.text or "").strip()
            if not chunk_source_text:
                chunk_source_text = (parsed.markdown or "").strip()
            if not chunk_source_text:
                raise ValueError("Docling 解析结果为空，无法分块并嵌入")

            hierarchical_chunks = await self._await_call(
                chunker.segment_text_with_page_count,
                chunk_source_text,
                file.filename,
                len(parsed.pages),
            )
            sub_chunks = await self._await_call(chunker.get_all_sub_chunks, hierarchical_chunks)
            if not sub_chunks:
                raise ValueError("分块结果为空，未执行 Milvus 写入")

            vector_store = await self._await_call(self._get_shared_vector_store)
            embedder = DocumentEmbedder(vector_store=vector_store)
            embed_result = await self._await_call(embedder.embed_docling_sub_chunks, sub_chunks, file.filename, parsed)
            embed_result["parent_chunks_count"] = len(hierarchical_chunks)
            collection_stats = await self._await_call(embedder.get_collection_stats)

            return {
                "ok": True,
                "data": {
                    "filename": file.filename,
                    "format": embed_result.get("format"),
                    "pages": embed_result.get("pages", 0),
                    "tables": embed_result.get("tables", 0),
                    "images": embed_result.get("images", 0),
                    "parent_chunks_count": embed_result.get("parent_chunks_count", 0),
                    "sub_chunks_count": embed_result.get("sub_chunks_count", 0),
                    "milvus_row_count": collection_stats.get("row_count", 0) if collection_stats.get("success") else None,
                    "message": embed_result.get("message", "Docling 解析并嵌入完成"),
                },
            }
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            self._log_api_error("/admin/docling/parse-and-embed", "docling_embed", exc, filename=file.filename)
            return {"ok": False, "error": f"Docling 解析并嵌入失败: {str(exc)}"}
