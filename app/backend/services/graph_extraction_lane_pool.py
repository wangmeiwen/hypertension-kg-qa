"""图谱抽取模型 lane 组件。"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from app.backend.services.graph_import_writer import GraphImportWriter

logger = logging.getLogger(__name__)


class GraphExtractionLanePool:
    """管理 lane 状态、错误分类和单块图谱抽取。"""

    @staticmethod
    def classify_lane_error(error: BaseException | str | None) -> str:
        """将 lane 错误归类为 permanent 或 transient。"""
        error_text = str(error or "").strip().lower()
        if not error_text:
            return "transient"
        permanent_markers = (
            "401 unauthorized",
            "403 forbidden",
            "404 not found",
        )
        if any(marker in error_text for marker in permanent_markers):
            return "permanent"
        transient_markers = (
            "429",
            "rate limit",
            "too many requests",
            "timeout",
            "timed out",
            "readtimeout",
            "connecttimeout",
            "connection reset",
            "connection aborted",
            "connection error",
            "api connection error",
            "service unavailable",
            "server disconnected",
            "temporarily unavailable",
            "502",
            "503",
            "504",
            "network",
        )
        if any(marker in error_text for marker in transient_markers):
            return "transient"
        return "transient"

    @staticmethod
    def normalize_chunk_status(status: Any) -> str:
        """将未知状态收敛到块级状态枚举。"""
        status_text = str(status or "").strip().lower()
        if status_text in {"pending", "running", "succeeded", "failed"}:
            return status_text
        return "pending"

    @staticmethod
    def first_error_line(error_text: Optional[str]) -> str:
        """提取异常首行，避免状态中保存整段堆栈。"""
        text = str(error_text or "").strip()
        if not text:
            return ""
        return next(
            (line.strip() for line in text.splitlines() if line.strip()),
            "",
        )

    @staticmethod
    def clone_lane_statuses(
        lane_statuses: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """复制 lane 状态用于写回任务响应。"""
        return [dict(item) for item in lane_statuses]

    @classmethod
    def count_active_lanes(cls, lane_statuses: list[dict[str, Any]]) -> int:
        """统计当前可立即提供服务的 lane 数量。"""
        return sum(
            1 for item in lane_statuses if item.get("status") in {"ready", "running"}
        )

    @classmethod
    def count_viable_lanes(cls, lane_statuses: list[dict[str, Any]]) -> int:
        """统计当前仍可恢复或继续提供服务的 lane 数量。"""
        return sum(
            1
            for item in lane_statuses
            if item.get("status") in {"ready", "running", "backoff"}
        )

    @staticmethod
    def format_lane_label(lane_config: dict[str, Any]) -> str:
        """格式化 lane 展示标签。"""
        lane_id = lane_config.get("lane_id")
        model_name = str(lane_config.get("model_name") or "")
        source = str(lane_config.get("source") or "")
        if source:
            return f"lane{lane_id}:{model_name} ({source})"
        return f"lane{lane_id}:{model_name}"

    @classmethod
    def build_lane_statuses(
        cls, lane_configs: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """为可用模型 lane 创建初始运行状态。"""
        return [
            {
                "lane_id": int(item["lane_id"]),
                "label": cls.format_lane_label(item),
                "model_name": str(item["model_name"]),
                "status": "ready",
                "current_chunk_index": None,
                "current_attempt": 0,
                "last_error": "",
                "backoff_until": None,
            }
            for item in lane_configs
        ]

    @staticmethod
    def update_lane_status(
        lane_statuses: list[dict[str, Any]],
        lane_id: int,
        *,
        status: Optional[str] = None,
        current_chunk_index: Optional[int] = None,
        current_attempt: Optional[int] = None,
        last_error: Optional[str] = None,
        backoff_until: Optional[str] = None,
    ) -> None:
        """原地更新指定 lane 的运行状态。"""
        for item in lane_statuses:
            if int(item.get("lane_id") or 0) != lane_id:
                continue
            if status is not None:
                item["status"] = status
            item["current_chunk_index"] = current_chunk_index
            if current_attempt is not None:
                item["current_attempt"] = current_attempt
            if last_error is not None:
                item["last_error"] = last_error
            item["backoff_until"] = backoff_until
            return

    async def run(
        self,
        *,
        runtime: Any,
        writer: GraphImportWriter,
        run_id: str,
        job_id: str,
        chunks: list[str],
        chunk_map: dict[int, dict[str, Any]],
        prepared: dict[str, Any],
        progress: dict[str, int],
        stage_callback: Callable[..., None],
        lane_configs: list[dict[str, Any]],
        max_parallelism: int,
        request_timeout_seconds: int,
        max_attempts: int,
        transient_backoff_seconds: int,
    ) -> dict[str, Any]:
        """并发抽取待处理文本块，并按块序串行写入图数据库。"""
        total_chunks = len(chunks)
        total_entities = int(progress["entity_count"])
        total_relations = int(progress["relation_count"])
        total_node_batches = int(progress.get("node_batches") or 0)
        total_relation_batches = int(progress.get("relation_batches") or 0)
        processed_chars = int(progress["processed_chars"])
        completed_chunks = int(progress["completed_chunks"])
        quality_reports: list[dict[str, Any]] = []
        from app.knowledge_graph.extract_graph_data_utils import create_graph_llm_bundle

        lane_configs = list(lane_configs[:max_parallelism])
        if not lane_configs:
            raise RuntimeError(
                "未配置可用的图谱抽取模型，请检查 MODEL_* 或 GRAPH_LLM_* 环境变量"
            )

        lane_statuses = self.build_lane_statuses(lane_configs)
        parallelism = len(lane_statuses)
        await runtime._update_job_parallel_state(
            job_id,
            parallelism=parallelism,
            active_lanes=self.count_active_lanes(lane_statuses),
            current_attempt=0,
            max_attempts=max_attempts,
            lane_statuses=lane_statuses,
        )
        prepared.update(
            {
                "parallelism": parallelism,
                "active_lanes": self.count_active_lanes(lane_statuses),
                "current_attempt": 0,
                "max_attempts": max_attempts,
                "lane_statuses": self.clone_lane_statuses(lane_statuses),
            }
        )
        logger.info(
            "graph_import.parallel_extract_start run_id=%s parallelism=%s lanes=%s",
            run_id,
            parallelism,
            [self.format_lane_label(item) for item in lane_configs],
        )

        pending_chunks = [
            (index, chunk)
            for index, chunk in enumerate(chunks, start=1)
            if self.normalize_chunk_status(chunk_map.get(index, {}).get("status"))
            != "succeeded"
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
                    timeout=request_timeout_seconds,
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
                self.update_lane_status(
                    lane_statuses,
                    lane_id,
                    status=status,
                    current_chunk_index=current_chunk_index,
                    current_attempt=current_attempt,
                    last_error=last_error,
                    backoff_until=backoff_until,
                )
                await runtime._update_job_parallel_state(
                    job_id,
                    parallelism=parallelism,
                    active_lanes=self.count_active_lanes(lane_statuses),
                    current_attempt=current_attempt,
                    max_attempts=max_attempts,
                    lane_statuses=lane_statuses,
                )

            async def fail_chunk_and_task(chunk_index: int, error_message: str) -> None:
                """标记当前块和整个导入任务失败，并通知并发协程停止。"""
                nonlocal terminal_error
                if terminal_error:
                    return
                terminal_error = error_message
                await runtime._mark_graph_chunk_failed(
                    run_id, chunk_index, error_message
                )
                await runtime._set_graph_run_status(
                    run_id, "failed", last_error=error_message
                )
                async with state_condition:
                    state_condition.notify_all()
                stop_event.set()

            async def lane_worker(lane_config: dict[str, Any]) -> None:
                """从队列领取待抽取文本块，并在对应 LLM lane 上执行抽取。"""
                nonlocal active_extractions, terminal_error, write_cursor
                lane_id = int(lane_config["lane_id"])
                llm_bundle = lane_bundles[lane_id]
                lane_label = self.format_lane_label(lane_config)
                while not stop_event.is_set():
                    try:
                        chunk_index, chunk = await asyncio.wait_for(
                            work_queue.get(), timeout=1.0
                        )
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
                    lane_stage_callback = self.make_lane_stage_callback(
                        stage_callback,
                        lane_id=lane_id,
                        chunk_index=chunk_index,
                        total_chunks=total_chunks,
                        attempt=attempt,
                        max_attempts=max_attempts,
                    )

                    await runtime._mark_graph_chunk_running(run_id, chunk_index)
                    await sync_lane_status(
                        lane_id,
                        status="running",
                        current_chunk_index=chunk_index,
                        current_attempt=attempt,
                        last_error="",
                        backoff_until=None,
                    )
                    await runtime._heartbeat_job_stage(
                        job_id,
                        detail=(
                            f"正在并发抽取第 {chunk_index}/{total_chunks} 段文本，"
                            f"{lane_label} 正在执行第 {attempt}/{max_attempts} 次尝试"
                        ),
                        progress_text=(
                            f"并发抽取中：第 {chunk_index}/{total_chunks} 段文本，"
                            f"{lane_label} 第 {attempt}/{max_attempts} 次尝试"
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
                            self.extract_single_chunk_blocking,
                            chunk,
                            chunk_index,
                            total_chunks,
                            prepared.get("parsed_text_length") or 0,
                            processed_chars,
                            lane_stage_callback,
                            llm_bundle,
                            prepared,
                            lane_config,
                        )
                    except Exception as exc:
                        error_text = str(exc).strip() or exc.__class__.__name__
                        failure_text = (
                            f"第 {chunk_index}/{total_chunks} 块在 {lane_label} 上第 "
                            f"{attempt}/{max_attempts} 次尝试失败: {error_text}"
                        )
                        await runtime._mark_graph_chunk_failed(
                            run_id, chunk_index, failure_text
                        )
                        error_type = self.classify_lane_error(exc)
                        first_error = self.first_error_line(error_text) or error_text

                        async with state_condition:
                            active_extractions = max(0, active_extractions - 1)
                            state_condition.notify_all()

                        if attempt >= max_attempts:
                            await fail_chunk_and_task(
                                chunk_index,
                                (
                                    f"第 {chunk_index}/{total_chunks} 块累计失败 "
                                    f"{max_attempts} 次，任务失败。最近错误: {first_error}"
                                ),
                            )
                            work_queue.task_done()
                            break

                        await work_queue.put((chunk_index, chunk))
                        await runtime._add_job_warning(
                            job_id,
                            (
                                f"{lane_label} 处理第 {chunk_index}/{total_chunks} 块失败，"
                                f"已回队重试（{attempt}/{max_attempts}）：{first_error}"
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
                            await runtime._add_job_warning(
                                job_id, f"{lane_label} 因鉴权或模型配置错误已摘除"
                            )
                            if self.count_viable_lanes(lane_statuses) <= 0:
                                await fail_chunk_and_task(
                                    chunk_index,
                                    "所有图谱抽取模型 lane 均不可用，任务失败",
                                )
                                work_queue.task_done()
                                break
                            work_queue.task_done()
                            break

                        backoff_until = (
                            datetime.utcnow()
                            + timedelta(seconds=transient_backoff_seconds)
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
                            await asyncio.sleep(transient_backoff_seconds)
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

                    await runtime._update_job_parallel_state(
                        job_id,
                        parallelism=parallelism,
                        active_lanes=self.count_active_lanes(lane_statuses),
                        current_attempt=attempt,
                        max_attempts=max_attempts,
                        lane_statuses=lane_statuses,
                    )
                    try:
                        write_result = await writer.write_chunk(
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
                    quality_reports.append(
                        {
                            "chunk_index": index,
                            **dict(chunk_result["quality"]),
                        }
                    )

                    write_cursor += 1
                    if next_schedule_cursor < len(pending_chunks):
                        await work_queue.put(pending_chunks[next_schedule_cursor])
                        next_schedule_cursor += 1

            worker_tasks = [
                asyncio.create_task(
                    lane_worker(item), name=f"graph-lane-{int(item['lane_id'])}"
                )
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

        return {
            "parallelism": parallelism,
            "active_lanes": self.count_active_lanes(lane_statuses),
            "current_attempt": int(
                max(attempt_counts.values()) if pending_chunks and attempt_counts else 0
            ),
            "max_attempts": max_attempts,
            "lane_statuses": self.clone_lane_statuses(lane_statuses),
            "processed_text_length": processed_chars,
            "completed_chunks": completed_chunks,
            "entity_count": total_entities,
            "relation_count": total_relations,
            "node_batches": total_node_batches,
            "relation_batches": total_relation_batches,
            "quality_reports": quality_reports,
        }

    def extract_single_chunk_blocking(  # pragma: no cover - external LLM runtime
        self,
        chunk: str,
        chunk_index: int,
        total_chunks: int,
        total_chars: int,
        processed_chars: int,
        stage_callback: Callable[..., None],
        llm_bundle: dict[str, Any],
        source_document: dict[str, Any],
        lane_config: dict[str, Any],
    ) -> dict[str, Any]:
        """在线程中执行单块图谱抽取（单次尝试）。

        Args:
            chunk: 当前文本块。
            chunk_index: 当前块序号。
            total_chunks: 总块数。
            total_chars: 文档总字符数。
            processed_chars: 已处理字符数。
            stage_callback: 阶段进度回调。
            llm_bundle: 当前 lane 使用的 LLM 配置和客户端上下文。
            source_document: 当前导入文档的可信元数据。
            lane_config: 当前抽取 lane 配置。

        Returns:
            dict[str, Any]: 当前块抽取出的节点查询、关系查询和统计数据。
        """
        from app.knowledge_graph.extract_graph_data_utils import (
            extract_text_to_graph_queries,
        )
        from app.knowledge_graph.graph_quality import GraphSourceContext

        chunk_chars = len(chunk)
        source_context: GraphSourceContext = {
            "source_id": str(source_document["file_hash"]),
            "source_name": str(source_document["file_name"]),
            "source_hash": str(source_document["file_hash"]),
            "source_chunk_index": chunk_index,
            "extracted_at": datetime.now(timezone.utc).isoformat(),
            "extraction_model": str(lane_config["model_name"]),
        }
        stage_callback(
            "heartbeat",
            detail=f"文档解析完成，准备抽取第 {chunk_index}/{total_chunks} 段文本",
            progress_text=f"正在抽取第 {chunk_index}/{total_chunks} 段文本",
            metrics={
                "total_chars": total_chars,
                "processed_chars": processed_chars,
                "total_chunks": total_chunks,
                "completed_chunks": chunk_index - 1,
                "current_chunk_index": chunk_index,
                "current_chunk_chars": chunk_chars,
            },
        )
        graph_data = extract_text_to_graph_queries(
            chunk,
            chunk_chars,
            stage_callback=stage_callback,
            llm_bundle=llm_bundle,
            source_context=source_context,
        )
        return {
            "chunk_index": chunk_index,
            "chunk_chars": chunk_chars,
            "node_queries": graph_data["node_queries"],
            "relation_queries": graph_data["relation_queries"],
            "stats": graph_data["stats"],
            "quality": graph_data["quality"],
        }

    @staticmethod
    def make_lane_stage_callback(  # pragma: no cover - external LLM lane callback
        base_callback: Callable[..., None],
        *,
        lane_id: int,
        chunk_index: int,
        total_chunks: int,
        attempt: int,
        max_attempts: int,
    ) -> Callable[..., None]:
        """为并发 lane 包装带上下文的阶段回调。

        Args:
            base_callback: 原始阶段回调。
            lane_id: 当前 lane 标识。
            chunk_index: 当前块序号。
            total_chunks: 总块数。
            attempt: 当前尝试次数。
            max_attempts: 最大尝试次数。

        Returns:
            Callable[..., None]: 会自动补充 lane、块和尝试次数前缀的回调。
        """

        prefix = f"[lane{lane_id} 第 {chunk_index}/{total_chunks} 块 第 {attempt}/{max_attempts} 次]"

        def _callback(
            action: str,
            stage: Optional[str] = None,
            detail: Optional[str] = None,
            progress_text: Optional[str] = None,
            metrics: Optional[dict[str, Any]] = None,
        ) -> None:
            next_detail = f"{prefix} {detail}" if detail else prefix
            next_progress = (
                f"{prefix} {progress_text}" if progress_text else next_detail
            )
            base_callback(
                action,
                stage=stage,
                detail=next_detail,
                progress_text=next_progress,
                metrics=metrics,
            )

        return _callback
