"""OCR 文档与 token 使用异步管理。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from app.backend.repositories.database import fetch_all, fetch_one, execute, insert_and_get_last_id


class OcrDocumentManager:
    @staticmethod
    async def create_document(
        user_id: str,
        file_name: str,
        file_type: str,
        file_size: int = 0,
        page_count: int = 0,
    ) -> dict:
        """创建资源。

        Args:
            user_id: 文档所属用户 ID。
            file_name: 原始文件名。
            file_type: 文件类型（如 pdf、docx）。
            file_size: 文件大小（字节）。
            page_count: 页数（预估或已解析）。

        Returns:
            dict: 新建 OCR 文档记录摘要。
        """
        doc_id = str(uuid.uuid4())
        await execute(
            """
            INSERT INTO ocr_documents
            (id, user_id, file_name, file_type, file_size, page_count, status)
            VALUES (:id, :user_id, :file_name, :file_type, :file_size, :page_count, :status)
            """,
            {
                "id": doc_id,
                "user_id": user_id,
                "file_name": file_name,
                "file_type": file_type,
                "file_size": int(file_size or 0),
                "page_count": int(page_count or 0),
                "status": "processing",
            },
        )
        return {
            "id": doc_id,
            "user_id": user_id,
            "file_name": file_name,
            "file_type": file_type,
            "file_size": int(file_size or 0),
            "page_count": int(page_count or 0),
            "status": "processing",
            "created_at": datetime.now().isoformat(),
        }

    @staticmethod
    async def update_document_status(
        doc_id: str,
        status: str,
        chunk_count: Optional[int] = None,
        error_message: Optional[str] = None,
    ) -> bool:
        """更新数据。

        Args:
            doc_id: 文档 ID。
            status: 目标状态（processing/completed/failed 等）。
            chunk_count: 分块数量，可选。
            error_message: 失败原因，可选。

        Returns:
            bool: 更新成功返回 True。
        """
        updates = ["status = :status"]
        params = {"id": doc_id, "status": status}
        if chunk_count is not None:
            updates.append("chunk_count = :chunk_count")
            params["chunk_count"] = int(chunk_count)
        if error_message is not None:
            updates.append("error_message = :error_message")
            params["error_message"] = error_message
        affected = await execute(
            f"UPDATE ocr_documents SET {', '.join(updates)} WHERE id = :id",
            params,
        )
        return affected > 0

    @staticmethod
    async def add_page_content(doc_id: str, page_num: int, text_content: str) -> dict:
        """写入单页 OCR 文本内容。

        Args:
            doc_id: 文档 ID。
            page_num: 页码（从 1 开始）。
            text_content: OCR 识别出的页面文本。

        Returns:
            dict: 新增页面记录摘要。
        """
        page_id = await insert_and_get_last_id(
            """
            INSERT INTO ocr_document_pages (document_id, page_num, text_content)
            VALUES (:document_id, :page_num, :text_content)
            """,
            {"document_id": doc_id, "page_num": int(page_num), "text_content": text_content},
        )
        return {
            "id": page_id,
            "document_id": doc_id,
            "page_num": int(page_num),
            "created_at": datetime.now().isoformat(),
        }

    @staticmethod
    async def get_documents(user_id: str, limit: int = 50, offset: int = 0) -> list:
        """分页查询用户 OCR 文档列表。

        Args:
            user_id: 用户 ID。
            limit: 返回条数上限。
            offset: 分页偏移量。

        Returns:
            list: 文档摘要列表。
        """
        rows = await fetch_all(
            """
            SELECT id, user_id, file_name, file_type, file_size,
                   page_count, chunk_count, status, error_message,
                   created_at, updated_at
            FROM ocr_documents
            WHERE user_id=:user_id
            ORDER BY created_at DESC
            LIMIT :limit OFFSET :offset
            """,
            {"user_id": user_id, "limit": int(limit), "offset": int(offset)},
        )
        return [
            {
                "id": row["id"],
                "user_id": row["user_id"],
                "file_name": row["file_name"],
                "file_type": row["file_type"],
                "file_size": row["file_size"],
                "page_count": row["page_count"],
                "chunk_count": row.get("chunk_count"),
                "status": row.get("status"),
                "error_message": row.get("error_message"),
                "created_at": row.get("created_at").isoformat() if row.get("created_at") else None,
                "updated_at": row.get("updated_at").isoformat() if row.get("updated_at") else None,
            }
            for row in rows
        ]

    @staticmethod
    async def get_document_detail(doc_id: str) -> Optional[dict]:
        """查询文档详情及其页面文本。

        Args:
            doc_id: 文档 ID。

        Returns:
            Optional[dict]: 文档详情；不存在时返回 None。
        """
        doc = await fetch_one(
            """
            SELECT id, user_id, file_name, file_type, file_size,
                   page_count, chunk_count, status, error_message,
                   created_at, updated_at
            FROM ocr_documents WHERE id=:id
            """,
            {"id": doc_id},
        )
        if not doc:
            return None
        pages = await fetch_all(
            """
            SELECT page_num, text_content, created_at
            FROM ocr_document_pages
            WHERE document_id=:document_id
            ORDER BY page_num ASC
            """,
            {"document_id": doc_id},
        )
        return {
            "id": doc["id"],
            "user_id": doc["user_id"],
            "file_name": doc["file_name"],
            "file_type": doc["file_type"],
            "file_size": doc["file_size"],
            "page_count": doc["page_count"],
            "chunk_count": doc.get("chunk_count"),
            "status": doc.get("status"),
            "error_message": doc.get("error_message"),
            "created_at": doc.get("created_at").isoformat() if doc.get("created_at") else None,
            "updated_at": doc.get("updated_at").isoformat() if doc.get("updated_at") else None,
            "pages": [
                {
                    "page_num": row.get("page_num"),
                    "text_content": row.get("text_content"),
                    "created_at": row.get("created_at").isoformat() if row.get("created_at") else None,
                }
                for row in pages
            ],
        }

    @staticmethod
    async def delete_document(doc_id: str) -> bool:
        """删除数据。

        Args:
            doc_id: 文档 ID。

        Returns:
            bool: 删除成功返回 True。
        """
        affected = await execute("DELETE FROM ocr_documents WHERE id=:id", {"id": doc_id})
        return affected > 0

    @staticmethod
    async def get_total_count(user_id: Optional[str] = None) -> int:
        """统计文档总数。

        Args:
            user_id: 用户 ID；为空时统计全量文档。

        Returns:
            int: 文档总数。
        """
        if user_id:
            row = await fetch_one(
                "SELECT COUNT(*) as count FROM ocr_documents WHERE user_id=:user_id",
                {"user_id": user_id},
            )
        else:
            row = await fetch_one("SELECT COUNT(*) as count FROM ocr_documents")
        return int((row or {}).get("count") or 0)


class OcrTokenUsageManager:
    @staticmethod
    async def record_usage(
        document_id: str,
        user_id: str,
        prompt_tokens: int,
        completion_tokens: int,
        page_count: int = 0,
    ) -> dict:
        """记录 OCR 文档的 token 使用量。

        Args:
            document_id: 文档 ID。
            user_id: 用户 ID。
            prompt_tokens: 输入 token 数。
            completion_tokens: 输出 token 数。
            page_count: 处理页数。

        Returns:
            dict: 新增 token 记录摘要。
        """
        total_tokens = int(prompt_tokens or 0) + int(completion_tokens or 0)
        usage_id = await insert_and_get_last_id(
            """
            INSERT INTO ocr_token_usage
            (document_id, user_id, prompt_tokens, completion_tokens, total_tokens, page_count)
            VALUES (:document_id, :user_id, :prompt_tokens, :completion_tokens, :total_tokens, :page_count)
            """,
            {
                "document_id": document_id,
                "user_id": user_id,
                "prompt_tokens": int(prompt_tokens or 0),
                "completion_tokens": int(completion_tokens or 0),
                "total_tokens": total_tokens,
                "page_count": int(page_count or 0),
            },
        )
        return {
            "id": usage_id,
            "document_id": document_id,
            "user_id": user_id,
            "prompt_tokens": int(prompt_tokens or 0),
            "completion_tokens": int(completion_tokens or 0),
            "total_tokens": total_tokens,
            "page_count": int(page_count or 0),
            "created_at": datetime.now().isoformat(),
        }

    @staticmethod
    async def get_usage_by_document(document_id: str) -> Optional[dict]:
        """查询单个文档的 token 使用记录。

        Args:
            document_id: 文档 ID。

        Returns:
            Optional[dict]: 命中时返回记录，未命中返回 None。
        """
        row = await fetch_one(
            """
            SELECT id, document_id, user_id, prompt_tokens, completion_tokens,
                   total_tokens, page_count, created_at
            FROM ocr_token_usage
            WHERE document_id=:document_id
            """,
            {"document_id": document_id},
        )
        if not row:
            return None
        return {
            "id": row["id"],
            "document_id": row["document_id"],
            "user_id": row["user_id"],
            "prompt_tokens": row["prompt_tokens"],
            "completion_tokens": row["completion_tokens"],
            "total_tokens": row["total_tokens"],
            "page_count": row["page_count"],
            "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        }

    @staticmethod
    async def get_daily_stats(days: int = 30) -> list:
        """按天聚合 OCR token 使用统计。

        Args:
            days: 回溯天数。

        Returns:
            list: 每日聚合统计列表。
        """
        rows = await fetch_all(
            """
            SELECT
                DATE(created_at) as date,
                COUNT(*) as document_count,
                SUM(prompt_tokens) as total_prompt_tokens,
                SUM(completion_tokens) as total_completion_tokens,
                SUM(total_tokens) as total_tokens,
                SUM(page_count) as total_pages,
                COUNT(DISTINCT user_id) as user_count
            FROM ocr_token_usage
            WHERE created_at >= DATE_SUB(CURDATE(), INTERVAL :days DAY)
            GROUP BY DATE(created_at)
            ORDER BY date DESC
            """,
            {"days": int(days)},
        )
        return [
            {
                "date": row["date"].isoformat() if row.get("date") else None,
                "document_count": row.get("document_count") or 0,
                "prompt_tokens": row.get("total_prompt_tokens") or 0,
                "completion_tokens": row.get("total_completion_tokens") or 0,
                "total_tokens": row.get("total_tokens") or 0,
                "page_count": row.get("total_pages") or 0,
                "user_count": row.get("user_count") or 0,
            }
            for row in rows
        ]

    @staticmethod
    async def get_total_stats() -> dict:
        """获取 OCR token 全量统计汇总。

        Returns:
            dict: 全局汇总统计结果。
        """
        row = await fetch_one(
            """
            SELECT
                COUNT(*) as total_documents,
                SUM(prompt_tokens) as total_prompt_tokens,
                SUM(completion_tokens) as total_completion_tokens,
                SUM(total_tokens) as total_tokens,
                SUM(page_count) as total_pages,
                COUNT(DISTINCT user_id) as total_users,
                COUNT(DISTINCT DATE(created_at)) as total_days
            FROM ocr_token_usage
            """
        )
        return {
            "total_documents": int((row or {}).get("total_documents") or 0),
            "total_prompt_tokens": int((row or {}).get("total_prompt_tokens") or 0),
            "total_completion_tokens": int((row or {}).get("total_completion_tokens") or 0),
            "total_tokens": int((row or {}).get("total_tokens") or 0),
            "total_pages": int((row or {}).get("total_pages") or 0),
            "total_users": int((row or {}).get("total_users") or 0),
            "total_days": int((row or {}).get("total_days") or 0),
        }
