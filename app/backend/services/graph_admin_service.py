"""管理员图谱服务。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Optional
from uuid import uuid4

from fastapi import UploadFile

from app.backend.domain.job_status import BackgroundJobStatus
from app.backend.services.background_job_runtime import BackgroundJobRuntimeMixin
from app.backend.services.graph_extraction_lane_pool import GraphExtractionLanePool
from app.backend.services.graph_crud_mixin import GraphCrudMixin
from app.backend.services.graph_import_cache_mixin import GraphImportCacheMixin
from app.backend.services.graph_import_parser import GraphImportParser
from app.backend.services.graph_import_repository_mixin import (
    GraphImportRepositoryMixin,
)
from app.backend.services.graph_import_writer import GraphImportWriter
from app.core.config import Config

logger = logging.getLogger(__name__)


class GraphAdminService(
    GraphCrudMixin,
    GraphImportCacheMixin,
    GraphImportRepositoryMixin,
    BackgroundJobRuntimeMixin,
):
    """封装管理员图谱管理与导入嵌入业务。"""

    JOB_STATUS_QUEUED = BackgroundJobStatus.QUEUED.value
    JOB_STATUS_RUNNING = BackgroundJobStatus.RUNNING.value
    JOB_STATUS_PAUSED = BackgroundJobStatus.PAUSED.value
    JOB_STATUS_SUCCEEDED = BackgroundJobStatus.SUCCEEDED.value
    JOB_STATUS_FAILED = BackgroundJobStatus.FAILED.value
    JOB_STATUS_CANCELLED = BackgroundJobStatus.CANCELLED.value

    IMPORT_JOB_TYPE = "graph_extract_import"
    EMBED_JOB_TYPE = "graph_embed_milvus"
    SOFT_TIMEOUT_SECONDS = 300
    SOFT_TIMEOUT_REPEAT_SECONDS = 60
    GRAPH_IMPORT_CACHE_VERSION = 2
    GRAPH_IMPORT_CHUNK_LIMIT = 10000
    GRAPH_IMPORT_CHUNK_STRATEGY_VERSION = 3
    GRAPH_LLM_REQUEST_TIMEOUT_SECONDS = 600
    GRAPH_LLM_MAX_ATTEMPTS = 3
    GRAPH_LLM_MAX_PARALLELISM = 3
    GRAPH_LLM_TRANSIENT_BACKOFF_SECONDS = 15

    def __init__(
        self,
        await_call: Callable[..., Awaitable[Any]],
        neo4j_client: Any,
        get_shared_kg_milvus_store: Callable[[], Any],
        validate_cypher_identifier: Callable[[Any], Optional[str]],
        sanitize_graph_properties: Callable[[Any], dict[str, Any]],
        log_api_error: Callable[..., None],
    ) -> None:
        """初始化实例并注入运行依赖。

        Args:
            await_call: 兼容同步/异步函数的调用适配器。
            neo4j_client: Neo4j 客户端实例，用于执行图数据库查询和写入。
            get_shared_kg_milvus_store: 获取图谱向量库实例的回调。
            validate_cypher_identifier: 校验 Cypher label/type/property 标识符的回调。
            sanitize_graph_properties: 清洗图谱节点和关系属性的回调。
            log_api_error: 统一记录管理员图谱接口错误的回调。

        Returns:
            None: 无返回值。
        """
        self._await_call = await_call
        self._neo4j_client = neo4j_client
        self._get_shared_kg_milvus_store = get_shared_kg_milvus_store
        self._validate_cypher_identifier = validate_cypher_identifier
        self._sanitize_graph_properties = sanitize_graph_properties
        self._log_api_error = log_api_error
        self._config = Config()
        self._graph_llm_lane_configs = list(self._config.GRAPH_LLM_LANE_CONFIGS)
        self._graph_extraction_lane_pool = GraphExtractionLanePool()
        self._graph_import_parser = GraphImportParser(
            cache=self,
            chunk_limit=self.GRAPH_IMPORT_CHUNK_LIMIT,
            chunk_strategy_version=self.GRAPH_IMPORT_CHUNK_STRATEGY_VERSION,
            log_api_error=log_api_error,
        )
        self._initialize_background_job_runtime()
        self._graph_import_writer = GraphImportWriter(
            neo4j_client=neo4j_client,
            enter_job_stage=self._enter_job_stage,
            heartbeat_job_stage=self._heartbeat_job_stage,
            complete_job_stage=self._complete_job_stage,
            mark_chunk_succeeded=self._mark_graph_chunk_succeeded,
            mark_chunk_failed=self._mark_graph_chunk_failed,
            set_run_status=self._set_graph_run_status,
            recompute_run_progress=self._recompute_graph_run_progress,
        )

    @staticmethod
    def _format_resume_message(chunk_index: Optional[int]) -> str:
        """生成自动续跑提示文案。"""
        if chunk_index and chunk_index > 0:
            return f"检测到未完成导入，正在从第 {chunk_index} 块继续"
        return "检测到未完成导入，正在自动继续执行"

    async def submit_graph_extract_import_job(
        self, file: UploadFile
    ) -> dict[str, Any]:  # pragma: no cover - background task orchestration
        """读取上传文件并提交图谱抽取导入后台任务。

        Args:
            file: FastAPI 上传文件对象。

        Returns:
            dict[str, Any]: 任务提交结果、去重命中结果或校验错误。
        """
        filename = file.filename or "uploaded_file"
        try:
            file_bytes = await file.read()
            if not file_bytes:
                return {"ok": False, "error": "上传文件为空"}
            if len(file_bytes) > 50 * 1024 * 1024:
                return {"ok": False, "error": "文件大小不能超过 50MB"}
            file_hash = self._compute_file_hash(file_bytes)
            resumable_run = await self._find_resumable_graph_run(
                file_hash,
                chunk_limit=self.GRAPH_IMPORT_CHUNK_LIMIT,
                chunk_strategy_version=self.GRAPH_IMPORT_CHUNK_STRATEGY_VERSION,
            )
            if resumable_run:
                active_job = await self._find_active_job_by_run_id(
                    str(resumable_run.get("run_id") or "")
                )
                if active_job and resumable_run.get("status") == "running":
                    active_job["message"] = (
                        "检测到同文件导入任务仍在执行，已返回当前任务状态"
                    )
                    active_job["deduplicated"] = True
                    active_job["existing_job_id"] = active_job.get("job_id")
                    active_job["existing_run_id"] = resumable_run.get("run_id")
                    return {"ok": True, "data": active_job}
            resumed_from_chunk = None
            if resumable_run:
                resumed_from_chunk = await self._find_first_unfinished_chunk_index(
                    str(resumable_run.get("run_id") or "")
                )
            return await self._start_background_job(
                job_type=self.IMPORT_JOB_TYPE,
                runner=lambda job_id: self._extract_and_import_graph_sync(
                    file_bytes, filename, job_id
                ),
                dedupe_active=True,
                seed={
                    "file_hash": file_hash,
                    "is_resumed": bool(resumable_run),
                    "resume_run_id": (
                        resumable_run.get("run_id") if resumable_run else None
                    ),
                    "resumed_from_chunk": resumed_from_chunk,
                    "chunk_limit": self.GRAPH_IMPORT_CHUNK_LIMIT,
                    "chunk_strategy_version": self.GRAPH_IMPORT_CHUNK_STRATEGY_VERSION,
                    "resume_compatible": bool(resumable_run) if resumable_run else None,
                    "parallelism": min(
                        self.GRAPH_LLM_MAX_PARALLELISM,
                        len(self._graph_llm_lane_configs),
                    ),
                    "active_lanes": min(
                        self.GRAPH_LLM_MAX_PARALLELISM,
                        len(self._graph_llm_lane_configs),
                    ),
                    "current_attempt": 0,
                    "max_attempts": self.GRAPH_LLM_MAX_ATTEMPTS,
                    "progress_text": (
                        self._format_resume_message(resumed_from_chunk)
                        if resumable_run
                        else "任务已提交，等待执行"
                    ),
                },
            )
        except (
            RuntimeError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            OSError,
            ImportError,
        ) as exc:
            self._log_api_error(
                "/admin/graph/jobs/extract-import",
                "extract_import_submit",
                exc,
                filename=filename,
            )
            return {"ok": False, "error": f"提交抽取导入任务失败: {exc}"}

    async def submit_graph_embed_milvus_job(
        self, data: Optional[dict[str, Any]] = None
    ) -> dict[str, Any]:  # pragma: no cover - background task orchestration
        """提交图谱嵌入 Milvus 后台任务。

        Args:
            data: 可选标签列表和批大小配置。

        Returns:
            dict[str, Any]: 任务提交结果或错误响应。
        """
        try:
            payload = data or {}
            labels_obj = payload.get("labels")
            labels = (
                [str(item).strip() for item in labels_obj if str(item).strip()]
                if isinstance(labels_obj, list)
                else None
            )
            batch_size_raw = payload.get("batch_size", 100)
            try:
                batch_size = int(batch_size_raw)
            except (TypeError, ValueError):
                batch_size = 100
            return await self._start_background_job(
                job_type=self.EMBED_JOB_TYPE,
                runner=lambda job_id: self._embed_graph_to_milvus_sync(payload, job_id),
                seed={
                    "labels": labels or None,
                    "batch_size": batch_size,
                },
            )
        except (
            RuntimeError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            OSError,
            ImportError,
        ) as exc:
            self._log_api_error(
                "/admin/graph/jobs/embed-milvus", "embed_graph_submit", exc
            )
            return {"ok": False, "error": f"提交嵌入任务失败: {exc}"}

    async def _extract_and_import_graph_sync(
        self, file_bytes: bytes, filename: str, job_id: str
    ) -> dict[str, Any]:  # pragma: no cover - external LLM/Neo4j background runtime
        """执行图谱抽取导入主体，包含解析、续跑、抽取和写入。

        Args:
            file_bytes: 上传文件二进制内容。
            filename: 上传文件名。
            job_id: 进程内后台任务标识。

        Returns:
            dict[str, Any]: 图谱导入结果，可能返回暂停态结果。
        """
        loop = asyncio.get_running_loop()
        stage_callback = self._make_stage_event_callback(loop, job_id)
        file_hash = self._compute_file_hash(file_bytes)
        prepared = await asyncio.to_thread(
            self._graph_import_parser.parse_and_split,
            file_bytes,
            filename,
            stage_callback,
        )
        chunks = list(prepared.pop("_chunks", []))
        total_chunks = len(chunks)
        file_size = len(file_bytes)
        chunk_limit = int(prepared.get("chunk_limit") or self.GRAPH_IMPORT_CHUNK_LIMIT)
        chunk_strategy_version = int(
            prepared.get("chunk_strategy_version")
            or self.GRAPH_IMPORT_CHUNK_STRATEGY_VERSION
        )
        cache_used = bool(prepared.get("cache_used"))
        cache_invalidated_reason = str(prepared.get("cache_invalidated_reason") or "")

        resumable_run = await self._find_resumable_graph_run(
            file_hash,
            chunk_limit=chunk_limit,
            chunk_strategy_version=chunk_strategy_version,
        )
        latest_run = await self._find_latest_graph_run_by_file_hash(file_hash)
        existing_chunks: list[dict[str, Any]] = []
        is_resumed = False
        run_id = ""
        resumed_from_chunk: Optional[int] = None
        resume_compatible: Optional[bool] = None

        if latest_run and (
            int(latest_run.get("chunk_limit") or 0) != chunk_limit
            or int(latest_run.get("chunk_strategy_version") or 0)
            != chunk_strategy_version
        ):
            resume_compatible = False
            compatibility_reason = (
                f"旧断点分块策略已过期（旧策略: limit={int(latest_run.get('chunk_limit') or 0)}, "
                f"version={int(latest_run.get('chunk_strategy_version') or 0)}；"
                f"当前策略: limit={chunk_limit}, version={chunk_strategy_version}），已重新建任务"
            )
            if str(latest_run.get("status") or "") in {"running", "paused"}:
                await self._set_graph_run_status(
                    str(latest_run.get("run_id") or ""),
                    "failed",
                    last_error=compatibility_reason,
                    finished=True,
                )
            cache_invalidated_reason = compatibility_reason
            logger.info(
                "graph_import.resume_incompatible file_hash=%s latest_run_id=%s reason=%s",
                file_hash,
                latest_run.get("run_id"),
                compatibility_reason,
            )
        elif resumable_run:
            resume_compatible = True

        if resumable_run:
            run_id = str(resumable_run.get("run_id") or "")
            existing_chunks = await self._load_graph_import_chunks(run_id)
            if existing_chunks and len(existing_chunks) != total_chunks:
                resume_compatible = False
                incompatibility_reason = (
                    "旧导入断点与当前分块结果不一致，已终止旧断点并改为新任务导入"
                )
                await self._set_graph_run_status(
                    run_id,
                    "failed",
                    last_error=incompatibility_reason,
                    finished=True,
                )
                cache_invalidated_reason = incompatibility_reason
                resumable_run = None
                existing_chunks = []
                logger.info(
                    "graph_import.resume_chunk_mismatch file_hash=%s run_id=%s chunk_limit=%s total_chunks=%s",
                    file_hash,
                    run_id,
                    chunk_limit,
                    total_chunks,
                )

        if not resumable_run:
            run_id = f"graph-run-{uuid4().hex}"
            await self._create_graph_import_run(
                run_id=run_id,
                file_hash=file_hash,
                file_name=filename,
                file_size=file_size,
                prepared=prepared,
            )
            await self._initialize_graph_import_chunks(run_id, chunks)
            existing_chunks = await self._load_graph_import_chunks(run_id)
        else:
            is_resumed = True
            await self._sync_graph_import_run_metadata(
                run_id,
                file_name=filename,
                file_size=file_size,
                prepared=prepared,
                status="running",
                clear_error=True,
            )
            if not existing_chunks:
                await self._initialize_graph_import_chunks(run_id, chunks)
                existing_chunks = await self._load_graph_import_chunks(run_id)

        logger.info(
            "graph_import.run_selected file_hash=%s run_id=%s resumed=%s chunk_limit=%s chunk_strategy_version=%s total_chunks=%s first_chunk_chars=%s cache_used=%s resume_compatible=%s",
            file_hash,
            run_id,
            is_resumed,
            chunk_limit,
            chunk_strategy_version,
            total_chunks,
            len(chunks[0]) if chunks else 0,
            cache_used,
            resume_compatible,
        )

        progress = await self._recompute_graph_run_progress(run_id)
        chunk_map = {
            int(item.get("chunk_index") or 0): item for item in existing_chunks
        }
        for index in range(1, total_chunks + 1):
            if chunk_map.get(index, {}).get("status") != "succeeded":
                resumed_from_chunk = index if is_resumed else None
                break

        await self._update_job_resume_state(
            job_id,
            run_id=run_id,
            file_hash=file_hash,
            is_resumed=is_resumed,
            resumed_from_chunk=resumed_from_chunk,
            chunk_limit=chunk_limit,
            chunk_strategy_version=chunk_strategy_version,
            cache_used=cache_used,
            cache_invalidated_reason=cache_invalidated_reason,
            resume_compatible=resume_compatible,
        )
        prepared.update(
            {
                "file_hash": file_hash,
                "run_id": run_id,
                "is_resumed": is_resumed,
                "resume_run_id": run_id if is_resumed else None,
                "resumed_from_chunk": resumed_from_chunk,
                "chunk_limit": chunk_limit,
                "chunk_strategy_version": chunk_strategy_version,
                "cache_used": cache_used,
                "cache_invalidated_reason": cache_invalidated_reason,
                "resume_compatible": resume_compatible,
            }
        )

        if is_resumed:
            await self._heartbeat_job_stage(
                job_id,
                detail=self._format_resume_message(resumed_from_chunk),
                progress_text=self._format_resume_message(resumed_from_chunk),
                metrics={
                    "total_chars": prepared.get("parsed_text_length") or 0,
                    "processed_chars": progress["processed_chars"],
                    "total_chunks": total_chunks,
                    "completed_chunks": progress["completed_chunks"],
                    "current_chunk_index": resumed_from_chunk or 0,
                    "current_chunk_chars": (
                        len(chunks[(resumed_from_chunk or 1) - 1])
                        if resumed_from_chunk and resumed_from_chunk <= total_chunks
                        else 0
                    ),
                },
            )

        extraction_result = await self._graph_extraction_lane_pool.run(
            runtime=self,
            writer=self._graph_import_writer,
            run_id=run_id,
            job_id=job_id,
            chunks=chunks,
            chunk_map=chunk_map,
            prepared=prepared,
            progress=progress,
            stage_callback=stage_callback,
            lane_configs=self._graph_llm_lane_configs,
            max_parallelism=self.GRAPH_LLM_MAX_PARALLELISM,
            request_timeout_seconds=self.GRAPH_LLM_REQUEST_TIMEOUT_SECONDS,
            max_attempts=self.GRAPH_LLM_MAX_ATTEMPTS,
            transient_backoff_seconds=self.GRAPH_LLM_TRANSIENT_BACKOFF_SECONDS,
        )

        await self._set_graph_run_status(run_id, "succeeded", finished=True)
        await self._enter_job_stage(job_id, "finalize", "图谱抽取完成，正在整理结果")
        await self._complete_job_stage(job_id, detail="任务执行完成")

        prepared.update(
            {
                "file_hash": file_hash,
                "run_id": run_id,
                "is_resumed": is_resumed,
                "resume_run_id": run_id if is_resumed else None,
                "resumed_from_chunk": resumed_from_chunk,
                "chunk_limit": chunk_limit,
                "chunk_strategy_version": chunk_strategy_version,
                "cache_used": cache_used,
                "cache_invalidated_reason": cache_invalidated_reason,
                "resume_compatible": resume_compatible,
                **extraction_result,
                "message": (
                    f"抽取并导入完成，实体 {extraction_result['entity_count']} 个，"
                    f"关系 {extraction_result['relation_count']} 条"
                ),
            }
        )
        return prepared

    async def _embed_graph_to_milvus_sync(
        self, data: Optional[dict[str, Any]], job_id: str
    ) -> dict[str, Any]:  # pragma: no cover - external Milvus runtime
        """执行图谱嵌入主体，从 Neo4j 读取数据并重建 Milvus 索引。

        Args:
            data: 可选标签过滤和批大小配置。
            job_id: 进程内后台任务标识。

        Returns:
            dict[str, Any]: Milvus 重建统计和完成时间。
        """
        await self._enter_job_stage(job_id, "read_graph", "准备读取图谱数据")
        data = data or {}
        labels = data.get("labels")
        batch_size = data.get("batch_size", 100)
        kg_store = self._get_shared_kg_milvus_store()
        loop = asyncio.get_running_loop()

        def progress_callback(text: str) -> None:
            """将 Milvus 重建进度回传到后台任务心跳。"""
            try:
                asyncio.run_coroutine_threadsafe(
                    self._heartbeat_job_stage(
                        job_id, detail=str(text or ""), progress_text=str(text or "")
                    ),
                    loop,
                )
            except RuntimeError:
                return

        stats = await kg_store.rebuild_from_neo4j(
            self._neo4j_client,
            labels,
            batch_size,
            progress_callback=progress_callback,
        )
        await self._complete_job_stage(job_id, detail="Milvus 嵌入完成")
        await self._enter_job_stage(
            job_id, "finalize", "Milvus 嵌入已完成，正在整理结果"
        )
        await self._complete_job_stage(job_id, detail="任务执行完成")
        return {
            "message": f"知识图谱嵌入成功，共 {stats['total_documents']} 条记录",
            "stats": stats,
            "timestamp": stats.get("timestamp"),
        }
