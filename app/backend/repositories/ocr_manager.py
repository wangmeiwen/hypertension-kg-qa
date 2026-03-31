"""
OCR 文档管理模块。

本模块提供 OCR 文档和 Token 使用记录的管理功能，支持：
- OCR 文档记录的创建、查询、更新、删除
- 文档页面内容的管理
- OCR Token 使用记录的统计

主要类：
- OcrDocumentManager: OCR 文档管理器
- OcrTokenUsageManager: OCR Token 使用记录管理器

"""

import uuid
from datetime import datetime
from typing import Optional

from app.backend.repositories.database import get_db


class OcrDocumentManager:
    """
    OCR 文档管理器。

    提供 OCR 文档记录的创建、查询、更新、删除等操作。
    """

    @staticmethod
    def create_document(user_id: str, file_name: str, file_type: str,
                        file_size: int = 0, page_count: int = 0) -> dict:
        """
        创建 OCR 文档记录。

        Args:
            user_id: 用户 ID
            file_name: 文件名
            file_type: 文件类型
            file_size: 文件大小（字节），默认 0
            page_count: 页数，默认 0

        Returns:
            文档信息字典
        """
        doc_id = str(uuid.uuid4())
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO ocr_documents
                    (id, user_id, file_name, file_type, file_size, page_count, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (doc_id, user_id, file_name, file_type, file_size, page_count, 'processing')
                )
                conn.commit()

                return {
                    "id": doc_id,
                    "user_id": user_id,
                    "file_name": file_name,
                    "file_type": file_type,
                    "file_size": file_size,
                    "page_count": page_count,
                    "status": 'processing',
                    "created_at": datetime.now().isoformat()
                }

    @staticmethod
    def update_document_status(doc_id: str, status: str, chunk_count: Optional[int] = None,
                               error_message: Optional[str] = None) -> bool:
        """
        更新文档状态。

        Args:
            doc_id: 文档 ID
            status: 新状态
            chunk_count: 切块数量，可选
            error_message: 错误信息，可选

        Returns:
            是否更新成功
        """
        with get_db() as conn:
            with conn.cursor() as cursor:
                if chunk_count is not None and error_message is not None:
                    cursor.execute(
                        """
                        UPDATE ocr_documents
                        SET status = %s, chunk_count = %s, error_message = %s
                        WHERE id = %s
                        """,
                        (status, chunk_count, error_message, doc_id)
                    )
                elif chunk_count is not None:
                    cursor.execute(
                        """
                        UPDATE ocr_documents
                        SET status = %s, chunk_count = %s
                        WHERE id = %s
                        """,
                        (status, chunk_count, doc_id)
                    )
                elif error_message is not None:
                    cursor.execute(
                        """
                        UPDATE ocr_documents
                        SET status = %s, error_message = %s
                        WHERE id = %s
                        """,
                        (status, error_message, doc_id)
                    )
                else:
                    cursor.execute(
                        "UPDATE ocr_documents SET status = %s WHERE id = %s",
                        (status, doc_id)
                    )
                conn.commit()
                return cursor.rowcount > 0

    @staticmethod
    def add_page_content(doc_id: str, page_num: int, text_content: str) -> dict:
        """
        添加页面解析内容。

        Args:
            doc_id: 文档 ID
            page_num: 页码
            text_content: 文本内容

        Returns:
            页面信息字典
        """
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO ocr_document_pages (document_id, page_num, text_content)
                    VALUES (%s, %s, %s)
                    """,
                    (doc_id, page_num, text_content)
                )
                conn.commit()
                page_id = cursor.lastrowid

                return {
                    "id": page_id,
                    "document_id": doc_id,
                    "page_num": page_num,
                    "created_at": datetime.now().isoformat()
                }

    @staticmethod
    def get_documents(user_id: str, limit: int = 50, offset: int = 0) -> list:
        """
        获取用户的 OCR 文档列表。

        Args:
            user_id: 用户 ID
            limit: 返回数量限制，默认 50
            offset: 偏移量，默认 0

        Returns:
            文档信息列表
        """
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, user_id, file_name, file_type, file_size,
                           page_count, chunk_count, status, error_message,
                           created_at, updated_at
                    FROM ocr_documents
                    WHERE user_id = %s
                    ORDER BY created_at DESC
                    LIMIT %s OFFSET %s
                    """,
                    (user_id, limit, offset)
                )
                rows = cursor.fetchall()
                return [
                    {
                        "id": row["id"],
                        "user_id": row["user_id"],
                        "file_name": row["file_name"],
                        "file_type": row["file_type"],
                        "file_size": row["file_size"],
                        "page_count": row["page_count"],
                        "chunk_count": row["chunk_count"],
                        "status": row["status"],
                        "error_message": row["error_message"],
                        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None
                    }
                    for row in rows
                ]

    @staticmethod
    def get_document_detail(doc_id: str) -> Optional[dict]:
        """
        获取文档详情（包含页面内容）。

        Args:
            doc_id: 文档 ID

        Returns:
            文档详情字典，包含页面内容列表
        """
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, user_id, file_name, file_type, file_size,
                           page_count, chunk_count, status, error_message,
                           created_at, updated_at
                    FROM ocr_documents
                    WHERE id = %s
                    """,
                    (doc_id,)
                )
                doc_row = cursor.fetchone()

                if not doc_row:
                    return None

                cursor.execute(
                    """
                    SELECT page_num, text_content, created_at
                    FROM ocr_document_pages
                    WHERE document_id = %s
                    ORDER BY page_num ASC
                    """,
                    (doc_id,)
                )
                page_rows = cursor.fetchall()

                return {
                    "id": doc_row["id"],
                    "user_id": doc_row["user_id"],
                    "file_name": doc_row["file_name"],
                    "file_type": doc_row["file_type"],
                    "file_size": doc_row["file_size"],
                    "page_count": doc_row["page_count"],
                    "chunk_count": doc_row["chunk_count"],
                    "status": doc_row["status"],
                    "error_message": doc_row["error_message"],
                    "created_at": doc_row["created_at"].isoformat() if doc_row["created_at"] else None,
                    "updated_at": doc_row["updated_at"].isoformat() if doc_row["updated_at"] else None,
                    "pages": [
                        {
                            "page_num": row["page_num"],
                            "text_content": row["text_content"],
                            "created_at": row["created_at"].isoformat() if row["created_at"] else None
                        }
                        for row in page_rows
                    ]
                }

    @staticmethod
    def delete_document(doc_id: str) -> bool:
        """
        删除文档及其页面内容（级联删除）。

        Args:
            doc_id: 文档 ID

        Returns:
            是否删除成功
        """
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM ocr_documents WHERE id = %s", (doc_id,))
                conn.commit()
                return cursor.rowcount > 0

    @staticmethod
    def get_total_count(user_id: Optional[str] = None) -> int:
        """
        获取文档总数。

        Args:
            user_id: 用户 ID，可选

        Returns:
            文档总数
        """
        with get_db() as conn:
            with conn.cursor() as cursor:
                if user_id:
                    cursor.execute(
                        "SELECT COUNT(*) as count FROM ocr_documents WHERE user_id = %s",
                        (user_id,)
                    )
                else:
                    cursor.execute("SELECT COUNT(*) as count FROM ocr_documents")
                result = cursor.fetchone()
                return result["count"] if result else 0


class OcrTokenUsageManager:
    """
    OCR Token 使用量管理器。

    提供 OCR Token 使用记录的创建、查询、统计等操作。
    """

    @staticmethod
    def record_usage(document_id: str, user_id: str, prompt_tokens: int,
                     completion_tokens: int, page_count: int = 0) -> dict:
        """
        记录 OCR Token 使用量。

        Args:
            document_id: 文档 ID
            user_id: 用户 ID
            prompt_tokens: 提示词 Token 数
            completion_tokens: 完成内容 Token 数
            page_count: 页数，默认 0

        Returns:
            使用记录信息字典
        """
        total_tokens = prompt_tokens + completion_tokens
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO ocr_token_usage
                    (document_id, user_id, prompt_tokens, completion_tokens, total_tokens, page_count)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (document_id, user_id, prompt_tokens, completion_tokens, total_tokens, page_count)
                )
                conn.commit()
                usage_id = cursor.lastrowid

                return {
                    "id": usage_id,
                    "document_id": document_id,
                    "user_id": user_id,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": total_tokens,
                    "page_count": page_count,
                    "created_at": datetime.now().isoformat()
                }

    @staticmethod
    def get_usage_by_document(document_id: str) -> Optional[dict]:
        """
        获取指定文档的 Token 使用记录。

        Args:
            document_id: 文档 ID

        Returns:
            Token 使用记录字典，不存在则返回 None
        """
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, document_id, user_id, prompt_tokens, completion_tokens,
                           total_tokens, page_count, created_at
                    FROM ocr_token_usage
                    WHERE document_id = %s
                    """,
                    (document_id,)
                )
                row = cursor.fetchone()
                if row:
                    return {
                        "id": row["id"],
                        "document_id": row["document_id"],
                        "user_id": row["user_id"],
                        "prompt_tokens": row["prompt_tokens"],
                        "completion_tokens": row["completion_tokens"],
                        "total_tokens": row["total_tokens"],
                        "page_count": row["page_count"],
                        "created_at": row["created_at"].isoformat() if row["created_at"] else None
                    }
                return None

    @staticmethod
    def get_daily_stats(days: int = 30) -> list:
        """
        获取按天统计的 OCR Token 使用量。

        Args:
            days: 统计天数，默认 30

        Returns:
            每日统计列表
        """
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
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
                    WHERE created_at >= DATE_SUB(CURDATE(), INTERVAL %s DAY)
                    GROUP BY DATE(created_at)
                    ORDER BY date DESC
                    """,
                    (days,)
                )
                rows = cursor.fetchall()
                return [
                    {
                        "date": row["date"].isoformat() if row["date"] else None,
                        "document_count": row["document_count"] or 0,
                        "prompt_tokens": row["total_prompt_tokens"] or 0,
                        "completion_tokens": row["total_completion_tokens"] or 0,
                        "total_tokens": row["total_tokens"] or 0,
                        "page_count": row["total_pages"] or 0,
                        "user_count": row["user_count"] or 0
                    }
                    for row in rows
                ]

    @staticmethod
    def get_total_stats() -> dict:
        """
        获取总体 OCR Token 统计信息。

        Returns:
            总体统计字典
        """
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
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
                row = cursor.fetchone()
                return {
                    "total_documents": row["total_documents"] or 0,
                    "total_prompt_tokens": row["total_prompt_tokens"] or 0,
                    "total_completion_tokens": row["total_completion_tokens"] or 0,
                    "total_tokens": row["total_tokens"] or 0,
                    "total_pages": row["total_pages"] or 0,
                    "total_users": row["total_users"] or 0,
                    "total_days": row["total_days"] or 0
                }

