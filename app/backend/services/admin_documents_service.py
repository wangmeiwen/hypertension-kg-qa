"""管理员文档服务。"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import traceback
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Optional
from uuid import uuid4

from fastapi import UploadFile

from app.backend.domain.job_status import BackgroundJobStatus
from app.backend.observability.session_logger import get_request_id
from app.backend.security.upload_policy import inspect_upload
from app.core.concurrency import concurrency_guard
from app.core.config import Config

logger = logging.getLogger(__name__)

DOCUMENT_UPLOAD_SUFFIXES = {
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
DOCUMENT_UPLOAD_MAX_BYTES = 50 * 1024 * 1024


class AdminDocumentsService:
    """封装管理员文本嵌入业务。"""

    JOB_STATUS_QUEUED = BackgroundJobStatus.QUEUED.value
    JOB_STATUS_RUNNING = BackgroundJobStatus.RUNNING.value
    JOB_STATUS_SUCCEEDED = BackgroundJobStatus.SUCCEEDED.value
    JOB_STATUS_FAILED = BackgroundJobStatus.FAILED.value
    JOB_STATUS_CANCELLED = BackgroundJobStatus.CANCELLED.value

    TEXT_EMBED_JOB_TYPE = "text_vector_embed"
    SOFT_TIMEOUT_SECONDS = 300
    SOFT_TIMEOUT_REPEAT_SECONDS = 60

    def __init__(
        self,
        await_call: Callable[..., Awaitable[Any]],
        get_shared_vector_store: Callable[[], Any],
        log_api_error: Callable[..., None],
    ) -> None:
        """初始化实例并注入管理员文档任务运行依赖。

        Args:
            await_call: 兼容同步/异步函数的调用适配器。
            get_shared_vector_store: 获取共享向量库实例的回调。
            log_api_error: 统一记录管理员接口错误的回调。

        Returns:
            None: 无返回值。
        """
        self._await_call = await_call
        self._get_shared_vector_store = get_shared_vector_store
        self._log_api_error = log_api_error
        self._config = Config()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._job_lock = asyncio.Lock()
        self._max_retained_jobs = 100

    @staticmethod
    def _utc_now_iso() -> str:
        """返回统一的 UTC ISO 时间文本。"""
        return datetime.utcnow().isoformat(timespec="seconds") + "Z"

    @staticmethod
    def _parse_utc_iso(value: Optional[str]) -> Optional[datetime]:
        """解析 UTC ISO 时间文本。"""
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None

    @staticmethod
    def _build_job_response(job: dict[str, Any]) -> dict[str, Any]:
        """裁剪对外 job 响应。"""
        return {
            "job_id": job["job_id"],
            "trace_id": job["trace_id"],
            "job_type": job["job_type"],
            "status": job["status"],
            "progress_text": job.get("progress_text") or "",
            "created_at": job.get("created_at"),
            "started_at": job.get("started_at"),
            "finished_at": job.get("finished_at"),
            "duration_ms": job.get("duration_ms"),
            "stage": job.get("stage"),
            "stage_detail": job.get("stage_detail") or "",
            "stage_started_at": job.get("stage_started_at"),
            "last_heartbeat_at": job.get("last_heartbeat_at"),
            "warnings": list(job.get("warnings") or []),
            "trace": list(job.get("trace") or []),
            "result": job.get("result"),
            "error": job.get("error"),
        }

    async def _register_job(self, job_type: str) -> dict[str, Any]:
        """注册一个新的内存后台任务并执行保留数量裁剪。

        Args:
            job_type: 后台任务类型。

        Returns:
            dict[str, Any]: 新建任务的快照数据。
        """
        async with self._job_lock:
            job_id = f"text-embed-job-{uuid4().hex}"
            job: dict[str, Any] = {
                "job_id": job_id,
                "trace_id": get_request_id(),
                "job_type": job_type,
                "status": self.JOB_STATUS_QUEUED,
                "progress_text": "任务已提交，等待执行",
                "created_at": self._utc_now_iso(),
                "started_at": None,
                "finished_at": None,
                "duration_ms": None,
                "stage": None,
                "stage_detail": "",
                "stage_started_at": None,
                "last_heartbeat_at": None,
                "warnings": [],
                "trace": [],
                "result": None,
                "error": None,
                "_task": None,
                "_watchdog_task": None,
                "_last_soft_timeout_notice_at": None,
            }
            self._jobs[job_id] = job
            self._prune_jobs_locked()
            return dict(job)

    def _prune_jobs_locked(self) -> None:
        """在持有任务锁时裁剪已结束的旧任务，限制内存保留数量。"""
        if len(self._jobs) <= self._max_retained_jobs:
            return
        removable = sorted(
            self._jobs.values(), key=lambda item: item.get("created_at") or ""
        )
        while removable and len(self._jobs) > self._max_retained_jobs:
            candidate = removable.pop(0)
            if candidate.get("status") in {
                self.JOB_STATUS_SUCCEEDED,
                self.JOB_STATUS_FAILED,
                self.JOB_STATUS_CANCELLED,
            }:
                self._jobs.pop(candidate["job_id"], None)

    def _mark_current_trace_finished(
        self, job: dict[str, Any], status: str, error: Optional[str] = None
    ) -> None:
        """把任务当前运行中的 trace 阶段标记为结束。

        Args:
            job: 内存任务对象。
            status: 阶段结束状态。
            error: 可选错误文本，失败时写入 trace。

        Returns:
            None: 无返回值。
        """
        trace = job.get("trace") or []
        for item in reversed(trace):
            if not item.get("finished_at"):
                finished_at = self._utc_now_iso()
                item["finished_at"] = finished_at
                item["status"] = status
                item["error"] = error
                started_at = self._parse_utc_iso(item.get("started_at"))
                finished_dt = self._parse_utc_iso(finished_at)
                if started_at and finished_dt:
                    item["duration_ms"] = int(
                        (finished_dt - started_at).total_seconds() * 1000
                    )
                break

    async def _set_job_state(
        self,
        job_id: str,
        *,
        status: Optional[str] = None,
        progress_text: Optional[str] = None,
        result: Optional[dict[str, Any]] = None,
        error: Optional[str] = None,
        started: bool = False,
        finished: bool = False,
    ) -> None:
        """更新内存任务状态、结果、错误和生命周期时间。

        Args:
            job_id: 任务标识。
            status: 可选任务状态。
            progress_text: 可选进度文案。
            result: 可选任务结果。
            error: 可选错误文本。
            started: 是否写入开始时间。
            finished: 是否写入结束时间。

        Returns:
            None: 无返回值。
        """
        async with self._job_lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            if status:
                job["status"] = status
            if progress_text is not None:
                job["progress_text"] = progress_text
            if result is not None:
                job["result"] = result
            if error is not None:
                job["error"] = error
                self._mark_current_trace_finished(
                    job, status or job.get("status") or self.JOB_STATUS_FAILED, error
                )
            if started and not job.get("started_at"):
                job["started_at"] = self._utc_now_iso()
            if finished:
                finished_at = self._utc_now_iso()
                job["finished_at"] = finished_at
                started_at = self._parse_utc_iso(
                    job["started_at"] if job["started_at"] else job["created_at"]
                )
                finished_dt = self._parse_utc_iso(finished_at)
                if started_at is not None and finished_dt is not None:
                    job["duration_ms"] = int(
                        (finished_dt - started_at).total_seconds() * 1000
                    )

    async def _enter_job_stage(self, job_id: str, stage: str, detail: str = "") -> None:
        """进入任务新阶段并追加 trace 记录。

        Args:
            job_id: 任务标识。
            stage: 阶段名称。
            detail: 阶段说明文本。

        Returns:
            None: 无返回值。
        """
        now_iso = self._utc_now_iso()
        async with self._job_lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            if job.get("stage") and job.get("stage") != stage:
                self._mark_current_trace_finished(job, "succeeded")
            job["stage"] = stage
            job["stage_detail"] = detail or ""
            job["stage_started_at"] = now_iso
            job["last_heartbeat_at"] = now_iso
            job["_last_soft_timeout_notice_at"] = None
            job["progress_text"] = detail or stage
            job.setdefault("trace", []).append(
                {
                    "trace_id": job["trace_id"],
                    "stage": stage,
                    "detail": detail or "",
                    "status": "running",
                    "started_at": now_iso,
                    "finished_at": None,
                    "duration_ms": None,
                    "error": None,
                }
            )

    async def _heartbeat_job_stage(
        self,
        job_id: str,
        *,
        detail: Optional[str] = None,
        progress_text: Optional[str] = None,
    ) -> None:
        """刷新当前阶段心跳、进度文案和 trace 明细。

        Args:
            job_id: 任务标识。
            detail: 可选阶段明细。
            progress_text: 可选对外进度文案。

        Returns:
            None: 无返回值。
        """
        now_iso = self._utc_now_iso()
        async with self._job_lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job["last_heartbeat_at"] = now_iso
            if detail is not None:
                job["stage_detail"] = detail
            if progress_text is not None:
                job["progress_text"] = progress_text
            elif detail is not None:
                job["progress_text"] = detail
            for item in reversed(job.get("trace") or []):
                if not item.get("finished_at"):
                    if detail is not None:
                        item["detail"] = detail
                    break

    async def _complete_job_stage(
        self, job_id: str, detail: Optional[str] = None
    ) -> None:
        """完成任务当前阶段并收口 trace。

        Args:
            job_id: 任务标识。
            detail: 可选完成明细。

        Returns:
            None: 无返回值。
        """
        now_iso = self._utc_now_iso()
        async with self._job_lock:
            job = self._jobs.get(job_id)
            if not job or not job.get("stage"):
                return
            job["last_heartbeat_at"] = now_iso
            if detail is not None:
                job["stage_detail"] = detail
                job["progress_text"] = detail
                for item in reversed(job.get("trace") or []):
                    if not item.get("finished_at"):
                        item["detail"] = detail
                        break
            self._mark_current_trace_finished(job, "succeeded")

    async def _add_job_warning(
        self,
        job_id: str,
        message: str,
        *,
        elapsed_seconds: Optional[int] = None,
    ) -> None:
        """为任务追加可展示的 warning 信息。

        Args:
            job_id: 任务标识。
            message: warning 文本。
            elapsed_seconds: 可选阶段已耗时秒数。

        Returns:
            None: 无返回值。
        """
        warning = {
            "message": message,
            "stage": None,
            "timestamp": self._utc_now_iso(),
            "elapsed_seconds": elapsed_seconds,
        }
        async with self._job_lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            warning["stage"] = job.get("stage")
            job.setdefault("warnings", []).append(warning)

    async def _watch_job_soft_timeout(
        self, job_id: str
    ) -> None:  # pragma: no cover - wall-clock watchdog
        """监控单阶段运行时长，超过软超时后周期性追加 warning。"""
        try:
            while True:
                await asyncio.sleep(5)
                async with self._job_lock:
                    job = self._jobs.get(job_id)
                    if not job or job.get("status") != self.JOB_STATUS_RUNNING:
                        return
                    stage = job.get("stage")
                    stage_started_at = self._parse_utc_iso(job.get("stage_started_at"))
                    if not stage or stage_started_at is None:
                        continue
                    now_dt = datetime.now(stage_started_at.tzinfo)
                    elapsed_seconds = int((now_dt - stage_started_at).total_seconds())
                    if elapsed_seconds < self.SOFT_TIMEOUT_SECONDS:
                        continue
                    last_notice_at = self._parse_utc_iso(
                        job.get("_last_soft_timeout_notice_at")
                    )
                    if last_notice_at and (now_dt - last_notice_at) < timedelta(
                        seconds=self.SOFT_TIMEOUT_REPEAT_SECONDS
                    ):
                        continue
                    message = f"当前任务已在阶段 {stage} 持续 {elapsed_seconds} 秒"
                    job["_last_soft_timeout_notice_at"] = self._utc_now_iso()
                    job.setdefault("warnings", []).append(
                        {
                            "message": message,
                            "stage": stage,
                            "timestamp": job["_last_soft_timeout_notice_at"],
                            "elapsed_seconds": elapsed_seconds,
                        }
                    )
                    logger.warning(message)
        except asyncio.CancelledError:
            raise

    def _make_stage_event_callback(
        self, loop: asyncio.AbstractEventLoop, job_id: str
    ) -> Callable[..., None]:  # pragma: no cover - background thread callback
        """生成线程安全的任务阶段事件回调，供阻塞嵌入线程回传进度。"""

        def _progress(
            action: str,
            stage: Optional[str] = None,
            detail: Optional[str] = None,
            progress_text: Optional[str] = None,
        ) -> None:
            try:
                asyncio.run_coroutine_threadsafe(
                    self._handle_job_stage_event(
                        job_id,
                        action=action,
                        stage=stage,
                        detail=detail,
                        progress_text=progress_text,
                    ),
                    loop,
                )
            except RuntimeError:
                return

        return _progress

    async def _handle_job_stage_event(  # pragma: no cover - background thread callback
        self,
        job_id: str,
        *,
        action: str,
        stage: Optional[str] = None,
        detail: Optional[str] = None,
        progress_text: Optional[str] = None,
    ) -> None:
        """接收阻塞线程回传的阶段事件并映射为任务状态更新。

        Args:
            job_id: 任务标识。
            action: 阶段事件动作，支持 enter、heartbeat、complete、warning。
            stage: 可选阶段名称。
            detail: 可选阶段明细。
            progress_text: 可选对外进度文案。

        Returns:
            None: 无返回值。
        """
        if action == "enter" and stage:
            await self._enter_job_stage(job_id, stage, detail or progress_text or "")
            return
        if action == "heartbeat":
            await self._heartbeat_job_stage(
                job_id, detail=detail or progress_text, progress_text=progress_text
            )
            return
        if action == "complete":
            await self._complete_job_stage(job_id, detail=detail or progress_text)
            return
        if action == "warning":
            message = detail or progress_text or ""
            if message:
                await self._add_job_warning(job_id, message)

    async def _start_background_job(  # pragma: no cover - background task orchestration
        self,
        *,
        runner: Callable[[str], Awaitable[dict[str, Any]]],
    ) -> dict[str, Any]:
        """创建后台任务、启动 watchdog，并立即返回任务提交结果。

        Args:
            runner: 接收 job_id 并执行实际后台工作的异步函数。

        Returns:
            dict[str, Any]: 任务提交响应，包含对外 job 快照。
        """
        job = await self._register_job(self.TEXT_EMBED_JOB_TYPE)
        job_id = job["job_id"]

        async def _run() -> None:
            await self._set_job_state(
                job_id,
                status=self.JOB_STATUS_RUNNING,
                progress_text="任务开始执行",
                started=True,
            )
            watchdog_task = asyncio.create_task(
                self._watch_job_soft_timeout(job_id), name=f"watchdog:{job_id}"
            )
            async with self._job_lock:
                if job_id in self._jobs:
                    self._jobs[job_id]["_watchdog_task"] = watchdog_task
            try:
                result = await runner(job_id)
                await self._set_job_state(
                    job_id,
                    status=self.JOB_STATUS_SUCCEEDED,
                    progress_text="任务执行完成",
                    result=result,
                    finished=True,
                )
            except asyncio.CancelledError:
                await self._set_job_state(
                    job_id,
                    status=self.JOB_STATUS_CANCELLED,
                    progress_text="任务已取消",
                    error="任务已取消",
                    finished=True,
                )
                raise
            except Exception as exc:
                await self._set_job_state(
                    job_id,
                    status=self.JOB_STATUS_FAILED,
                    progress_text="任务执行失败",
                    error=f"{exc}\n{traceback.format_exc()}",
                    finished=True,
                )
            finally:
                watchdog = None
                async with self._job_lock:
                    if job_id in self._jobs:
                        watchdog = self._jobs[job_id].get("_watchdog_task")
                        self._jobs[job_id]["_watchdog_task"] = None
                if watchdog:
                    watchdog.cancel()
                    try:
                        await watchdog
                    except asyncio.CancelledError:
                        pass

        task = asyncio.create_task(_run(), name=f"{self.TEXT_EMBED_JOB_TYPE}:{job_id}")
        async with self._job_lock:
            if job_id in self._jobs:
                self._jobs[job_id]["_task"] = task

        response_data = self._build_job_response(job)
        response_data["message"] = "任务已提交到后台处理"
        return {"ok": True, "data": response_data}

    def _validate_file(
        self,
        file_bytes: bytes,
        filename: str,
        declared_mime: str | None = None,
    ) -> str:
        """校验上传文件基础约束并返回扩展名。

        Args:
            file_bytes: 上传文件二进制内容。
            filename: 上传文件名。

        Returns:
            str: 小写文件扩展名。

        Raises:
            RuntimeError: 文件名、内容、大小或格式不满足要求。
        """
        try:
            inspection = inspect_upload(
                content=file_bytes,
                filename=filename,
                declared_mime=declared_mime,
                allowed_suffixes=DOCUMENT_UPLOAD_SUFFIXES,
                max_bytes=DOCUMENT_UPLOAD_MAX_BYTES,
            )
        except ValueError as exc:
            raise RuntimeError(str(exc)) from exc
        return inspection.suffix

    def _embed_text_document_blocking(  # pragma: no cover - external embedding runtime
        self,
        file_bytes: bytes,
        filename: str,
        stage_callback: Callable[..., None],
    ) -> dict[str, Any]:
        """在线程中执行文档解析、分块和向量嵌入。

        Args:
            file_bytes: 上传文件二进制内容。
            filename: 上传文件名。
            stage_callback: 线程安全阶段回调，用于向后台 job 汇报进度。

        Returns:
            dict[str, Any]: 文档解析、分块和向量写入结果摘要。
        """
        from app.core.config import Config
        from app.document_parser.docling_parser import DoclingParser
        from app.document_parser.document_segmentation import DocumentSegmentation
        from app.rag_core.document_embedding import DocumentEmbedder

        stage_callback("enter", "validate_file", "正在校验文件")
        self._validate_file(file_bytes, filename)
        stage_callback("complete", "validate_file", "文件校验完成")

        parser = DoclingParser(
            enable_image_ocr=True,
            strict_image_ocr=True,
            progress_callback=stage_callback,
        )
        parsed = parser.parse_bytes(file_bytes, filename)
        metadata = parsed.metadata if isinstance(parsed.metadata, dict) else {}
        parser_used = (
            "docling_with_pymupdf_fallback"
            if metadata.get("pdf_fallback_used")
            else "docling"
        )
        parse_warning = str(metadata.get("pdf_fallback_reason") or "")
        docling_failed_pages = list(metadata.get("docling_failed_pages") or [])

        conf = Config()
        stage_callback("enter", "segment_text", "正在切分文本块")
        chunker = DocumentSegmentation(
            parent_chunk_size=conf.PARENT_CHUNK_SIZE,
            sub_chunk_size=conf.SUB_CHUNK_SIZE,
            parent_chunk_overlap=conf.PARENT_CHUNK_OVERLAP,
            sub_chunk_overlap=conf.SUB_CHUNK_OVERLAP,
        )
        chunk_source_text = (parsed.text or "").strip() or (
            parsed.markdown or ""
        ).strip()
        if not chunk_source_text:
            raise ValueError("Docling 解析结果为空，无法分块并嵌入")

        hierarchical_chunks = chunker.segment_text_with_page_count(
            chunk_source_text,
            filename,
            len(parsed.pages),
        )
        sub_chunks = chunker.get_all_sub_chunks(hierarchical_chunks)
        if not sub_chunks:
            raise ValueError("分块结果为空，未执行 Milvus 写入")
        stage_callback(
            "complete",
            "segment_text",
            f"文本切分完成，父块 {len(hierarchical_chunks)} 个，子块 {len(sub_chunks)} 个",
        )

        stage_callback("enter", "embed_vector_store", "正在写入文本向量数据库")
        vector_store = self._get_shared_vector_store()
        embedder = DocumentEmbedder(vector_store=vector_store)
        embed_result = embedder.embed_docling_sub_chunks(
            sub_chunks,
            filename,
            parsed,
            source_hash=hashlib.sha256(file_bytes).hexdigest(),
        )
        collection_stats = embedder.get_collection_stats()
        stage_callback("complete", "embed_vector_store", "文本向量数据库写入完成")

        return {
            "file_name": filename,
            "format": embed_result.get("format"),
            "pages": embed_result.get("pages", 0),
            "tables": embed_result.get("tables", 0),
            "images": embed_result.get("images", 0),
            "parser_used": parser_used,
            "parse_warning": parse_warning,
            "docling_failed_pages": docling_failed_pages,
            "pdf_fallback_used": bool(metadata.get("pdf_fallback_used")),
            "parent_chunks_count": len(hierarchical_chunks),
            "sub_chunks_count": embed_result.get("sub_chunks_count", 0),
            "milvus_row_count": (
                collection_stats.get("row_count", 0)
                if collection_stats.get("success")
                else None
            ),
            "message": embed_result.get("message", "文本嵌入完成"),
        }

    async def _embed_text_document_job(
        self, file_bytes: bytes, filename: str, job_id: str
    ) -> dict[str, Any]:  # pragma: no cover - external embedding runtime
        """执行文本嵌入后台任务，并通过 keyed lock 串行化向量库写入。

        Args:
            file_bytes: 上传文件二进制内容。
            filename: 上传文件名。
            job_id: 后台任务标识。

        Returns:
            dict[str, Any]: 文档嵌入结果摘要。
        """
        loop = asyncio.get_running_loop()
        stage_callback = self._make_stage_event_callback(loop, job_id)
        await self._enter_job_stage(
            job_id, "waiting_for_lock", "等待文本向量数据库写入锁"
        )
        async with concurrency_guard.keyed_lock(
            f"rag-milvus:write:{self._config.MILVUS_COLLECTION_NAME}",
            timeout=max(1, int(self._config.TEXT_EMBED_MAX_CONCURRENCY or 1)) * 300,
        ):
            result = await asyncio.to_thread(
                self._embed_text_document_blocking,
                file_bytes,
                filename,
                stage_callback,
            )
        await self._enter_job_stage(job_id, "finalize", "文本嵌入完成，正在整理结果")
        await self._complete_job_stage(job_id, detail="任务执行完成")
        return result

    async def submit_text_embedding_job(self, file: UploadFile) -> dict[str, Any]:
        """读取上传文件并提交文本嵌入后台任务。

        Args:
            file: FastAPI 上传文件对象。

        Returns:
            dict[str, Any]: 任务提交结果或错误响应。
        """
        filename = file.filename or "uploaded_file"
        try:
            file_bytes = await file.read()
            self._validate_file(file_bytes, filename, file.content_type)
            return await self._start_background_job(
                runner=lambda job_id: self._embed_text_document_job(
                    file_bytes, filename, job_id
                ),
            )
        except Exception as exc:
            self._log_api_error(
                "/admin/text-embedding/jobs/embed",
                "text_embedding_submit",
                exc,
                filename=filename,
            )
            return {"ok": False, "error": f"提交文本嵌入任务失败: {exc}"}

    async def get_text_embedding_job(self, job_id: str) -> dict[str, Any]:
        """获取指定文本嵌入后台任务状态。

        Args:
            job_id: 任务标识。

        Returns:
            dict[str, Any]: 任务状态响应，任务不存在时返回错误。
        """
        async with self._job_lock:
            job = self._jobs.get(job_id)
            if not job:
                return {"ok": False, "error": "任务不存在或已过期"}
            return {"ok": True, "data": self._build_job_response(job)}

    async def list_text_embedding_jobs(self, limit: int = 10) -> dict[str, Any]:
        """返回最近文本嵌入任务列表。

        Args:
            limit: 返回数量上限，服务内会裁剪到允许范围。

        Returns:
            dict[str, Any]: 最近任务快照列表。
        """
        safe_limit = max(1, min(int(limit or 10), 50))
        async with self._job_lock:
            jobs = sorted(
                (self._build_job_response(job) for job in self._jobs.values()),
                key=lambda item: item.get("created_at") or "",
                reverse=True,
            )[:safe_limit]
        return {"ok": True, "data": jobs}

    async def upload_document(self, file: UploadFile) -> dict[str, Any]:
        """兼容旧 DOCX 上传嵌入接口，内部复用文本嵌入任务提交逻辑。"""
        return await self.submit_text_embedding_job(file)

    async def get_document_stats(self) -> dict[str, Any]:
        """获取文档向量库统计信息。

        Returns:
            dict[str, Any]: 向量库统计结果或错误响应。
        """
        try:
            from app.rag_core.document_embedding import DocumentEmbedder

            vector_store = await self._await_call(self._get_shared_vector_store)
            embedder = DocumentEmbedder(vector_store=vector_store)
            stats = await self._await_call(embedder.get_collection_stats)
            return stats
        except Exception as exc:
            self._log_api_error("/admin/documents/stats", "get_collection_stats", exc)
            return {"ok": False, "error": f"获取统计信息失败: {str(exc)}"}
