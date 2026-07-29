"""管理员图谱服务。"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Optional
from uuid import uuid4

from fastapi import UploadFile

from app.backend.domain.job_status import BackgroundJobStatus
from app.backend.services.background_job_runtime import BackgroundJobRuntimeMixin
from app.backend.services.graph_extraction_lane_pool import GraphExtractionLanePool
from app.backend.services.graph_crud_mixin import GraphCrudMixin
from app.backend.services.graph_import_cache_mixin import GraphImportCacheMixin
from app.backend.services.graph_import_parser import GraphImportParser
from app.backend.services.graph_import_repository_mixin import GraphImportRepositoryMixin
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
    GRAPH_IMPORT_CHUNK_STRATEGY_VERSION = 2
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
    def _normalize_chunk_status(status: Any) -> str:
        """将未知状态收敛到块级状态枚举。"""
        status_text = str(status or "").strip().lower()
        if status_text in {"pending", "running", "succeeded", "failed"}:
            return status_text
        return "pending"

    @staticmethod
    def _format_resume_message(chunk_index: Optional[int]) -> str:
        """生成自动续跑提示文案。"""
        if chunk_index and chunk_index > 0:
            return f"检测到未完成导入，正在从第 {chunk_index} 块继续"
        return "检测到未完成导入，正在自动继续执行"

    @staticmethod
    def _first_error_line(error_text: Optional[str]) -> str:
        """提取异常首行，避免前端展示整段堆栈。"""
        text = str(error_text or "").strip()
        if not text:
            return ""
        return next((line.strip() for line in text.splitlines() if line.strip()), "")

    @classmethod
    def _summarize_pause_error(cls, error_text: Optional[str]) -> str:
        """将块级中断异常归一化为适合前端展示的暂停原因。"""
        raw_text = str(error_text or "").strip()
        if not raw_text:
            return "大模型调用中断"
        first_line = cls._first_error_line(raw_text) or raw_text
        lowered = raw_text.lower()
        if "401 unauthorized" in lowered:
            if "dashscope" in lowered:
                return "DashScope 接口返回 401 Unauthorized，请检查 API Key、模型权限或免费额度。"
            if "open.bigmodel.cn" in lowered:
                return "GLM 接口返回 401 Unauthorized，请检查 API Key 或模型权限。"
            return "模型接口返回 401 Unauthorized，请检查 API Key 或模型权限。"
        if "404 not found" in lowered:
            if "dashscope" in lowered:
                return "DashScope 接口返回 404 Not Found，请检查模型名或兼容接口地址配置。"
            if "open.bigmodel.cn" in lowered:
                return "GLM 接口返回 404 Not Found，请检查模型名或接口地址配置。"
            return "模型接口返回 404 Not Found，请检查模型名或接口地址配置。"
        if "timeout" in lowered or "timed out" in lowered:
            return "模型接口调用超时，当前块未完成，重新提交同文件可继续。"
        if "rate limit" in lowered or "too many requests" in lowered or "429" in lowered:
            return "模型接口触发限流，当前块未完成，重新提交同文件可继续。"
        return first_line

    @staticmethod
    def _is_timeout_error(error: BaseException | str | None) -> bool:
        """判断异常是否属于超时类错误。"""
        error_text = str(error or "").strip().lower()
        if not error_text:
            return False
        timeout_markers = (
            "timeout",
            "timed out",
            "readtimeout",
            "read timeout",
            "connecttimeout",
            "connect timeout",
            "apitimeouterror",
            "request timed out",
        )
        return any(marker in error_text for marker in timeout_markers)

    @staticmethod
    def _build_paused_job_result(
        *,
        message: str,
        pause_reason: str,
        file_name: str,
        file_hash: str,
        run_id: str,
        resumed_from_chunk: Optional[int],
        prepared: dict[str, Any],
        entity_count: int,
        relation_count: int,
        processed_chars: int,
        completed_chunks: int,
    ) -> dict[str, Any]:
        """生成暂停态任务结果。

        Args:
            message: 对外展示的暂停提示。
            pause_reason: 暂停原因。
            file_name: 导入文件名。
            file_hash: 文件内容哈希。
            run_id: 持久化导入 run 标识。
            resumed_from_chunk: 本次续跑起始块序号。
            prepared: 文件解析和分块元数据。
            entity_count: 已抽取实体数量。
            relation_count: 已抽取关系数量。
            processed_chars: 已处理字符数。
            completed_chunks: 已完成块数。

        Returns:
            dict[str, Any]: 可写入 job.result 的暂停态结果。
        """
        return {
            "_job_status": "paused",
            "message": message,
            "file_name": file_name,
            "file_hash": file_hash,
            "run_id": run_id,
            "is_resumed": bool(prepared.get("is_resumed")),
            "resume_run_id": run_id if prepared.get("is_resumed") else None,
            "resumed_from_chunk": resumed_from_chunk,
            "chunk_limit": prepared.get("chunk_limit"),
            "chunk_strategy_version": prepared.get("chunk_strategy_version"),
            "parsed_text_length": prepared.get("parsed_text_length") or 0,
            "processed_text_length": processed_chars,
            "total_chunk_chars": prepared.get("total_chunk_chars") or 0,
            "chunk_count": prepared.get("chunk_count") or 0,
            "completed_chunks": completed_chunks,
            "entity_count": entity_count,
            "relation_count": relation_count,
            "parser_used": prepared.get("parser_used"),
            "ocr_engine": prepared.get("ocr_engine"),
            "parse_warning": prepared.get("parse_warning"),
            "docling_failed_pages": prepared.get("docling_failed_pages") or [],
            "pdf_fallback_used": bool(prepared.get("pdf_fallback_used")),
            "cache_used": bool(prepared.get("cache_used")),
            "cache_invalidated_reason": prepared.get("cache_invalidated_reason") or "",
            "resume_compatible": prepared.get("resume_compatible"),
            "pause_reason": pause_reason,
            "last_error": pause_reason,
        }

    async def submit_graph_extract_import_job(self, file: UploadFile) -> dict[str, Any]:  # pragma: no cover - background task orchestration
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
                active_job = await self._find_active_job_by_run_id(str(resumable_run.get("run_id") or ""))
                if active_job and resumable_run.get("status") == "running":
                    active_job["message"] = "检测到同文件导入任务仍在执行，已返回当前任务状态"
                    active_job["deduplicated"] = True
                    active_job["existing_job_id"] = active_job.get("job_id")
                    active_job["existing_run_id"] = resumable_run.get("run_id")
                    return {"ok": True, "data": active_job}
            resumed_from_chunk = None
            if resumable_run:
                resumed_from_chunk = await self._find_first_unfinished_chunk_index(str(resumable_run.get("run_id") or ""))
            return await self._start_background_job(
                job_type=self.IMPORT_JOB_TYPE,
                runner=lambda job_id: self._extract_and_import_graph_sync(file_bytes, filename, job_id),
                dedupe_active=True,
                seed={
                    "file_hash": file_hash,
                    "is_resumed": bool(resumable_run),
                    "resume_run_id": resumable_run.get("run_id") if resumable_run else None,
                    "resumed_from_chunk": resumed_from_chunk,
                    "chunk_limit": self.GRAPH_IMPORT_CHUNK_LIMIT,
                    "chunk_strategy_version": self.GRAPH_IMPORT_CHUNK_STRATEGY_VERSION,
                    "resume_compatible": bool(resumable_run) if resumable_run else None,
                    "parallelism": min(self.GRAPH_LLM_MAX_PARALLELISM, len(self._graph_llm_lane_configs)),
                    "active_lanes": min(self.GRAPH_LLM_MAX_PARALLELISM, len(self._graph_llm_lane_configs)),
                    "current_attempt": 0,
                    "max_attempts": self.GRAPH_LLM_MAX_ATTEMPTS,
                    "progress_text": self._format_resume_message(resumed_from_chunk) if resumable_run else "任务已提交，等待执行",
                },
            )
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            self._log_api_error("/admin/graph/jobs/extract-import", "extract_import_submit", exc, filename=filename)
            return {"ok": False, "error": f"提交抽取导入任务失败: {exc}"}

    async def submit_graph_embed_milvus_job(self, data: Optional[dict[str, Any]] = None) -> dict[str, Any]:  # pragma: no cover - background task orchestration
        """提交图谱嵌入 Milvus 后台任务。

        Args:
            data: 可选标签列表和批大小配置。

        Returns:
            dict[str, Any]: 任务提交结果或错误响应。
        """
        try:
            payload = data or {}
            labels_obj = payload.get("labels")
            labels = [str(item).strip() for item in labels_obj if str(item).strip()] if isinstance(labels_obj, list) else None
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
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            self._log_api_error("/admin/graph/jobs/embed-milvus", "embed_graph_submit", exc)
            return {"ok": False, "error": f"提交嵌入任务失败: {exc}"}

    async def _extract_and_import_graph_sync(self, file_bytes: bytes, filename: str, job_id: str) -> dict[str, Any]:  # pragma: no cover - external LLM/Neo4j background runtime
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
            prepared.get("chunk_strategy_version") or self.GRAPH_IMPORT_CHUNK_STRATEGY_VERSION
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
            or int(latest_run.get("chunk_strategy_version") or 0) != chunk_strategy_version
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
                incompatibility_reason = "旧导入断点与当前分块结果不一致，已终止旧断点并改为新任务导入"
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
        chunk_map = {int(item.get("chunk_index") or 0): item for item in existing_chunks}
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
                    "current_chunk_chars": len(chunks[(resumed_from_chunk or 1) - 1]) if resumed_from_chunk and resumed_from_chunk <= total_chunks else 0,
                },
            )

        total_entities = int(progress["entity_count"])
        total_relations = int(progress["relation_count"])
        total_node_batches = int(progress.get("node_batches") or 0)
        total_relation_batches = int(progress.get("relation_batches") or 0)
        processed_chars = int(progress["processed_chars"])
        completed_chunks = int(progress["completed_chunks"])
        from app.knowledge_graph.extract_graph_data_utils import create_graph_llm_bundle

        lane_configs = list(self._graph_llm_lane_configs[: self.GRAPH_LLM_MAX_PARALLELISM])
        if not lane_configs:
            raise RuntimeError("未配置可用的图谱抽取模型，请检查 MODEL_* 或 GRAPH_LLM_* 环境变量")

        lane_statuses = self._graph_extraction_lane_pool.build_lane_statuses(
            lane_configs
        )
        parallelism = len(lane_statuses)
        await self._update_job_parallel_state(
            job_id,
            parallelism=parallelism,
            active_lanes=self._graph_extraction_lane_pool.count_active_lanes(
                lane_statuses
            ),
            current_attempt=0,
            max_attempts=self.GRAPH_LLM_MAX_ATTEMPTS,
            lane_statuses=lane_statuses,
        )
        prepared.update(
            {
                "parallelism": parallelism,
                "active_lanes": self._graph_extraction_lane_pool.count_active_lanes(
                    lane_statuses
                ),
                "current_attempt": 0,
                "max_attempts": self.GRAPH_LLM_MAX_ATTEMPTS,
                "lane_statuses": self._graph_extraction_lane_pool.clone_lane_statuses(
                    lane_statuses
                ),
            }
        )
        logger.info(
            "graph_import.parallel_extract_start run_id=%s parallelism=%s lanes=%s",
            run_id,
            parallelism,
            [
                self._graph_extraction_lane_pool.format_lane_label(item)
                for item in lane_configs
            ],
        )

        pending_chunks = [
            (index, chunk)
            for index, chunk in enumerate(chunks, start=1)
            if self._normalize_chunk_status(chunk_map.get(index, {}).get("status")) != "succeeded"
        ]
        if pending_chunks:
            work_queue: asyncio.Queue[tuple[int, str]] = asyncio.Queue()
            for item in pending_chunks[:parallelism]:
                await work_queue.put(item)
            next_schedule_cursor = min(parallelism, len(pending_chunks))
            write_cursor = 0
            active_extractions = 0
            attempt_counts: dict[int, int] = {}
            result_buffer: dict[int, dict[str, Any]] = {}
            stop_event = asyncio.Event()
            state_condition = asyncio.Condition()
            terminal_error: Optional[str] = None

            lane_bundles = {
                int(item["lane_id"]): create_graph_llm_bundle(
                    api_key=str(item["api_key"]),
                    base_url=str(item["base_url"]),
                    model_name=str(item["model_name"]),
                    timeout=self.GRAPH_LLM_REQUEST_TIMEOUT_SECONDS,
                    max_retries=0,
                    lane_id=int(item["lane_id"]),
                )
                for item in lane_configs
            }

            async def sync_lane_status(
                lane_id: int,
                *,
                status: Optional[str] = None,
                current_chunk_index: Optional[int] = None,
                current_attempt: Optional[int] = None,
                last_error: Optional[str] = None,
                backoff_until: Optional[str] = None,
            ) -> None:
                """同步单个 LLM lane 的当前运行状态到任务快照。"""
                self._graph_extraction_lane_pool.update_lane_status(
                    lane_statuses,
                    lane_id,
                    status=status,
                    current_chunk_index=current_chunk_index,
                    current_attempt=current_attempt,
                    last_error=last_error,
                    backoff_until=backoff_until,
                )
                await self._update_job_parallel_state(
                    job_id,
                    parallelism=parallelism,
                    active_lanes=self._graph_extraction_lane_pool.count_active_lanes(
                        lane_statuses
                    ),
                    current_attempt=current_attempt,
                    max_attempts=self.GRAPH_LLM_MAX_ATTEMPTS,
                    lane_statuses=lane_statuses,
                )

            async def fail_chunk_and_task(chunk_index: int, error_message: str) -> None:
                """标记当前块和整个导入任务失败，并通知并发协程停止。"""
                nonlocal terminal_error
                if terminal_error:
                    return
                terminal_error = error_message
                await self._mark_graph_chunk_failed(run_id, chunk_index, error_message)
                await self._set_graph_run_status(run_id, "failed", last_error=error_message)
                async with state_condition:
                    state_condition.notify_all()
                stop_event.set()

            async def lane_worker(lane_config: dict[str, Any]) -> None:
                """从队列领取待抽取文本块，并在对应 LLM lane 上执行抽取。"""
                nonlocal active_extractions, terminal_error, write_cursor
                lane_id = int(lane_config["lane_id"])
                llm_bundle = lane_bundles[lane_id]
                lane_label = self._graph_extraction_lane_pool.format_lane_label(
                    lane_config
                )
                while not stop_event.is_set():
                    try:
                        chunk_index, chunk = await asyncio.wait_for(work_queue.get(), timeout=1.0)
                    except asyncio.TimeoutError:
                        if stop_event.is_set() or write_cursor >= len(pending_chunks):
                            break
                        continue

                    if stop_event.is_set():
                        work_queue.task_done()
                        break

                    attempt = int(attempt_counts.get(chunk_index) or 0) + 1
                    attempt_counts[chunk_index] = attempt
                    chunk_chars = len(chunk)
                    lane_stage_callback = self._graph_extraction_lane_pool.make_lane_stage_callback(
                        stage_callback,
                        lane_id=lane_id,
                        chunk_index=chunk_index,
                        total_chunks=total_chunks,
                        attempt=attempt,
                        max_attempts=self.GRAPH_LLM_MAX_ATTEMPTS,
                    )

                    await self._mark_graph_chunk_running(run_id, chunk_index)
                    await sync_lane_status(
                        lane_id,
                        status="running",
                        current_chunk_index=chunk_index,
                        current_attempt=attempt,
                        last_error="",
                        backoff_until=None,
                    )
                    await self._heartbeat_job_stage(
                        job_id,
                        detail=(
                            f"正在并发抽取第 {chunk_index}/{total_chunks} 段文本，"
                            f"{lane_label} 正在执行第 {attempt}/{self.GRAPH_LLM_MAX_ATTEMPTS} 次尝试"
                        ),
                        progress_text=(
                            f"并发抽取中：第 {chunk_index}/{total_chunks} 段文本，"
                            f"{lane_label} 第 {attempt}/{self.GRAPH_LLM_MAX_ATTEMPTS} 次尝试"
                        ),
                        metrics={
                            "total_chars": prepared.get("parsed_text_length") or 0,
                            "processed_chars": processed_chars,
                            "total_chunks": total_chunks,
                            "completed_chunks": completed_chunks,
                            "current_chunk_index": chunk_index,
                            "current_chunk_chars": chunk_chars,
                        },
                    )

                    async with state_condition:
                        active_extractions += 1

                    try:
                        chunk_result = await asyncio.to_thread(
                            self._graph_extraction_lane_pool.extract_single_chunk_blocking,
                            chunk,
                            chunk_index,
                            total_chunks,
                            prepared.get("parsed_text_length") or 0,
                            processed_chars,
                            lane_stage_callback,
                            llm_bundle,
                        )
                    except Exception as exc:
                        error_text = str(exc).strip() or exc.__class__.__name__
                        failure_text = (
                            f"第 {chunk_index}/{total_chunks} 块在 {lane_label} 上第 "
                            f"{attempt}/{self.GRAPH_LLM_MAX_ATTEMPTS} 次尝试失败: {error_text}"
                        )
                        await self._mark_graph_chunk_failed(run_id, chunk_index, failure_text)
                        error_type = (
                            self._graph_extraction_lane_pool.classify_lane_error(exc)
                        )
                        first_error = self._first_error_line(error_text) or error_text

                        async with state_condition:
                            active_extractions = max(0, active_extractions - 1)
                            state_condition.notify_all()

                        if attempt >= self.GRAPH_LLM_MAX_ATTEMPTS:
                            await fail_chunk_and_task(
                                chunk_index,
                                (
                                    f"第 {chunk_index}/{total_chunks} 块累计失败 "
                                    f"{self.GRAPH_LLM_MAX_ATTEMPTS} 次，任务失败。最近错误: {first_error}"
                                ),
                            )
                            work_queue.task_done()
                            break

                        await work_queue.put((chunk_index, chunk))
                        await self._add_job_warning(
                            job_id,
                            (
                                f"{lane_label} 处理第 {chunk_index}/{total_chunks} 块失败，"
                                f"已回队重试（{attempt}/{self.GRAPH_LLM_MAX_ATTEMPTS}）：{first_error}"
                            ),
                        )

                        if error_type == "permanent":
                            await sync_lane_status(
                                lane_id,
                                status="disabled",
                                current_chunk_index=chunk_index,
                                current_attempt=attempt,
                                last_error=first_error,
                                backoff_until=None,
                            )
                            await self._add_job_warning(job_id, f"{lane_label} 因鉴权或模型配置错误已摘除")
                            if (
                                self._graph_extraction_lane_pool.count_viable_lanes(
                                    lane_statuses
                                )
                                <= 0
                            ):
                                await fail_chunk_and_task(
                                    chunk_index,
                                    "所有图谱抽取模型 lane 均不可用，任务失败",
                                )
                                work_queue.task_done()
                                break
                            work_queue.task_done()
                            break

                        backoff_until = (
                            datetime.utcnow() + timedelta(seconds=self.GRAPH_LLM_TRANSIENT_BACKOFF_SECONDS)
                        ).isoformat(timespec="seconds") + "Z"
                        await sync_lane_status(
                            lane_id,
                            status="backoff",
                            current_chunk_index=chunk_index,
                            current_attempt=attempt,
                            last_error=first_error,
                            backoff_until=backoff_until,
                        )
                        work_queue.task_done()
                        if not stop_event.is_set():
                            await asyncio.sleep(self.GRAPH_LLM_TRANSIENT_BACKOFF_SECONDS)
                            if not stop_event.is_set():
                                await sync_lane_status(
                                    lane_id,
                                    status="ready",
                                    current_chunk_index=None,
                                    current_attempt=attempt,
                                    last_error="",
                                    backoff_until=None,
                                )
                        continue

                    async with state_condition:
                        active_extractions = max(0, active_extractions - 1)
                        result_buffer[chunk_index] = {
                            **chunk_result,
                            "attempt": attempt,
                            "lane_id": lane_id,
                            "lane_label": lane_label,
                        }
                        state_condition.notify_all()
                    await sync_lane_status(
                        lane_id,
                        status="ready",
                        current_chunk_index=None,
                        current_attempt=attempt,
                        last_error="",
                        backoff_until=None,
                    )
                    work_queue.task_done()

            async def writer_loop() -> None:
                """按块序等待抽取结果并串行写入 Neo4j，保证续跑进度有序推进。"""
                nonlocal next_schedule_cursor, write_cursor, processed_chars, completed_chunks
                nonlocal total_entities, total_relations, total_node_batches, total_relation_batches, terminal_error
                while write_cursor < len(pending_chunks):
                    index, chunk = pending_chunks[write_cursor]
                    chunk_chars = len(chunk)
                    async with state_condition:
                        await state_condition.wait_for(
                            lambda: terminal_error is not None or index in result_buffer
                        )
                        if terminal_error:
                            raise RuntimeError(terminal_error)
                        chunk_result = result_buffer.pop(index)

                    attempt = int(chunk_result.get("attempt") or 0)

                    await self._update_job_parallel_state(
                        job_id,
                        parallelism=parallelism,
                        active_lanes=self._graph_extraction_lane_pool.count_active_lanes(
                            lane_statuses
                        ),
                        current_attempt=attempt,
                        max_attempts=self.GRAPH_LLM_MAX_ATTEMPTS,
                        lane_statuses=lane_statuses,
                    )
                    try:
                        write_result = await self._graph_import_writer.write_chunk(
                            run_id=run_id,
                            job_id=job_id,
                            chunk_index=index,
                            total_chunks=total_chunks,
                            chunk_chars=chunk_chars,
                            chunk_result=chunk_result,
                            total_chars=prepared.get("parsed_text_length") or 0,
                            processed_chars=processed_chars,
                            completed_chunks=completed_chunks,
                        )
                    except RuntimeError as exc:
                        terminal_error = str(exc)
                        stop_event.set()
                        raise

                    processed_chars = write_result["processed_chars"]
                    completed_chunks = write_result["completed_chunks"]
                    total_entities = write_result["entity_count"]
                    total_relations = write_result["relation_count"]
                    total_node_batches += write_result["node_batches"]
                    total_relation_batches += write_result["relation_batches"]

                    write_cursor += 1
                    if next_schedule_cursor < len(pending_chunks):
                        await work_queue.put(pending_chunks[next_schedule_cursor])
                        next_schedule_cursor += 1

            worker_tasks = [
                asyncio.create_task(lane_worker(item), name=f"graph-lane-{int(item['lane_id'])}")
                for item in lane_configs
            ]
            try:
                await writer_loop()
            finally:
                stop_event.set()
                for task in worker_tasks:
                    task.cancel()
                if worker_tasks:
                    await asyncio.gather(*worker_tasks, return_exceptions=True)

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
                "parallelism": parallelism,
                "active_lanes": self._graph_extraction_lane_pool.count_active_lanes(
                    lane_statuses
                ),
                "current_attempt": int(max(attempt_counts.values()) if pending_chunks and attempt_counts else 0),
                "max_attempts": self.GRAPH_LLM_MAX_ATTEMPTS,
                "lane_statuses": self._graph_extraction_lane_pool.clone_lane_statuses(
                    lane_statuses
                ),
                "processed_text_length": processed_chars,
                "entity_count": total_entities,
                "relation_count": total_relations,
                "node_batches": total_node_batches,
                "relation_batches": total_relation_batches,
                "message": f"抽取并导入完成，实体 {total_entities} 个，关系 {total_relations} 条",
            }
        )
        return prepared

    async def _embed_graph_to_milvus_sync(self, data: Optional[dict[str, Any]], job_id: str) -> dict[str, Any]:  # pragma: no cover - external Milvus runtime
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
                    self._heartbeat_job_stage(job_id, detail=str(text or ""), progress_text=str(text or "")),
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
        await self._enter_job_stage(job_id, "finalize", "Milvus 嵌入已完成，正在整理结果")
        await self._complete_job_stage(job_id, detail="任务执行完成")
        return {
            "message": f"知识图谱嵌入成功，共 {stats['total_documents']} 条记录",
            "stats": stats,
            "timestamp": stats.get("timestamp"),
        }
