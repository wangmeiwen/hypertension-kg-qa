"""管理员图谱服务。"""

from __future__ import annotations

import asyncio
import logging
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional
from uuid import uuid4

from fastapi import UploadFile

logger = logging.getLogger(__name__)


class GraphAdminService:
    """封装管理员图谱管理与导入嵌入业务。"""

    JOB_STATUS_QUEUED = "queued"
    JOB_STATUS_RUNNING = "running"
    JOB_STATUS_SUCCEEDED = "succeeded"
    JOB_STATUS_FAILED = "failed"
    JOB_STATUS_CANCELLED = "cancelled"

    IMPORT_JOB_TYPE = "graph_extract_import"
    EMBED_JOB_TYPE = "graph_embed_milvus"
    SOFT_TIMEOUT_SECONDS = 300
    SOFT_TIMEOUT_REPEAT_SECONDS = 60

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
            await_call: 函数输入参数。
            neo4j_client: 函数输入参数。
            get_shared_kg_milvus_store: 函数输入参数。
            validate_cypher_identifier: 函数输入参数。
            sanitize_graph_properties: 函数输入参数。
            log_api_error: 函数输入参数。
        
        Returns:
            None: 无返回值。
        """
        self._await_call = await_call
        self._neo4j_client = neo4j_client
        self._get_shared_kg_milvus_store = get_shared_kg_milvus_store
        self._validate_cypher_identifier = validate_cypher_identifier
        self._sanitize_graph_properties = sanitize_graph_properties
        self._log_api_error = log_api_error
        self._jobs: dict[str, dict[str, Any]] = {}
        self._job_lock = asyncio.Lock()
        self._max_retained_jobs = 100

    @staticmethod
    def _utc_now_iso() -> str:
        """返回统一的 UTC ISO 时间文本。"""
        return datetime.utcnow().isoformat(timespec="seconds") + "Z"

    @staticmethod
    def _build_job_response(job: dict[str, Any]) -> dict[str, Any]:
        """裁剪对外 job 响应。"""
        return {
            "job_id": job["job_id"],
            "job_type": job["job_type"],
            "status": job["status"],
            "progress_text": job.get("progress_text") or "",
            "created_at": job.get("created_at"),
            "started_at": job.get("started_at"),
            "finished_at": job.get("finished_at"),
            "stage": job.get("stage"),
            "stage_detail": job.get("stage_detail") or "",
            "stage_started_at": job.get("stage_started_at"),
            "last_heartbeat_at": job.get("last_heartbeat_at"),
            "total_chars": job.get("total_chars"),
            "processed_chars": job.get("processed_chars"),
            "total_chunks": job.get("total_chunks"),
            "completed_chunks": job.get("completed_chunks"),
            "current_chunk_index": job.get("current_chunk_index"),
            "current_chunk_chars": job.get("current_chunk_chars"),
            "warnings": list(job.get("warnings") or []),
            "trace": list(job.get("trace") or []),
            "result": job.get("result"),
            "error": job.get("error"),
        }

    @staticmethod
    def _parse_utc_iso(value: Optional[str]) -> Optional[datetime]:
        """将统一的 UTC ISO 时间文本解析为 datetime。"""
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None

    async def _register_job(self, job_type: str) -> dict[str, Any]:
        """注册后台任务。"""
        async with self._job_lock:
            job_id = f"graph-job-{uuid4().hex}"
            job = {
                "job_id": job_id,
                "job_type": job_type,
                "status": self.JOB_STATUS_QUEUED,
                "progress_text": "任务已提交，等待执行",
                "created_at": self._utc_now_iso(),
                "started_at": None,
                "finished_at": None,
                "stage": None,
                "stage_detail": "",
                "stage_started_at": None,
                "last_heartbeat_at": None,
                "total_chars": 0,
                "processed_chars": 0,
                "total_chunks": 0,
                "completed_chunks": 0,
                "current_chunk_index": 0,
                "current_chunk_chars": 0,
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
        """裁剪旧任务，避免进程内状态无限增长。"""
        if len(self._jobs) <= self._max_retained_jobs:
            return
        removable = sorted(
            self._jobs.values(),
            key=lambda item: item.get("created_at") or "",
        )
        while len(removable) > 0 and len(self._jobs) > self._max_retained_jobs:
            candidate = removable.pop(0)
            if candidate.get("status") in {
                self.JOB_STATUS_SUCCEEDED,
                self.JOB_STATUS_FAILED,
                self.JOB_STATUS_CANCELLED,
            }:
                self._jobs.pop(candidate["job_id"], None)

    def _make_stage_event_callback(self, loop: asyncio.AbstractEventLoop, job_id: str) -> Callable[..., None]:
        """生成线程安全的任务阶段事件回调。"""
        def _progress(
            action: str,
            stage: Optional[str] = None,
            detail: Optional[str] = None,
            progress_text: Optional[str] = None,
            metrics: Optional[dict[str, Any]] = None,
        ) -> None:
            try:
                asyncio.run_coroutine_threadsafe(
                    self._handle_job_stage_event(
                        job_id,
                        action=action,
                        stage=stage,
                        detail=detail,
                        progress_text=progress_text,
                        metrics=metrics,
                    ),
                    loop,
                )
            except RuntimeError:
                # 进程关闭或 loop 不可用时静默跳过，避免后台线程崩溃。
                return
        return _progress

    async def _handle_job_stage_event(
        self,
        job_id: str,
        *,
        action: str,
        stage: Optional[str] = None,
        detail: Optional[str] = None,
        progress_text: Optional[str] = None,
        metrics: Optional[dict[str, Any]] = None,
    ) -> None:
        """接收线程回传的阶段事件。"""
        if action == "enter" and stage:
            await self._enter_job_stage(job_id, stage, detail or progress_text or "", metrics=metrics)
            return
        if action == "heartbeat":
            await self._heartbeat_job_stage(job_id, detail=detail or progress_text, progress_text=progress_text, metrics=metrics)
            return
        if action == "complete":
            await self._complete_job_stage(job_id, detail=detail or progress_text, metrics=metrics)
            return
        if action == "warning":
            message = detail or progress_text or ""
            if message:
                await self._add_job_warning(job_id, message)

    @staticmethod
    def _apply_job_metrics(job: dict[str, Any], metrics: Optional[dict[str, Any]] = None) -> None:
        """合并任务进度指标。"""
        if not metrics:
            return
        for key in (
            "total_chars",
            "processed_chars",
            "total_chunks",
            "completed_chunks",
            "current_chunk_index",
            "current_chunk_chars",
        ):
            if key in metrics and metrics[key] is not None:
                job[key] = metrics[key]

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
        """更新任务状态。"""
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
                self._mark_current_trace_finished(job, status or job.get("status") or self.JOB_STATUS_FAILED, error)
            if started and not job.get("started_at"):
                job["started_at"] = self._utc_now_iso()
            if finished:
                job["finished_at"] = self._utc_now_iso()

    def _mark_current_trace_finished(self, job: dict[str, Any], status: str, error: Optional[str] = None) -> None:
        """收口当前 trace 阶段。"""
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
                    item["duration_ms"] = int((finished_dt - started_at).total_seconds() * 1000)
                break

    async def _enter_job_stage(
        self,
        job_id: str,
        stage: str,
        detail: str = "",
        *,
        metrics: Optional[dict[str, Any]] = None,
    ) -> None:
        """进入新阶段。"""
        now_iso = self._utc_now_iso()
        async with self._job_lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            self._apply_job_metrics(job, metrics)
            if job.get("stage") and job.get("stage") != stage:
                self._mark_current_trace_finished(job, "succeeded")
            job["stage"] = stage
            job["stage_detail"] = detail or ""
            job["stage_started_at"] = now_iso
            job["last_heartbeat_at"] = now_iso
            job["_last_soft_timeout_notice_at"] = None
            job["progress_text"] = detail or stage
            trace = job.setdefault("trace", [])
            trace.append(
                {
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
        metrics: Optional[dict[str, Any]] = None,
    ) -> None:
        """更新当前阶段心跳。"""
        now_iso = self._utc_now_iso()
        async with self._job_lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            self._apply_job_metrics(job, metrics)
            job["last_heartbeat_at"] = now_iso
            if detail is not None:
                job["stage_detail"] = detail
            if progress_text is not None:
                job["progress_text"] = progress_text
            elif detail is not None:
                job["progress_text"] = detail
            trace = job.get("trace") or []
            for item in reversed(trace):
                if not item.get("finished_at"):
                    if detail is not None:
                        item["detail"] = detail
                    break

    async def _complete_job_stage(
        self,
        job_id: str,
        detail: Optional[str] = None,
        *,
        metrics: Optional[dict[str, Any]] = None,
    ) -> None:
        """完成当前阶段。"""
        now_iso = self._utc_now_iso()
        async with self._job_lock:
            job = self._jobs.get(job_id)
            if not job or not job.get("stage"):
                return
            self._apply_job_metrics(job, metrics)
            job["last_heartbeat_at"] = now_iso
            if detail is not None:
                job["stage_detail"] = detail
                job["progress_text"] = detail
                trace = job.get("trace") or []
                for item in reversed(trace):
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
        """为任务追加 warning。"""
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

    async def _watch_job_soft_timeout(self, job_id: str) -> None:
        """监控单阶段运行时长，超过阈值只提示不失败。"""
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
                    last_notice_at = self._parse_utc_iso(job.get("_last_soft_timeout_notice_at"))
                    if last_notice_at and (now_dt - last_notice_at) < timedelta(seconds=self.SOFT_TIMEOUT_REPEAT_SECONDS):
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

    async def get_graph_job(self, job_id: str) -> dict[str, Any]:
        """获取后台任务状态。"""
        async with self._job_lock:
            job = self._jobs.get(job_id)
            if not job:
                return {"ok": False, "error": "任务不存在或已过期"}
            return {"ok": True, "data": self._build_job_response(job)}

    async def list_graph_jobs(self, limit: int = 10) -> dict[str, Any]:
        """返回最近图谱后台任务列表。"""
        safe_limit = max(1, min(int(limit or 10), 50))
        async with self._job_lock:
            jobs = sorted(
                (self._build_job_response(job) for job in self._jobs.values()),
                key=lambda item: item.get("created_at") or "",
                reverse=True,
            )[:safe_limit]
        return {"ok": True, "data": jobs}

    async def _start_background_job(
        self,
        *,
        job_type: str,
        runner: Callable[[str], Awaitable[dict[str, Any]]],
    ) -> dict[str, Any]:
        """创建后台任务并立即返回任务信息。"""
        job = await self._register_job(job_type)
        job_id = job["job_id"]

        async def _run() -> None:
            await self._set_job_state(
                job_id,
                status=self.JOB_STATUS_RUNNING,
                progress_text="任务开始执行",
                started=True,
            )
            watchdog_task = asyncio.create_task(self._watch_job_soft_timeout(job_id), name=f"watchdog:{job_id}")
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

        task = asyncio.create_task(_run(), name=f"{job_type}:{job_id}")
        async with self._job_lock:
            if job_id in self._jobs:
                self._jobs[job_id]["_task"] = task
        response_data = self._build_job_response(job)
        response_data["message"] = "任务已提交到后台处理"
        return {
            "ok": True,
            "data": response_data,
        }

    async def submit_graph_extract_import_job(self, file: UploadFile) -> dict[str, Any]:
        """提交文件抽取导入后台任务。"""
        filename = file.filename or "uploaded_file"
        try:
            file_bytes = await file.read()
            if not file_bytes:
                return {"ok": False, "error": "上传文件为空"}
            if len(file_bytes) > 50 * 1024 * 1024:
                return {"ok": False, "error": "文件大小不能超过 50MB"}
            return await self._start_background_job(
                job_type=self.IMPORT_JOB_TYPE,
                runner=lambda job_id: self._extract_and_import_graph_sync(file_bytes, filename, job_id),
            )
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            self._log_api_error("/admin/graph/jobs/extract-import", "extract_import_submit", exc, filename=filename)
            return {"ok": False, "error": f"提交抽取导入任务失败: {exc}"}

    async def submit_graph_embed_milvus_job(self, data: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """提交图谱嵌入后台任务。"""
        try:
            payload = data or {}
            return await self._start_background_job(
                job_type=self.EMBED_JOB_TYPE,
                runner=lambda job_id: self._embed_graph_to_milvus_sync(payload, job_id),
            )
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            self._log_api_error("/admin/graph/jobs/embed-milvus", "embed_graph_submit", exc)
            return {"ok": False, "error": f"提交嵌入任务失败: {exc}"}

    async def _extract_and_import_graph_sync(self, file_bytes: bytes, filename: str, job_id: str) -> dict[str, Any]:
        """执行图谱抽取导入主体。"""
        loop = asyncio.get_running_loop()
        stage_callback = self._make_stage_event_callback(loop, job_id)
        prepared = await asyncio.to_thread(
            self._extract_and_prepare_graph_blocking,
            file_bytes,
            filename,
            stage_callback,
        )
        chunk_results = prepared.pop("_chunk_results", [])
        total_chunks = len(chunk_results)

        await self._enter_job_stage(
            job_id,
            "write_neo4j",
            f"正在写入 Neo4j，共 {total_chunks} 段文本",
            metrics={
                "total_chunks": total_chunks,
                "completed_chunks": total_chunks,
            },
        )
        for chunk_result in chunk_results:
            chunk_index = int(chunk_result.get("index") or 0)
            node_queries = chunk_result.get("node_queries") or []
            relation_queries = chunk_result.get("relation_queries") or []

            if node_queries:
                await self._heartbeat_job_stage(
                    job_id,
                    detail=f"正在写入第 {chunk_index}/{total_chunks} 段文本对应的节点",
                    progress_text=f"正在写入第 {chunk_index}/{total_chunks} 段文本对应的节点",
                )
                await self._neo4j_client.run_multiple_cypher(node_queries)
            if relation_queries:
                await self._heartbeat_job_stage(
                    job_id,
                    detail=f"正在写入第 {chunk_index}/{total_chunks} 段文本对应的关系",
                    progress_text=f"正在写入第 {chunk_index}/{total_chunks} 段文本对应的关系",
                )
                await self._neo4j_client.run_multiple_cypher(relation_queries)

        await self._complete_job_stage(job_id, detail="Neo4j 写入完成")
        await self._enter_job_stage(job_id, "finalize", "图谱抽取完成，正在整理结果")
        await self._complete_job_stage(job_id, detail="任务执行完成")
        return prepared

    def _extract_and_prepare_graph_blocking(
        self,
        file_bytes: bytes,
        filename: str,
        stage_callback: Callable[..., None],
    ) -> dict[str, Any]:
        """在线程中执行文档解析与图谱抽取。"""
        suffix = Path(filename).suffix.lower()
        supported_suffixes = {".txt", ".md", ".pdf", ".docx", ".doc", ".pptx", ".ppt", ".html", ".htm", ".jpg", ".jpeg", ".png"}
        if suffix not in supported_suffixes:
            raise RuntimeError(f"不支持的文件格式: {suffix}，仅支持 {', '.join(sorted(supported_suffixes))}")
        chunk_limit = 20000

        def split_text_by_sentence(text: str, limit: int = 20000) -> list[str]:
            """按句切分长文本。"""
            chunks: list[str] = []
            remaining = (text or "").strip()
            sentence_ends = ["。", ".", "!", "！", "?", "？"]
            while remaining:
                if len(remaining) <= limit:
                    chunks.append(remaining)
                    break
                window = remaining[:limit]
                split_at = max(window.rfind(mark) for mark in sentence_ends)
                if split_at < 0:
                    split_at = limit
                else:
                    split_at += 1
                chunk = remaining[:split_at].strip()
                if not chunk:
                    chunk = remaining[:limit]
                    split_at = limit
                chunks.append(chunk)
                remaining = remaining[split_at:].strip()
            return chunks

        def extract_text_fallback(raw_bytes: bytes, raw_filename: str, raw_suffix: str) -> str:
            """Docling 不可用时的兜底解析。"""
            _ = raw_filename
            if raw_suffix == ".pdf":
                import fitz

                doc = fitz.open(stream=raw_bytes, filetype="pdf")
                try:
                    return "\n".join(page.get_text("text") for page in doc)
                finally:
                    doc.close()
            if raw_suffix == ".docx":
                from io import BytesIO
                from docx import Document

                doc = Document(BytesIO(raw_bytes))
                return "\n\n".join(p.text.strip() for p in doc.paragraphs if p.text and p.text.strip())
            if raw_suffix in {".html", ".htm"}:
                from bs4 import BeautifulSoup

                html = raw_bytes.decode("utf-8", errors="replace")
                return BeautifulSoup(html, "html.parser").get_text(separator="\n", strip=True)
            raise RuntimeError(f"当前环境无法使用 Docling，且该格式无内置回退解析器: {raw_suffix}")

        if not file_bytes:
            raise RuntimeError("上传文件为空")
        if len(file_bytes) > 50 * 1024 * 1024:
            raise RuntimeError("文件大小不能超过 50MB")

        stage_callback("enter", "validate_file", "正在校验文件")
        stage_callback("complete", "validate_file", "文件校验完成")

        parser_used = "plain_text"
        parse_warning = ""
        parsed = None
        text = ""

        if suffix in {".txt", ".md"}:
            stage_callback("enter", "parse_document", "正在解析纯文本内容")
            text = file_bytes.decode("utf-8", errors="replace")
            stage_callback("complete", "parse_document", "文档正文解析完成")
        else:
            try:
                from app.document_parser.docling_parser import DoclingParser

                parser = DoclingParser(enable_image_ocr=True, strict_image_ocr=True, progress_callback=stage_callback)
                parsed = parser.parse_bytes(file_bytes, filename)
                text = parsed.text
                parser_used = "docling"
            except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as docling_error:
                if "图片 OCR 失败" in str(docling_error):
                    raise
                self._log_api_error("/admin/graph/jobs/extract-import", "docling_parse_fallback", docling_error, filename=filename)
                stage_callback("heartbeat", "parse_document", "Docling 不可用，正在执行回退解析")
                text = extract_text_fallback(file_bytes, filename, suffix)
                stage_callback("complete", "parse_document", "回退解析完成")
                parser_used = "fallback"
                parse_warning = f"Docling 不可用，已自动回退解析: {docling_error}"

        if not text or not text.strip():
            raise RuntimeError("文件解析后文本为空，无法抽取图谱")

        if parse_warning:
            stage_callback("warning", detail=parse_warning)

        from app.knowledge_graph.extract_graph_data_utils import extract_text_to_graph_queries

        chunks = split_text_by_sentence(text, chunk_limit)
        total_chars = len(text)
        total_chunk_chars = sum(len(chunk) for chunk in chunks)
        processed_chars = 0
        total_entities = 0
        total_relations = 0
        total_node_batches = 0
        total_relation_batches = 0

        chunk_results: list[dict[str, Any]] = []
        for index, chunk in enumerate(chunks, start=1):
            chunk_chars = len(chunk)
            stage_callback(
                "heartbeat",
                detail=f"文档解析完成，已提取约 {len(text)} 个字符，准备处理第 {index}/{len(chunks)} 段文本",
                progress_text=f"正在处理第 {index}/{len(chunks)} 段文本",
                metrics={
                    "total_chars": total_chars,
                    "processed_chars": processed_chars,
                    "total_chunks": len(chunks),
                    "completed_chunks": index - 1,
                    "current_chunk_index": index,
                    "current_chunk_chars": chunk_chars,
                },
            )
            graph_data = extract_text_to_graph_queries(chunk, len(chunk), stage_callback=stage_callback)
            node_queries = graph_data["node_queries"]
            relation_queries = graph_data["relation_queries"]
            stats = graph_data["stats"]
            total_entities += stats["entities"]
            total_relations += stats["relations"]
            total_node_batches += len(node_queries)
            total_relation_batches += len(relation_queries)
            processed_chars = min(total_chars, processed_chars + chunk_chars)
            stage_callback(
                "heartbeat",
                detail=f"已完成第 {index}/{len(chunks)} 段文本抽取",
                progress_text=f"已完成第 {index}/{len(chunks)} 段文本抽取",
                metrics={
                    "total_chars": total_chars,
                    "processed_chars": processed_chars,
                    "total_chunks": len(chunks),
                    "completed_chunks": index,
                    "current_chunk_index": index,
                    "current_chunk_chars": chunk_chars,
                },
            )
            chunk_results.append(
                {
                    "index": index,
                    "chunk_chars": chunk_chars,
                    "node_queries": node_queries,
                    "relation_queries": relation_queries,
                }
            )

        ocr_engine = None
        docling_failed_pages: list[int] = []
        pdf_fallback_used = False
        if parsed and parser_used == "docling":
            metadata = parsed.metadata if isinstance(parsed.metadata, dict) else {}
            ocr_engine = metadata.get("ocr_engine")
            docling_failed_pages = list(metadata.get("docling_failed_pages") or [])
            pdf_fallback_used = bool(metadata.get("pdf_fallback_used"))
            if metadata.get("pdf_fallback_used"):
                parser_used = "docling_with_pymupdf_fallback"
                if not parse_warning:
                    parse_warning = str(metadata.get("pdf_fallback_reason") or "")

        response_data: dict[str, Any] = {
            "file_name": filename,
            "parsed_text_length": len(text),
            "total_chunk_chars": total_chunk_chars,
            "processed_text_length": processed_chars,
            "parser_used": parser_used,
            "ocr_engine": ocr_engine,
            "parse_warning": parse_warning,
            "docling_failed_pages": docling_failed_pages,
            "pdf_fallback_used": pdf_fallback_used,
            "chunk_count": len(chunks),
            "entity_count": total_entities,
            "relation_count": total_relations,
            "node_batches": total_node_batches,
            "relation_batches": total_relation_batches,
            "message": f"抽取并导入完成，实体 {total_entities} 个，关系 {total_relations} 条",
        }
        if parsed and parser_used == "docling":
            response_data["parse_details"] = {
                "format": parsed.format,
                "pages": parsed.pages,
                "tables_count": len(parsed.tables),
                "images_count": len(parsed.images),
                "metadata": parsed.metadata,
                "text_preview": parsed.text[:2000] if parsed.text else "",
            }
        response_data["_chunk_results"] = chunk_results
        return response_data

    async def _embed_graph_to_milvus_sync(self, data: Optional[dict[str, Any]], job_id: str) -> dict[str, Any]:
        """执行图谱嵌入主体。"""
        await self._enter_job_stage(job_id, "read_graph", "准备读取图谱数据")
        data = data or {}
        labels = data.get("labels")
        batch_size = data.get("batch_size", 100)
        kg_store = self._get_shared_kg_milvus_store()
        loop = asyncio.get_running_loop()

        def progress_callback(text: str) -> None:
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

    async def get_graph_labels(self) -> dict[str, Any]:
        """获取所有节点标签。"""
        try:
            query = """
            MATCH (n)
            UNWIND labels(n) AS label
            RETURN DISTINCT label
            ORDER BY label
            """
            result = await self._await_call(self._neo4j_client.run_cypher, query)
            labels = [record["label"] for record in result]
            return {"ok": True, "data": labels}
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            self._log_api_error("/admin/graph/labels", "neo4j_query", exc)
            return {"ok": False, "error": str(exc)}

    async def get_graph_relationship_types(self) -> dict[str, Any]:
        """获取所有关系类型。"""
        try:
            query = """
            MATCH ()-[r]->()
            RETURN DISTINCT type(r) AS rel_type
            ORDER BY rel_type
            """
            result = await self._await_call(self._neo4j_client.run_cypher, query)
            rel_types = [record["rel_type"] for record in result]
            return {"ok": True, "data": rel_types}
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            self._log_api_error("/admin/graph/relationship-types", "neo4j_query", exc)
            return {"ok": False, "error": str(exc)}

    async def get_graph_nodes(self, label: str = "", keyword: str = "", limit: int = 100, offset: int = 0) -> dict[str, Any]:
        """获取节点列表。"""
        try:
            if label:
                if keyword:
                    query = f"""
                    MATCH (n:{label})
                    WHERE n.name CONTAINS $keyword OR n.title CONTAINS $keyword
                    RETURN id(n) AS id, labels(n) AS labels, properties(n) AS properties
                    ORDER BY n.name
                    SKIP $offset LIMIT $limit
                    """
                    count_query = f"""
                    MATCH (n:{label})
                    WHERE n.name CONTAINS $keyword OR n.title CONTAINS $keyword
                    RETURN count(n) AS total
                    """
                else:
                    query = f"""
                    MATCH (n:{label})
                    RETURN id(n) AS id, labels(n) AS labels, properties(n) AS properties
                    ORDER BY n.name
                    SKIP $offset LIMIT $limit
                    """
                    count_query = f"""
                    MATCH (n:{label})
                    RETURN count(n) AS total
                    """
            else:
                if keyword:
                    query = """
                    MATCH (n)
                    WHERE n.name CONTAINS $keyword OR n.title CONTAINS $keyword
                    RETURN id(n) AS id, labels(n) AS labels, properties(n) AS properties
                    ORDER BY n.name
                    SKIP $offset LIMIT $limit
                    """
                    count_query = """
                    MATCH (n)
                    WHERE n.name CONTAINS $keyword OR n.title CONTAINS $keyword
                    RETURN count(n) AS total
                    """
                else:
                    query = """
                    MATCH (n)
                    RETURN id(n) AS id, labels(n) AS labels, properties(n) AS properties
                    ORDER BY n.name
                    SKIP $offset LIMIT $limit
                    """
                    count_query = """
                    MATCH (n)
                    RETURN count(n) AS total
                    """

            params = {"keyword": keyword, "offset": offset, "limit": limit}
            result = await self._await_call(self._neo4j_client.run_cypher, query, params)
            count_result = await self._await_call(self._neo4j_client.run_cypher, count_query, {"keyword": keyword} if keyword else {})
            total = count_result[0]["total"] if count_result else 0
            return {"ok": True, "data": result, "total": total}
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            self._log_api_error("/admin/graph/nodes", "neo4j_query", exc, label=label, keyword=keyword)
            return {"ok": False, "error": str(exc)}

    async def get_graph_node(self, node_id: int) -> dict[str, Any]:
        """获取单个节点详情。"""
        try:
            node_query = """
            MATCH (n)
            WHERE id(n) = $node_id
            RETURN id(n) AS id, labels(n) AS labels, properties(n) AS properties
            """
            node_result = await self._await_call(self._neo4j_client.run_cypher, node_query, {"node_id": node_id})
            if not node_result:
                return {"ok": False, "error": "节点不存在"}

            incoming_query = """
            MATCH (n)<-[r]-(m)
            WHERE id(n) = $node_id
            RETURN id(r) AS rel_id, type(r) AS rel_type, properties(r) AS rel_props,
                   id(m) AS source_id, labels(m) AS source_labels, m.name AS source_name
            """
            incoming = await self._await_call(self._neo4j_client.run_cypher, incoming_query, {"node_id": node_id})

            outgoing_query = """
            MATCH (n)-[r]->(m)
            WHERE id(n) = $node_id
            RETURN id(r) AS rel_id, type(r) AS rel_type, properties(r) AS rel_props,
                   id(m) AS target_id, labels(m) AS target_labels, m.name AS target_name
            """
            outgoing = await self._await_call(self._neo4j_client.run_cypher, outgoing_query, {"node_id": node_id})

            return {
                "ok": True,
                "data": {
                    "node": node_result[0],
                    "relationships": {
                        "incoming": incoming,
                        "outgoing": outgoing,
                    },
                },
            }
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            self._log_api_error("/admin/graph/nodes/{node_id}", "neo4j_query", exc, node_id=node_id)
            return {"ok": False, "error": str(exc)}

    async def create_graph_node(self, data: dict[str, Any]) -> dict[str, Any]:
        """创建新节点。"""
        try:
            raw_label = str(data.get("label", "")).strip()
            label = self._validate_cypher_identifier(raw_label)
            properties = self._sanitize_graph_properties(data.get("properties", {}))

            if not raw_label:
                return {"ok": False, "error": "节点标签不能为空"}
            if not label:
                return {"ok": False, "error": "节点标签非法，仅允许字母数字下划线且不能以数字开头"}

            if properties:
                props_str = ", ".join([f"`{k}`: ${k}" for k in properties.keys()])
                query = f"CREATE (n:`{label}` {{{props_str}}}) RETURN id(n) AS id"
            else:
                query = f"CREATE (n:`{label}`) RETURN id(n) AS id"

            result = await self._await_call(self._neo4j_client.run_cypher, query, properties)
            if result:
                return {"ok": True, "data": {"id": result[0]["id"], "message": "节点创建成功"}}
            return {"ok": False, "error": "创建失败"}
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            self._log_api_error("/admin/graph/nodes", "create_node", exc)
            return {"ok": False, "error": str(exc)}

    async def update_graph_node(self, node_id: int, data: dict[str, Any]) -> dict[str, Any]:
        """更新节点。"""
        try:
            properties = self._sanitize_graph_properties(data.get("properties", {}))
            if not properties:
                return {"ok": False, "error": "属性不能为空"}

            set_clauses = [f"n.`{k}` = ${k}" for k in properties.keys()]
            set_str = ", ".join(set_clauses)
            query = f"""
            MATCH (n)
            WHERE id(n) = $node_id
            SET {set_str}
            RETURN id(n) AS id, properties(n) AS properties
            """
            params = {"node_id": node_id, **properties}
            result = await self._await_call(self._neo4j_client.run_cypher, query, params)
            if result:
                return {"ok": True, "data": {"id": result[0]["id"], "message": "节点更新成功"}}
            return {"ok": False, "error": "节点不存在或更新失败"}
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            self._log_api_error("/admin/graph/nodes/{node_id}", "update_node", exc, node_id=node_id)
            return {"ok": False, "error": str(exc)}

    async def delete_graph_node(self, node_id: int) -> dict[str, Any]:
        """删除节点及关系。"""
        try:
            query = """
            MATCH (n)
            WHERE id(n) = $node_id
            DETACH DELETE n
            RETURN count(n) AS deleted_count
            """
            result = await self._await_call(self._neo4j_client.run_cypher, query, {"node_id": node_id})
            if result and result[0]["deleted_count"] > 0:
                return {"ok": True, "message": "节点删除成功"}
            return {"ok": False, "error": "节点不存在"}
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            self._log_api_error("/admin/graph/nodes/{node_id}", "delete_node", exc, node_id=node_id)
            return {"ok": False, "error": str(exc)}

    async def create_graph_relationship(self, data: dict[str, Any]) -> dict[str, Any]:
        """创建关系。"""
        try:
            source_id = data.get("source_id")
            target_id = data.get("target_id")
            raw_rel_type = str(data.get("rel_type", "")).strip()
            rel_type = self._validate_cypher_identifier(raw_rel_type)
            properties = self._sanitize_graph_properties(data.get("properties", {}))

            if source_id is None or target_id is None or not raw_rel_type:
                return {"ok": False, "error": "源节点、目标节点和关系类型不能为空"}
            if not rel_type:
                return {"ok": False, "error": "关系类型非法，仅允许字母数字下划线且不能以数字开头"}

            try:
                source_id_int = int(source_id)
                target_id_int = int(target_id)
            except (TypeError, ValueError) as exc:
                self._log_api_error(
                    "/admin/graph/relationships",
                    "create_relationship_id_parse",
                    exc,
                    source_id=source_id,
                    target_id=target_id,
                )
                return {"ok": False, "error": "源节点和目标节点ID必须是整数"}

            if properties:
                props_str = "{ " + ", ".join([f"`{k}`: ${k}" for k in properties.keys()]) + " }"
                query = f"""
                MATCH (source), (target)
                WHERE id(source) = $source_id AND id(target) = $target_id
                CREATE (source)-[r:`{rel_type}` {props_str}]->(target)
                RETURN id(r) AS rel_id
                """
                params = {"source_id": source_id_int, "target_id": target_id_int, **properties}
            else:
                query = f"""
                MATCH (source), (target)
                WHERE id(source) = $source_id AND id(target) = $target_id
                CREATE (source)-[r:`{rel_type}`]->(target)
                RETURN id(r) AS rel_id
                """
                params = {"source_id": source_id_int, "target_id": target_id_int}

            result = await self._await_call(self._neo4j_client.run_cypher, query, params)
            if result:
                return {"ok": True, "data": {"rel_id": result[0]["rel_id"], "message": "关系创建成功"}}
            return {"ok": False, "error": "创建失败，请检查节点是否存在"}
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            self._log_api_error("/admin/graph/relationships", "create_relationship", exc)
            return {"ok": False, "error": str(exc)}

    async def delete_graph_relationship(self, rel_id: int) -> dict[str, Any]:
        """删除关系。"""
        try:
            query = """
            MATCH ()-[r]->()
            WHERE id(r) = $rel_id
            DELETE r
            RETURN count(r) AS deleted_count
            """
            result = await self._await_call(self._neo4j_client.run_cypher, query, {"rel_id": rel_id})
            if result and result[0]["deleted_count"] > 0:
                return {"ok": True, "message": "关系删除成功"}
            return {"ok": False, "error": "关系不存在"}
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            self._log_api_error("/admin/graph/relationships/{rel_id}", "delete_relationship", exc, rel_id=rel_id)
            return {"ok": False, "error": str(exc)}

    async def execute_cypher(self, data: dict[str, Any]) -> dict[str, Any]:
        """执行只读 Cypher。"""
        try:
            query = str(data.get("query") or "").strip()
            if not query:
                return {"ok": False, "error": "查询语句不能为空"}

            forbidden_keywords = ["create", "merge", "delete", "remove", "set", "drop", "alter"]
            query_lower = query.lower()
            for keyword in forbidden_keywords:
                if keyword in query_lower:
                    return {"ok": False, "error": f"出于安全考虑，不允许执行包含 '{keyword}' 的操作"}

            result = await self._await_call(self._neo4j_client.run_cypher, query)
            return {"ok": True, "data": result}
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            self._log_api_error("/admin/graph/execute-cypher", "execute_cypher", exc)
            return {"ok": False, "error": str(exc)}

    async def extract_and_import_graph(self, file: UploadFile) -> dict[str, Any]:
        """提交文件抽取导入后台任务。"""
        return await self.submit_graph_extract_import_job(file=file)

    async def embed_graph_to_milvus(self, data: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """提交图谱嵌入后台任务。"""
        return await self.submit_graph_embed_milvus_job(data=data)

    async def get_graph_embed_status(self) -> dict[str, Any]:
        """获取知识图谱嵌入状态。"""
        try:
            kg_store = await self._await_call(self._get_shared_kg_milvus_store)
            stats = await self._await_call(kg_store.get_stats)
            return {"ok": True, "data": stats}
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            self._log_api_error("/admin/graph/embed-status", "embed_status", exc)
            return {"ok": False, "error": str(exc)}
