"""图谱导入运行记录的持久化组件。"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Callable, Optional

from app.backend.repositories.database import execute, fetch_all, fetch_one


class GraphImportRepositoryMixin:
    """封装图谱导入 run 与 chunk 的数据库访问。"""

    GRAPH_IMPORT_CHUNK_LIMIT: int
    GRAPH_IMPORT_CHUNK_STRATEGY_VERSION: int
    GRAPH_LLM_MAX_ATTEMPTS: int
    IMPORT_JOB_TYPE: str
    JOB_STATUS_QUEUED: str
    JOB_STATUS_RUNNING: str
    _job_lock: asyncio.Lock
    _jobs: dict[str, dict[str, Any]]
    _json_loads: Callable[[Any, Any], Any]
    _json_dumps: Callable[[Any], str]
    _normalize_chunk_status: Callable[[Any], str]
    _build_job_response: Callable[[dict[str, Any]], dict[str, Any]]
    _clone_lane_statuses: Callable[[list[dict[str, Any]]], list[dict[str, Any]]]

    async def _find_resumable_graph_run(  # pragma: no cover - graph import persistence adapter
        self,
        file_hash: str,
        *,
        chunk_limit: int,
        chunk_strategy_version: int,
    ) -> Optional[dict[str, Any]]:
        """查找同文件且分块策略兼容的未完成导入。"""
        row = await fetch_one(
            """
            SELECT *
            FROM graph_import_runs
            WHERE file_hash = :file_hash
              AND chunk_limit = :chunk_limit
              AND chunk_strategy_version = :chunk_strategy_version
              AND status IN ('running', 'paused')
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            {
                "file_hash": file_hash,
                "chunk_limit": chunk_limit,
                "chunk_strategy_version": chunk_strategy_version,
            },
        )
        if not row:
            return None
        row["docling_failed_pages"] = self._json_loads(
            row.get("docling_failed_pages"), []
        )
        return row

    async def _find_latest_graph_run_by_file_hash(
        self, file_hash: str
    ) -> Optional[
        dict[str, Any]
    ]:  # pragma: no cover - graph import persistence adapter
        """查找同文件最近一次导入记录，不要求策略兼容。"""
        row = await fetch_one(
            """
            SELECT *
            FROM graph_import_runs
            WHERE file_hash = :file_hash
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            {"file_hash": file_hash},
        )
        if not row:
            return None
        row["docling_failed_pages"] = self._json_loads(
            row.get("docling_failed_pages"), []
        )
        return row

    async def _find_first_unfinished_chunk_index(
        self, run_id: str
    ) -> Optional[int]:  # pragma: no cover - graph import persistence adapter
        """返回 run 中第一个未完成块。"""
        row = await fetch_one(
            """
            SELECT chunk_index
            FROM graph_import_chunks
            WHERE run_id = :run_id
              AND status <> 'succeeded'
            ORDER BY chunk_index ASC
            LIMIT 1
            """,
            {"run_id": run_id},
        )
        if not row:
            return None
        try:
            return int(row.get("chunk_index") or 0)
        except (TypeError, ValueError):
            return None

    async def _find_active_job_by_run_id(
        self, run_id: str
    ) -> Optional[
        dict[str, Any]
    ]:  # pragma: no cover - graph import persistence adapter
        """查找当前进程内是否已有同 run 的活跃任务。"""
        async with self._job_lock:
            for job in self._jobs.values():
                if job.get("run_id") == run_id and job.get("status") in {
                    self.JOB_STATUS_QUEUED,
                    self.JOB_STATUS_RUNNING,
                }:
                    return self._build_job_response(job)
        return None

    async def _create_graph_import_run(  # pragma: no cover - graph import persistence adapter
        self,
        *,
        run_id: str,
        file_hash: str,
        file_name: str,
        file_size: int,
        prepared: dict[str, Any],
    ) -> None:
        """创建新的图谱导入 run。"""
        await execute(
            """
            INSERT INTO graph_import_runs (
                run_id, file_hash, file_name, file_size, status, parser_used,
                parse_warning, parsed_text_length, total_chars, processed_chars,
                total_chunks, completed_chunks, entity_count, relation_count,
                docling_failed_pages, pdf_fallback_used, chunk_limit, chunk_strategy_version, last_error
            )
            VALUES (
                :run_id, :file_hash, :file_name, :file_size, 'running', :parser_used,
                :parse_warning, :parsed_text_length, :total_chars, 0,
                :total_chunks, 0, 0, 0, :docling_failed_pages, :pdf_fallback_used, :chunk_limit, :chunk_strategy_version, NULL
            )
            """,
            {
                "run_id": run_id,
                "file_hash": file_hash,
                "file_name": file_name,
                "file_size": file_size,
                "parser_used": prepared.get("parser_used") or "",
                "parse_warning": prepared.get("parse_warning") or "",
                "parsed_text_length": prepared.get("parsed_text_length") or 0,
                "total_chars": prepared.get("parsed_text_length") or 0,
                "total_chunks": prepared.get("chunk_count") or 0,
                "docling_failed_pages": self._json_dumps(
                    prepared.get("docling_failed_pages") or []
                ),
                "pdf_fallback_used": 1 if prepared.get("pdf_fallback_used") else 0,
                "chunk_limit": self.GRAPH_IMPORT_CHUNK_LIMIT,
                "chunk_strategy_version": self.GRAPH_IMPORT_CHUNK_STRATEGY_VERSION,
            },
        )

    async def _initialize_graph_import_chunks(
        self, run_id: str, chunks: list[str]
    ) -> None:  # pragma: no cover - graph import persistence adapter
        """初始化块级断点记录。"""
        for index, chunk in enumerate(chunks, start=1):
            await execute(
                """
                INSERT INTO graph_import_chunks (
                    run_id, chunk_index, chunk_chars, status,
                    entity_count, relation_count, node_batches, relation_batches, error_message
                )
                VALUES (
                    :run_id, :chunk_index, :chunk_chars, 'pending',
                    0, 0, 0, 0, NULL
                )
                ON DUPLICATE KEY UPDATE chunk_chars = VALUES(chunk_chars)
                """,
                {
                    "run_id": run_id,
                    "chunk_index": index,
                    "chunk_chars": len(chunk),
                },
            )

    async def _sync_graph_import_run_metadata(  # pragma: no cover - graph import persistence adapter
        self,
        run_id: str,
        *,
        file_name: str,
        file_size: int,
        prepared: dict[str, Any],
        status: str = "running",
        clear_error: bool = False,
    ) -> None:
        """同步 run 的解析元数据。"""
        await execute(
            """
            UPDATE graph_import_runs
            SET file_name = :file_name,
                file_size = :file_size,
                status = :status,
                parser_used = :parser_used,
                parse_warning = :parse_warning,
                parsed_text_length = :parsed_text_length,
                total_chars = :total_chars,
                total_chunks = :total_chunks,
                docling_failed_pages = :docling_failed_pages,
                pdf_fallback_used = :pdf_fallback_used,
                chunk_limit = :chunk_limit,
                chunk_strategy_version = :chunk_strategy_version,
                last_error = CASE WHEN :clear_error = 1 THEN NULL ELSE last_error END
            WHERE run_id = :run_id
            """,
            {
                "run_id": run_id,
                "file_name": file_name,
                "file_size": file_size,
                "status": status,
                "parser_used": prepared.get("parser_used") or "",
                "parse_warning": prepared.get("parse_warning") or "",
                "parsed_text_length": prepared.get("parsed_text_length") or 0,
                "total_chars": prepared.get("parsed_text_length") or 0,
                "total_chunks": prepared.get("chunk_count") or 0,
                "docling_failed_pages": self._json_dumps(
                    prepared.get("docling_failed_pages") or []
                ),
                "pdf_fallback_used": 1 if prepared.get("pdf_fallback_used") else 0,
                "chunk_limit": self.GRAPH_IMPORT_CHUNK_LIMIT,
                "chunk_strategy_version": self.GRAPH_IMPORT_CHUNK_STRATEGY_VERSION,
                "clear_error": 1 if clear_error else 0,
            },
        )

    async def _load_graph_import_chunks(
        self, run_id: str
    ) -> list[dict[str, Any]]:  # pragma: no cover - graph import persistence adapter
        """加载块级断点记录。"""
        rows = await fetch_all(
            """
            SELECT *
            FROM graph_import_chunks
            WHERE run_id = :run_id
            ORDER BY chunk_index ASC
            """,
            {"run_id": run_id},
        )
        for row in rows:
            row["status"] = self._normalize_chunk_status(row.get("status"))
            for key in (
                "chunk_index",
                "chunk_chars",
                "entity_count",
                "relation_count",
                "node_batches",
                "relation_batches",
            ):
                try:
                    row[key] = int(row.get(key) or 0)
                except (TypeError, ValueError):
                    row[key] = 0
        return rows

    async def _recompute_graph_run_progress(
        self, run_id: str
    ) -> dict[str, int]:  # pragma: no cover - graph import persistence adapter
        """根据块级状态重算 run 进度。"""
        row = (
            await fetch_one(
                """
            SELECT
                COALESCE(SUM(CASE WHEN status = 'succeeded' THEN chunk_chars ELSE 0 END), 0) AS processed_chars,
                COALESCE(SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END), 0) AS completed_chunks,
                COALESCE(SUM(CASE WHEN status = 'succeeded' THEN entity_count ELSE 0 END), 0) AS entity_count,
                COALESCE(SUM(CASE WHEN status = 'succeeded' THEN relation_count ELSE 0 END), 0) AS relation_count,
                COALESCE(SUM(CASE WHEN status = 'succeeded' THEN node_batches ELSE 0 END), 0) AS node_batches,
                COALESCE(SUM(CASE WHEN status = 'succeeded' THEN relation_batches ELSE 0 END), 0) AS relation_batches
            FROM graph_import_chunks
            WHERE run_id = :run_id
            """,
                {"run_id": run_id},
            )
            or {}
        )
        progress = {
            "processed_chars": int(row.get("processed_chars") or 0),
            "completed_chunks": int(row.get("completed_chunks") or 0),
            "entity_count": int(row.get("entity_count") or 0),
            "relation_count": int(row.get("relation_count") or 0),
            "node_batches": int(row.get("node_batches") or 0),
            "relation_batches": int(row.get("relation_batches") or 0),
        }
        await execute(
            """
            UPDATE graph_import_runs
            SET processed_chars = :processed_chars,
                completed_chunks = :completed_chunks,
                entity_count = :entity_count,
                relation_count = :relation_count
            WHERE run_id = :run_id
            """,
            {"run_id": run_id, **progress},
        )
        return progress

    async def _mark_graph_chunk_running(
        self, run_id: str, chunk_index: int
    ) -> None:  # pragma: no cover - graph import persistence adapter
        """标记块开始执行。"""
        await execute(
            """
            UPDATE graph_import_chunks
            SET status = 'running',
                started_at = CURRENT_TIMESTAMP,
                finished_at = NULL,
                error_message = NULL
            WHERE run_id = :run_id AND chunk_index = :chunk_index
            """,
            {"run_id": run_id, "chunk_index": chunk_index},
        )

    async def _mark_graph_chunk_succeeded(  # pragma: no cover - graph import persistence adapter
        self,
        run_id: str,
        *,
        chunk_index: int,
        entity_count: int,
        relation_count: int,
        node_batches: int,
        relation_batches: int,
    ) -> None:
        """标记块执行成功。"""
        await execute(
            """
            UPDATE graph_import_chunks
            SET status = 'succeeded',
                entity_count = :entity_count,
                relation_count = :relation_count,
                node_batches = :node_batches,
                relation_batches = :relation_batches,
                error_message = NULL,
                finished_at = CURRENT_TIMESTAMP
            WHERE run_id = :run_id AND chunk_index = :chunk_index
            """,
            {
                "run_id": run_id,
                "chunk_index": chunk_index,
                "entity_count": entity_count,
                "relation_count": relation_count,
                "node_batches": node_batches,
                "relation_batches": relation_batches,
            },
        )

    async def _mark_graph_chunk_failed(
        self, run_id: str, chunk_index: int, error_message: str
    ) -> None:  # pragma: no cover - graph import persistence adapter
        """标记块执行失败。"""
        await execute(
            """
            UPDATE graph_import_chunks
            SET status = 'failed',
                error_message = :error_message,
                finished_at = CURRENT_TIMESTAMP
            WHERE run_id = :run_id AND chunk_index = :chunk_index
            """,
            {
                "run_id": run_id,
                "chunk_index": chunk_index,
                "error_message": error_message[:4000],
            },
        )

    async def _set_graph_run_status(  # pragma: no cover - graph import persistence adapter
        self,
        run_id: str,
        status: str,
        *,
        last_error: Optional[str] = None,
        finished: bool = False,
    ) -> None:
        """更新 run 状态。"""
        await execute(
            """
            UPDATE graph_import_runs
            SET status = :status,
                last_error = :last_error,
                finished_at = CASE WHEN :finished = 1 THEN CURRENT_TIMESTAMP ELSE finished_at END
            WHERE run_id = :run_id
            """,
            {
                "run_id": run_id,
                "status": status,
                "last_error": last_error[:4000] if last_error else None,
                "finished": 1 if finished else 0,
            },
        )

    async def _update_job_resume_state(  # pragma: no cover - graph import persistence adapter
        self,
        job_id: str,
        *,
        run_id: str,
        file_hash: str,
        is_resumed: bool,
        resumed_from_chunk: Optional[int],
        chunk_limit: Optional[int] = None,
        chunk_strategy_version: Optional[int] = None,
        cache_used: Optional[bool] = None,
        cache_invalidated_reason: Optional[str] = None,
        resume_compatible: Optional[bool] = None,
    ) -> None:
        """把恢复元数据写回进程内任务。"""
        async with self._job_lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job["run_id"] = run_id
            job["file_hash"] = file_hash
            job["is_resumed"] = is_resumed
            job["resume_run_id"] = run_id if is_resumed else None
            job["resumed_from_chunk"] = resumed_from_chunk
            if chunk_limit is not None:
                job["chunk_limit"] = chunk_limit
            if chunk_strategy_version is not None:
                job["chunk_strategy_version"] = chunk_strategy_version
            if cache_used is not None:
                job["cache_used"] = cache_used
            if cache_invalidated_reason is not None:
                job["cache_invalidated_reason"] = cache_invalidated_reason
            if resume_compatible is not None:
                job["resume_compatible"] = resume_compatible

    async def _update_job_parallel_state(  # pragma: no cover - graph import persistence adapter
        self,
        job_id: str,
        *,
        parallelism: Optional[int] = None,
        active_lanes: Optional[int] = None,
        current_attempt: Optional[int] = None,
        max_attempts: Optional[int] = None,
        lane_statuses: Optional[list[dict[str, Any]]] = None,
    ) -> None:
        """把并发抽取运行态写回进程内任务。"""
        async with self._job_lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            if parallelism is not None:
                job["parallelism"] = parallelism
            if active_lanes is not None:
                job["active_lanes"] = active_lanes
            if current_attempt is not None:
                job["current_attempt"] = current_attempt
            if max_attempts is not None:
                job["max_attempts"] = max_attempts
            if lane_statuses is not None:
                job["lane_statuses"] = self._clone_lane_statuses(lane_statuses)

    @staticmethod
    def _build_graph_run_progress_text(
        run_row: dict[str, Any]
    ) -> str:  # pragma: no cover - graph import persistence adapter
        """将持久化 run 转换为最近任务摘要。"""
        status = str(run_row.get("status") or "")
        completed_chunks = int(run_row.get("completed_chunks") or 0)
        total_chunks = int(run_row.get("total_chunks") or 0)
        processed_chars = int(run_row.get("processed_chars") or 0)
        total_chars = int(run_row.get("total_chars") or 0)
        if status == "paused":
            return "已暂停，可重新提交同文件继续"
        if status == "succeeded":
            return "任务执行完成"
        if total_chunks > 0:
            return f"已完成 {completed_chunks}/{total_chunks} 块，已处理 {processed_chars}/{total_chars} 字"
        return "任务执行中"

    @staticmethod
    def _format_run_timestamp(value: Any) -> Any:
        """将数据库 datetime 转换为 API 使用的 UTC 文本。"""
        return (
            value.isoformat(timespec="seconds") + "Z"
            if isinstance(value, datetime)
            else value
        )

    def _run_row_to_job_response(
        self, run_row: dict[str, Any]
    ) -> dict[str, Any]:  # pragma: no cover - graph import persistence adapter
        """把持久化 run 映射为任务响应结构。"""
        docling_failed_pages = self._json_loads(run_row.get("docling_failed_pages"), [])
        result: dict[str, Any] = {
            "file_name": run_row.get("file_name"),
            "file_hash": run_row.get("file_hash"),
            "parsed_text_length": int(run_row.get("parsed_text_length") or 0),
            "processed_text_length": int(run_row.get("processed_chars") or 0),
            "chunk_count": int(run_row.get("total_chunks") or 0),
            "completed_chunks": int(run_row.get("completed_chunks") or 0),
            "chunk_limit": int(
                run_row.get("chunk_limit") or self.GRAPH_IMPORT_CHUNK_LIMIT
            ),
            "chunk_strategy_version": int(
                run_row.get("chunk_strategy_version")
                or self.GRAPH_IMPORT_CHUNK_STRATEGY_VERSION
            ),
            "entity_count": int(run_row.get("entity_count") or 0),
            "relation_count": int(run_row.get("relation_count") or 0),
            "parser_used": run_row.get("parser_used"),
            "parse_warning": run_row.get("parse_warning") or "",
            "docling_failed_pages": docling_failed_pages,
            "pdf_fallback_used": bool(run_row.get("pdf_fallback_used")),
            "cache_used": False,
            "cache_invalidated_reason": "",
            "resume_compatible": True,
            "parallelism": 0,
            "active_lanes": 0,
            "current_attempt": 0,
            "max_attempts": self.GRAPH_LLM_MAX_ATTEMPTS,
            "lane_statuses": [],
            "message": self._build_graph_run_progress_text(run_row),
        }
        if run_row.get("status") == "paused":
            result["pause_reason"] = (
                run_row.get("last_error") or "当前任务已暂停，可重新提交同文件继续"
            )
        return {
            "job_id": f"graph-run-{run_row.get('run_id')}",
            "job_type": self.IMPORT_JOB_TYPE,
            "status": run_row.get("status"),
            "progress_text": self._build_graph_run_progress_text(run_row),
            "created_at": self._format_run_timestamp(run_row.get("created_at")),
            "started_at": self._format_run_timestamp(run_row.get("created_at")),
            "finished_at": self._format_run_timestamp(run_row.get("finished_at")),
            "stage": None,
            "stage_detail": "",
            "stage_started_at": None,
            "last_heartbeat_at": self._format_run_timestamp(run_row.get("updated_at")),
            "total_chars": int(run_row.get("total_chars") or 0),
            "processed_chars": int(run_row.get("processed_chars") or 0),
            "total_chunks": int(run_row.get("total_chunks") or 0),
            "completed_chunks": int(run_row.get("completed_chunks") or 0),
            "current_chunk_index": None,
            "current_chunk_chars": None,
            "file_hash": run_row.get("file_hash"),
            "run_id": run_row.get("run_id"),
            "is_resumed": False,
            "resume_run_id": None,
            "resumed_from_chunk": None,
            "chunk_limit": int(
                run_row.get("chunk_limit") or self.GRAPH_IMPORT_CHUNK_LIMIT
            ),
            "chunk_strategy_version": int(
                run_row.get("chunk_strategy_version")
                or self.GRAPH_IMPORT_CHUNK_STRATEGY_VERSION
            ),
            "cache_used": False,
            "cache_invalidated_reason": "",
            "resume_compatible": True,
            "parallelism": 0,
            "active_lanes": 0,
            "current_attempt": 0,
            "max_attempts": self.GRAPH_LLM_MAX_ATTEMPTS,
            "lane_statuses": [],
            "warnings": [],
            "trace": [],
            "result": result,
            "error": run_row.get("last_error"),
        }
