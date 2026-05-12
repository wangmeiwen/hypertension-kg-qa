"""
问答任务队列模块。

本模块提供进程内任务队列能力，支持：
- 多 worker 并发执行后台任务
- 任务状态跟踪与取消
- 失败重试与幂等提交
- 任务状态落盘与重启恢复

主要类：
- QAJobQueueManager: 问答任务队列管理器

"""

import logging
import json
import os
import hashlib
from pathlib import Path
import queue
import threading
import time
from typing import Any, Callable, Optional


logger = logging.getLogger(__name__)


JobHandler = Callable[[dict[str, Any]], None]


def _resolve_worker_count() -> int:
    """
    解析任务队列 worker 数量配置。

    Args:
        None。

    Returns:
        int: 生效的 worker 数量。
    """
    raw_worker_count = str(os.getenv("QA_JOB_WORKER_COUNT", "")).strip()
    if raw_worker_count:
        try:
            parsed_worker_count = int(raw_worker_count)
            if parsed_worker_count > 0:
                return parsed_worker_count
            logger.warning("[qa_job_queue] invalid QA_JOB_WORKER_COUNT=%s, fallback to cpu-based value", raw_worker_count)
        except ValueError:
            logger.warning("[qa_job_queue] invalid QA_JOB_WORKER_COUNT=%s, fallback to cpu-based value", raw_worker_count)

    cpu_cores = os.cpu_count()
    if cpu_cores is None:
        return 2
    return max(2, min(cpu_cores, 16))


def _resolve_max_retries() -> int:
    """
    解析任务最大重试次数配置。

    Args:
        None。

    Returns:
        int: 生效的最大重试次数。
    """
    raw = str(os.getenv("QA_JOB_MAX_RETRIES", "2")).strip()
    try:
        value = int(raw)
    except ValueError:
        value = 2
    if value < 0:
        return 0
    if value > 5:
        return 5
    return value


def _build_idempotency_key(job: dict[str, Any]) -> str:
    """
    为任务构建幂等键。

    Args:
        job: 任务载荷字典。

    Returns:
        str: 幂等键，若无法构建则返回空字符串。
    """
    explicit = str(job.get("idempotency_key") or "").strip()
    if explicit:
        return explicit
    queue_key = str(job.get("queue_key") or "").strip()
    input_text = str(job.get("input_text") or "").strip()
    user_id = str(job.get("user_id") or "").strip()
    session_id = str(job.get("session_id") or "").strip()
    if not (queue_key or input_text or user_id or session_id):
        return ""
    seed = f"{queue_key}|{session_id}|{user_id}|{input_text}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


class QAJobQueueManager:
    """问答任务队列管理器：Web API 只负责入队，后台 worker 负责执行。"""

    def __init__(
        self,
        worker_count: int = 2,
        default_timeout_seconds: int = 240,
        max_retries: int = 2,
        state_file: str = "logs/qa_job_queue_state.json",
    ):
        """
        初始化问答任务队列管理器。

        Args:
            worker_count: worker 线程数量。
            default_timeout_seconds: 默认任务超时时间（秒）。
            max_retries: 默认最大重试次数。
            state_file: 队列状态持久化文件路径。

        Returns:
            None。
        """
        self.worker_count = worker_count
        self.default_timeout_seconds = default_timeout_seconds
        self.max_retries = max_retries
        self._job_queue: queue.Queue[Optional[dict[str, Any]]] = queue.Queue()
        self._workers: list[threading.Thread] = []
        self._handler: Optional[JobHandler] = None
        self._started = False
        self._lock = threading.Lock()
        self._job_registry: dict[str, dict[str, Any]] = {}
        self._active_jobs: dict[str, str] = {}
        self._job_payloads: dict[str, dict[str, Any]] = {}
        self._failed_jobs = 0
        self._completed_jobs = 0
        self._cancelled_jobs = 0
        self._retried_jobs = 0
        self._idempotency_hits = 0
        self._idempotency_index: dict[str, str] = {}
        self._state_file = Path(state_file)
        self._state_file.parent.mkdir(parents=True, exist_ok=True)

    def set_handler(self, handler: JobHandler) -> None:
        """
        设置任务执行回调。

        Args:
            handler: 任务处理函数。

        Returns:
            None。
        """
        self._handler = handler

    def start(self) -> None:
        """
        启动队列并创建 worker 线程。

        Args:
            None。

        Returns:
            None。
        """
        with self._lock:
            if self._started:
                return
            if self._handler is None:
                raise RuntimeError("job_handler_not_set")

            self._restore_pending_jobs_locked()

            self._started = True
            for index in range(self.worker_count):
                worker = threading.Thread(
                    target=self._worker_loop,
                    name=f"qa-worker-{index + 1}",
                    daemon=True,
                )
                worker.start()
                self._workers.append(worker)

    def submit(self, job: dict[str, Any]) -> str:
        """
        提交任务到队列。

        Args:
            job: 任务载荷字典，必须包含 job_id。

        Returns:
            str: 入队任务 ID（幂等命中时返回已存在任务 ID）。
        """
        if not self._started:
            raise RuntimeError("job_queue_not_started")
        job_id = str(job.get("job_id") or "").strip()
        if not job_id:
            raise RuntimeError("job_id_required")

        now_ts = time.time()
        timeout_seconds = int(job.get("timeout_seconds") or self.default_timeout_seconds)
        job["timeout_seconds"] = timeout_seconds
        idempotency_key = _build_idempotency_key(job)
        if idempotency_key:
            job["idempotency_key"] = idempotency_key

        with self._lock:
            if idempotency_key:
                existing_job_id = self._idempotency_index.get(idempotency_key)
                if existing_job_id:
                    existing_job = self._job_registry.get(existing_job_id) or {}
                    existing_status = str(existing_job.get("status") or "")
                    if existing_status in {"queued", "running"}:
                        self._idempotency_hits += 1
                        return existing_job_id
                    self._idempotency_index.pop(idempotency_key, None)

            self._job_registry[job_id] = {
                "job_id": job_id,
                "status": "queued",
                "submitted_at": now_ts,
                "started_at": None,
                "finished_at": None,
                "timeout_seconds": timeout_seconds,
                "deadline_ts": None,
                "cancel_requested": False,
                "cancel_reason": "",
                "queue_key": str(job.get("queue_key") or ""),
                "idempotency_key": idempotency_key,
                "retry_count": 0,
                "max_retries": int(job.get("max_retries") or self.max_retries),
                "error": "",
            }
            self._job_payloads[job_id] = dict(job)
            if idempotency_key:
                self._idempotency_index[idempotency_key] = job_id
            self._persist_state_locked()
        self._job_queue.put(job)
        return job_id

    def request_cancel(self, job_id: str, reason: str = "manual_cancel") -> bool:
        """
        请求取消指定任务。

        Args:
            job_id: 任务 ID。
            reason: 取消原因。

        Returns:
            bool: 是否成功标记取消请求。
        """
        with self._lock:
            job_info = self._job_registry.get(job_id)
            if not job_info:
                return False

            if str(job_info.get("status") or "") in {"done", "failed", "cancelled", "timeout"}:
                return False

            job_info["cancel_requested"] = True
            job_info["cancel_reason"] = reason
            self._persist_state_locked()
            return True

    def get_job_detail(self, job_id: str) -> Optional[dict[str, Any]]:
        """
        获取任务详情。

        Args:
            job_id: 任务 ID。

        Returns:
            Optional[dict[str, Any]]: 任务详情，不存在时返回 None。
        """
        with self._lock:
            job_info = self._job_registry.get(job_id)
            if not job_info:
                return None
            payload = self._job_payloads.get(job_id)
            result: dict[str, Any] = {
                "job_info": dict(job_info),
                "payload": dict(payload) if isinstance(payload, dict) else {},
            }
            return result

    def get_status_snapshot(self) -> dict[str, Any]:
        """
        获取队列状态快照。

        Args:
            None。

        Returns:
            dict[str, Any]: 队列整体状态与任务摘要。
        """
        with self._lock:
            jobs = list(self._job_registry.values())
            queued_jobs = [j for j in jobs if str(j.get("status") or "") == "queued"]
            running_jobs = [j for j in jobs if str(j.get("status") or "") == "running"]

            running_rows = [
                {
                    "job_id": str(j.get("job_id") or ""),
                    "queue_key": str(j.get("queue_key") or ""),
                    "started_at": j.get("started_at"),
                    "deadline_ts": j.get("deadline_ts"),
                    "cancel_requested": bool(j.get("cancel_requested")),
                }
                for j in running_jobs
            ]
            queued_rows = [
                {
                    "job_id": str(j.get("job_id") or ""),
                    "queue_key": str(j.get("queue_key") or ""),
                    "submitted_at": j.get("submitted_at"),
                    "timeout_seconds": int(j.get("timeout_seconds") or self.default_timeout_seconds),
                }
                for j in queued_jobs
            ]

            return {
                "worker_count": self.worker_count,
                "active_workers": len(self._active_jobs),
                "queue_length": len(queued_jobs),
                "failed_jobs": self._failed_jobs,
                "completed_jobs": self._completed_jobs,
                "cancelled_jobs": self._cancelled_jobs,
                "retried_jobs": self._retried_jobs,
                "idempotency_hits": self._idempotency_hits,
                "running_jobs": running_rows,
                "queued_jobs": queued_rows,
            }

    def should_stop(self, job_id: str) -> tuple[bool, str]:
        """
        判断任务是否应当停止执行。

        Args:
            job_id: 任务 ID。

        Returns:
            tuple[bool, str]: (是否停止, 原因)。
        """
        with self._lock:
            job_info = self._job_registry.get(job_id)
            if not job_info:
                return True, "job_not_found"

            if bool(job_info.get("cancel_requested")):
                reason = str(job_info.get("cancel_reason") or "manual_cancel")
                return True, reason

            deadline_ts = job_info.get("deadline_ts")
            if isinstance(deadline_ts, (int, float)) and time.time() > float(deadline_ts):
                return True, "timeout"

        return False, ""

    def mark_done(self, job_id: str) -> None:
        """
        标记任务完成。

        Args:
            job_id: 任务 ID。

        Returns:
            None。
        """
        with self._lock:
            job_info = self._job_registry.get(job_id)
            if not job_info:
                return
            job_info["status"] = "done"
            job_info["finished_at"] = time.time()
            self._completed_jobs += 1
            idempotency_key = str(job_info.get("idempotency_key") or "").strip()
            if idempotency_key and self._idempotency_index.get(idempotency_key) == job_id:
                self._idempotency_index.pop(idempotency_key, None)
            self._job_payloads.pop(job_id, None)
            self._persist_state_locked()

    def mark_failed(self, job_id: str, error: str) -> None:
        """
        标记任务失败。

        Args:
            job_id: 任务 ID。
            error: 错误信息。

        Returns:
            None。
        """
        with self._lock:
            job_info = self._job_registry.get(job_id)
            if not job_info:
                return
            job_info["status"] = "failed"
            job_info["error"] = error
            job_info["finished_at"] = time.time()
            self._failed_jobs += 1
            idempotency_key = str(job_info.get("idempotency_key") or "").strip()
            if idempotency_key and self._idempotency_index.get(idempotency_key) == job_id:
                self._idempotency_index.pop(idempotency_key, None)
            self._job_payloads.pop(job_id, None)
            self._persist_state_locked()

    def mark_cancelled(self, job_id: str, reason: str) -> None:
        """
        标记任务取消或超时。

        Args:
            job_id: 任务 ID。
            reason: 取消原因，timeout 表示超时。

        Returns:
            None。
        """
        with self._lock:
            job_info = self._job_registry.get(job_id)
            if not job_info:
                return
            final_status = "timeout" if reason == "timeout" else "cancelled"
            job_info["status"] = final_status
            job_info["cancel_reason"] = reason
            job_info["finished_at"] = time.time()
            self._cancelled_jobs += 1
            idempotency_key = str(job_info.get("idempotency_key") or "").strip()
            if idempotency_key and self._idempotency_index.get(idempotency_key) == job_id:
                self._idempotency_index.pop(idempotency_key, None)
            self._job_payloads.pop(job_id, None)
            self._persist_state_locked()

    def stop(self) -> None:
        """
        停止队列并回收 worker 线程。

        Args:
            None。

        Returns:
            None。
        """
        with self._lock:
            if not self._started:
                return
            for _ in self._workers:
                self._job_queue.put(None)
            for worker in self._workers:
                worker.join(timeout=2)
            self._workers = []
            self._started = False
            self._persist_state_locked()

    def _worker_loop(self) -> None:
        """
        worker 主循环：消费任务并执行处理逻辑。

        Args:
            None。

        Returns:
            None。
        """
        while True:
            job = self._job_queue.get()
            if job is None:
                self._job_queue.task_done()
                break

            job_id = str(job.get("job_id") or "")
            worker_name = threading.current_thread().name

            with self._lock:
                job_info = self._job_registry.get(job_id)
                if job_info:
                    if bool(job_info.get("cancel_requested")):
                        job_info["status"] = "cancelled"
                        job_info["finished_at"] = time.time()
                        self._cancelled_jobs += 1
                        self._job_payloads.pop(job_id, None)
                        self._persist_state_locked()
                        self._job_queue.task_done()
                        continue

                    timeout_seconds = int(job_info.get("timeout_seconds") or self.default_timeout_seconds)
                    now_ts = time.time()
                    job_info["status"] = "running"
                    job_info["started_at"] = now_ts
                    job_info["deadline_ts"] = now_ts + timeout_seconds
                    self._persist_state_locked()
                self._active_jobs[worker_name] = job_id

            try:
                if self._handler is None:
                    raise RuntimeError("job_handler_not_set")
                self._handler(job)
            except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
                should_retry = False
                retry_payload: Optional[dict[str, Any]] = None
                with self._lock:
                    job_info = self._job_registry.get(job_id)
                    if job_info:
                        retry_count = int(job_info.get("retry_count") or 0)
                        max_retries = int(job_info.get("max_retries") or self.max_retries)
                        if retry_count < max_retries:
                            should_retry = True
                            job_info["retry_count"] = retry_count + 1
                            job_info["status"] = "queued"
                            job_info["started_at"] = None
                            job_info["deadline_ts"] = None
                            job_info["error"] = str(exc)
                            self._retried_jobs += 1
                            payload = self._job_payloads.get(job_id)
                            if isinstance(payload, dict):
                                retry_payload = dict(payload)
                            self._persist_state_locked()

                if should_retry and retry_payload is not None:
                    self._job_queue.put(retry_payload)
                    logger.warning("[qa_job_worker_retry] job_id=%s retry_count=%s", job_id, self._job_registry.get(job_id, {}).get("retry_count"))
                else:
                    if job_id:
                        self.mark_failed(job_id, str(exc))
                    logger.exception("[qa_job_worker_error] %s", str(exc))
            finally:
                with self._lock:
                    if worker_name in self._active_jobs:
                        del self._active_jobs[worker_name]
                self._job_queue.task_done()

    def _restore_pending_jobs_locked(self) -> None:
        """
        从状态文件恢复待处理任务（需在锁内调用）。

        Args:
            None。

        Returns:
            None。
        """
        if not self._state_file.exists():
            return

        try:
            raw_text = self._state_file.read_text(encoding="utf-8")
            if not raw_text.strip():
                return
            state_data = json.loads(raw_text)
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            logger.warning("[qa_job_queue] load state failed: %s", str(exc))
            return

        pending_jobs = state_data.get("pending_jobs")
        if not isinstance(pending_jobs, list):
            return

        restored_count = 0
        for row in pending_jobs:
            if not isinstance(row, dict):
                continue
            payload = row.get("job")
            info = row.get("job_info")
            if not isinstance(payload, dict) or not isinstance(info, dict):
                continue

            job_id = str(payload.get("job_id") or "").strip()
            if not job_id:
                continue

            timeout_seconds = int(info.get("timeout_seconds") or self.default_timeout_seconds)
            retry_count = int(info.get("retry_count") or 0)
            max_retries = int(info.get("max_retries") or self.max_retries)
            idempotency_key = str(info.get("idempotency_key") or payload.get("idempotency_key") or "").strip()
            payload["timeout_seconds"] = timeout_seconds
            payload["idempotency_key"] = idempotency_key
            info["status"] = "queued"
            info["started_at"] = None
            info["finished_at"] = None
            info["deadline_ts"] = None
            info["cancel_requested"] = False
            info["cancel_reason"] = ""
            info["retry_count"] = retry_count
            info["max_retries"] = max_retries
            info["idempotency_key"] = idempotency_key

            self._job_registry[job_id] = info
            self._job_payloads[job_id] = payload
            if idempotency_key:
                self._idempotency_index[idempotency_key] = job_id
            self._job_queue.put(payload)
            restored_count += 1

        self._active_jobs = {}
        if restored_count:
            logger.info("[qa_job_queue] restored %s pending jobs from disk", restored_count)
        self._persist_state_locked()

    def _persist_state_locked(self) -> None:
        """
        持久化当前待处理任务状态到磁盘（需在锁内调用）。

        Args:
            None。

        Returns:
            None。
        """
        pending_jobs = []
        for job_id, payload in self._job_payloads.items():
            job_info = self._job_registry.get(job_id)
            if not job_info:
                continue
            if str(job_info.get("status") or "") not in {"queued", "running"}:
                continue
            pending_jobs.append(
                {
                    "job": payload,
                    "job_info": job_info,
                }
            )

        state_data = {
            "updated_at": time.time(),
            "pending_jobs": pending_jobs,
            "stats": {
                "failed_jobs": self._failed_jobs,
                "completed_jobs": self._completed_jobs,
                "cancelled_jobs": self._cancelled_jobs,
            },
        }

        tmp_file = self._state_file.with_suffix(".tmp")
        try:
            tmp_file.write_text(json.dumps(state_data, ensure_ascii=False), encoding="utf-8")
            tmp_file.replace(self._state_file)
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            logger.warning("[qa_job_queue] persist state failed: %s", str(exc))


qa_job_manager = QAJobQueueManager(
    worker_count=_resolve_worker_count(),
    default_timeout_seconds=240,
    max_retries=_resolve_max_retries(),
)

