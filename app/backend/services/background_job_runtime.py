"""图谱后台任务运行时组件。"""

from __future__ import annotations

import asyncio
import logging
import traceback
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Optional
from uuid import uuid4

from app.backend.repositories.database import fetch_all, fetch_one

logger = logging.getLogger(__name__)


class BackgroundJobRuntimeMixin:
    """提供进程内后台任务的注册、观测、查询和生命周期管理。"""

    JOB_STATUS_QUEUED: str
    JOB_STATUS_RUNNING: str
    JOB_STATUS_PAUSED: str
    JOB_STATUS_SUCCEEDED: str
    JOB_STATUS_FAILED: str
    JOB_STATUS_CANCELLED: str
    GRAPH_IMPORT_CHUNK_LIMIT: int
    GRAPH_IMPORT_CHUNK_STRATEGY_VERSION: int
    GRAPH_LLM_MAX_ATTEMPTS: int
    SOFT_TIMEOUT_SECONDS: int
    SOFT_TIMEOUT_REPEAT_SECONDS: int

    _run_row_to_job_response: Callable[[Any], dict[str, Any]]

    def _initialize_background_job_runtime(self) -> None:
        """初始化单实例的后台任务状态。"""
        self._jobs: dict[str, dict[str, Any]] = {}
        self._job_lock = asyncio.Lock()
        self._max_retained_jobs = 100

    @staticmethod
    def _utc_now_iso() -> str:
        """返回统一的 UTC ISO 时间文本。"""
        return datetime.utcnow().isoformat(timespec="seconds") + "Z"

    @classmethod
    def _build_job_response(cls, job: dict[str, Any]) -> dict[str, Any]:
        """裁剪对外 job 响应。"""
        return {
            "job_id": job["job_id"],
            "job_type": job["job_type"],
            "status": job["status"],
            "labels": job.get("labels"),
            "batch_size": job.get("batch_size"),
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
            "file_hash": job.get("file_hash"),
            "run_id": job.get("run_id"),
            "is_resumed": bool(job.get("is_resumed")),
            "resume_run_id": job.get("resume_run_id"),
            "resumed_from_chunk": job.get("resumed_from_chunk"),
            "chunk_limit": job.get("chunk_limit"),
            "chunk_strategy_version": job.get("chunk_strategy_version"),
            "cache_used": bool(job.get("cache_used")),
            "cache_invalidated_reason": job.get("cache_invalidated_reason"),
            "resume_compatible": job.get("resume_compatible"),
            "parallelism": int(job.get("parallelism") or 0),
            "active_lanes": int(job.get("active_lanes") or 0),
            "current_attempt": int(job.get("current_attempt") or 0),
            "max_attempts": int(job.get("max_attempts") or cls.GRAPH_LLM_MAX_ATTEMPTS),
            "lane_statuses": [dict(item) for item in job.get("lane_statuses") or []],
            "warnings": list(job.get("warnings") or []),
            "trace": list(job.get("trace") or []),
            "result": job.get("result"),
            "error": job.get("error"),
            "deduplicated": bool(job.get("deduplicated")),
            "existing_job_id": job.get("existing_job_id"),
            "existing_run_id": job.get("existing_run_id"),
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

    async def _register_job(
        self,
        job_type: str,
        *,
        seed: Optional[dict[str, Any]] = None,
        dedupe_active: bool = False,
    ) -> dict[str, Any]:
        """注册进程内后台任务，并按文件哈希去重活跃任务。

        Args:
            job_type: 后台任务类型。
            seed: 可选初始任务字段。
            dedupe_active: 是否复用同文件、同策略的活跃任务。

        Returns:
            dict[str, Any]: 新建任务快照，或去重命中的已有任务快照。
        """
        async with self._job_lock:
            if dedupe_active and seed:
                file_hash = str(seed.get("file_hash") or "")
                chunk_limit = int(
                    seed.get("chunk_limit") or self.GRAPH_IMPORT_CHUNK_LIMIT
                )
                chunk_strategy_version = int(
                    seed.get("chunk_strategy_version")
                    or self.GRAPH_IMPORT_CHUNK_STRATEGY_VERSION
                )
                if file_hash:
                    for existing in self._jobs.values():
                        if (
                            existing.get("job_type") == job_type
                            and existing.get("status")
                            in {self.JOB_STATUS_QUEUED, self.JOB_STATUS_RUNNING}
                            and str(existing.get("file_hash") or "") == file_hash
                            and int(existing.get("chunk_limit") or 0) == chunk_limit
                            and int(existing.get("chunk_strategy_version") or 0)
                            == chunk_strategy_version
                        ):
                            row = dict(existing)
                            row["deduplicated"] = True
                            row["existing_job_id"] = str(existing.get("job_id") or "")
                            row["existing_run_id"] = str(
                                existing.get("run_id")
                                or existing.get("resume_run_id")
                                or ""
                            )
                            row["_deduplicated"] = True
                            return row
            job_id = f"graph-job-{uuid4().hex}"
            job: dict[str, Any] = {
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
                "file_hash": None,
                "run_id": None,
                "is_resumed": False,
                "resume_run_id": None,
                "resumed_from_chunk": None,
                "chunk_limit": self.GRAPH_IMPORT_CHUNK_LIMIT,
                "chunk_strategy_version": self.GRAPH_IMPORT_CHUNK_STRATEGY_VERSION,
                "cache_used": False,
                "cache_invalidated_reason": "",
                "resume_compatible": None,
                "parallelism": 0,
                "active_lanes": 0,
                "current_attempt": 0,
                "max_attempts": self.GRAPH_LLM_MAX_ATTEMPTS,
                "lane_statuses": [],
                "labels": None,
                "batch_size": None,
                "warnings": [],
                "trace": [],
                "result": None,
                "error": None,
                "_task": None,
                "_watchdog_task": None,
                "_last_soft_timeout_notice_at": None,
            }
            if seed:
                job.update(
                    {key: value for key, value in seed.items() if value is not None}
                )
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
                self.JOB_STATUS_PAUSED,
                self.JOB_STATUS_SUCCEEDED,
                self.JOB_STATUS_FAILED,
                self.JOB_STATUS_CANCELLED,
            }:
                self._jobs.pop(candidate["job_id"], None)

    def _make_stage_event_callback(
        self, loop: asyncio.AbstractEventLoop, job_id: str
    ) -> Callable[..., None]:  # pragma: no cover - background thread callback
        """生成线程安全的任务阶段事件回调，供阻塞线程回传进度。"""

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

    async def _handle_job_stage_event(  # pragma: no cover - background thread callback
        self,
        job_id: str,
        *,
        action: str,
        stage: Optional[str] = None,
        detail: Optional[str] = None,
        progress_text: Optional[str] = None,
        metrics: Optional[dict[str, Any]] = None,
    ) -> None:
        """接收阻塞线程回传的阶段事件并更新进程内任务状态。

        Args:
            job_id: 任务标识。
            action: 阶段事件动作，支持 enter、heartbeat、complete、warning。
            stage: 可选阶段名称。
            detail: 可选阶段明细。
            progress_text: 可选对外进度文案。
            metrics: 可选进度指标。

        Returns:
            None: 无返回值。
        """
        if action == "enter" and stage:
            await self._enter_job_stage(
                job_id, stage, detail or progress_text or "", metrics=metrics
            )
            return
        if action == "heartbeat":
            await self._heartbeat_job_stage(
                job_id,
                detail=detail or progress_text,
                progress_text=progress_text,
                metrics=metrics,
            )
            return
        if action == "complete":
            await self._complete_job_stage(
                job_id, detail=detail or progress_text, metrics=metrics
            )
            return
        if action == "warning":
            message = detail or progress_text or ""
            if message:
                await self._add_job_warning(job_id, message)

    @staticmethod
    def _apply_job_metrics(
        job: dict[str, Any], metrics: Optional[dict[str, Any]] = None
    ) -> None:
        """将线程回传的进度指标合并到任务对象。"""
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
        """更新进程内任务状态、结果、错误和生命周期时间。

        Args:
            job_id: 任务标识。
            status: 可选任务状态。
            progress_text: 可选对外进度文案。
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
                job["finished_at"] = self._utc_now_iso()

    def _mark_current_trace_finished(
        self, job: dict[str, Any], status: str, error: Optional[str] = None
    ) -> None:
        """把任务当前运行中的 trace 阶段标记为结束。"""
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

    async def _enter_job_stage(
        self,
        job_id: str,
        stage: str,
        detail: str = "",
        *,
        metrics: Optional[dict[str, Any]] = None,
    ) -> None:
        """进入任务新阶段并追加 trace 记录。

        Args:
            job_id: 任务标识。
            stage: 阶段名称。
            detail: 阶段说明文本。
            metrics: 可选进度指标。

        Returns:
            None: 无返回值。
        """
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
        """刷新当前阶段心跳、进度文案和进度指标。

        Args:
            job_id: 任务标识。
            detail: 可选阶段明细。
            progress_text: 可选对外进度文案。
            metrics: 可选进度指标。

        Returns:
            None: 无返回值。
        """
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
        """完成任务当前阶段并收口 trace。

        Args:
            job_id: 任务标识。
            detail: 可选完成明细。
            metrics: 可选进度指标。

        Returns:
            None: 无返回值。
        """
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
        """为任务追加可展示 warning。

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

    async def get_graph_job(self, job_id: str) -> dict[str, Any]:
        """获取图谱后台任务状态，支持进程内任务和持久化 run。

        Args:
            job_id: 后台任务标识或 graph-run 前缀的持久化 run 标识。

        Returns:
            dict[str, Any]: 任务状态快照或任务不存在错误。
        """
        async with self._job_lock:
            job = self._jobs.get(job_id)
            if not job:
                pass
            else:
                return {"ok": True, "data": self._build_job_response(job)}
        if job_id.startswith("graph-run-"):
            run_id = job_id.removeprefix("graph-run-")
            row = await fetch_one(
                "SELECT * FROM graph_import_runs WHERE run_id = :run_id",
                {"run_id": run_id},
            )
            if row:
                return {"ok": True, "data": self._run_row_to_job_response(row)}
            return {"ok": False, "error": "任务不存在或已过期"}
        if job_id.startswith("graph-job-"):
            return {"ok": False, "error": "任务不存在或已过期"}
        return {"ok": False, "error": "任务不存在或已过期"}

    async def list_graph_jobs(self, limit: int = 10) -> dict[str, Any]:
        """返回最近图谱后台任务列表，合并进程内任务和持久化 run。

        Args:
            limit: 返回数量上限。

        Returns:
            dict[str, Any]: 最近任务快照列表。
        """
        safe_limit = max(1, min(int(limit or 10), 50))
        async with self._job_lock:
            jobs = [self._build_job_response(job) for job in self._jobs.values()]
        active_run_ids = {
            str(job.get("run_id") or "") for job in jobs if job.get("run_id")
        }
        persisted_rows = await fetch_all(
            """
            SELECT *
            FROM graph_import_runs
            ORDER BY updated_at DESC
            LIMIT :limit
            """,
            {"limit": safe_limit * 3},
        )
        persisted_jobs = [
            self._run_row_to_job_response(row)
            for row in persisted_rows
            if str(row.get("run_id") or "") not in active_run_ids
        ]
        jobs = sorted(
            [*jobs, *persisted_jobs],
            key=lambda item: item.get("created_at") or "",
            reverse=True,
        )[:safe_limit]
        return {"ok": True, "data": jobs}

    async def _start_background_job(  # pragma: no cover - background task orchestration
        self,
        *,
        job_type: str,
        runner: Callable[[str], Awaitable[dict[str, Any]]],
        seed: Optional[dict[str, Any]] = None,
        dedupe_active: bool = False,
    ) -> dict[str, Any]:
        """创建图谱后台任务、启动 watchdog，并立即返回任务信息。

        Args:
            job_type: 后台任务类型。
            runner: 接收 job_id 并执行实际后台工作的异步函数。
            seed: 可选初始任务字段。
            dedupe_active: 是否复用同文件、同策略的活跃任务。

        Returns:
            dict[str, Any]: 任务提交响应。
        """
        job = await self._register_job(job_type, seed=seed, dedupe_active=dedupe_active)
        if job.get("_deduplicated"):
            response_data = self._build_job_response(job)
            response_data["message"] = "检测到同资源任务正在执行，已返回现有任务"
            return {"ok": True, "data": response_data}
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
                job_status = (
                    str(result.pop("_job_status", self.JOB_STATUS_SUCCEEDED))
                    if isinstance(result, dict)
                    else self.JOB_STATUS_SUCCEEDED
                )
                if job_status == self.JOB_STATUS_PAUSED:
                    pause_message = ""
                    pause_error = None
                    if isinstance(result, dict):
                        pause_message = str(
                            result.get("message") or "任务已暂停，可重新提交同文件继续"
                        )
                        pause_error = result.get("pause_reason") or result.get(
                            "last_error"
                        )
                    await self._set_job_state(
                        job_id,
                        status=self.JOB_STATUS_PAUSED,
                        progress_text=pause_message or "任务已暂停",
                        result=result if isinstance(result, dict) else None,
                        error=str(pause_error) if pause_error else None,
                        finished=True,
                    )
                else:
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
