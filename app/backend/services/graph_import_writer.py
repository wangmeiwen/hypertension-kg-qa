"""图谱导入顺序写入组件。"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, TypedDict


class GraphImportWriteResult(TypedDict):
    """单块写入完成后的持久化进度。"""

    processed_chars: int
    completed_chunks: int
    entity_count: int
    relation_count: int
    node_batches: int
    relation_batches: int


class GraphImportWriter:
    """负责将单块抽取结果按节点、关系顺序写入 Neo4j。"""

    def __init__(
        self,
        *,
        neo4j_client: Any,
        enter_job_stage: Callable[..., Awaitable[None]],
        heartbeat_job_stage: Callable[..., Awaitable[None]],
        complete_job_stage: Callable[..., Awaitable[None]],
        mark_chunk_succeeded: Callable[..., Awaitable[None]],
        mark_chunk_failed: Callable[..., Awaitable[None]],
        set_run_status: Callable[..., Awaitable[None]],
        recompute_run_progress: Callable[..., Awaitable[dict[str, int]]],
    ) -> None:
        self._neo4j_client = neo4j_client
        self._enter_job_stage = enter_job_stage
        self._heartbeat_job_stage = heartbeat_job_stage
        self._complete_job_stage = complete_job_stage
        self._mark_chunk_succeeded = mark_chunk_succeeded
        self._mark_chunk_failed = mark_chunk_failed
        self._set_run_status = set_run_status
        self._recompute_run_progress = recompute_run_progress

    async def write_chunk(
        self,
        *,
        run_id: str,
        job_id: str,
        chunk_index: int,
        total_chunks: int,
        chunk_chars: int,
        chunk_result: dict[str, Any],
        total_chars: int,
        processed_chars: int,
        completed_chunks: int,
    ) -> GraphImportWriteResult:
        """顺序写入一个抽取结果，并返回数据库重算后的进度。"""
        node_queries = list(chunk_result["node_queries"])
        relation_queries = list(chunk_result["relation_queries"])
        stats = dict(chunk_result["stats"])
        quality_metrics = dict(chunk_result["quality"]["metrics"])
        node_batches = len(node_queries)
        relation_batches = len(relation_queries)
        lane_label = str(chunk_result.get("lane_label") or "")

        await self._enter_job_stage(
            job_id,
            "write_neo4j",
            (
                f"正在写入第 {chunk_index}/{total_chunks} 段文本到 Neo4j"
                f"{f'（来源 {lane_label}）' if lane_label else ''}"
            ),
            metrics={
                "total_chars": total_chars,
                "processed_chars": processed_chars,
                "total_chunks": total_chunks,
                "completed_chunks": completed_chunks,
                "current_chunk_index": chunk_index,
                "current_chunk_chars": chunk_chars,
                **quality_metrics,
            },
        )
        try:
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
        except Exception as exc:
            write_error = f"第 {chunk_index}/{total_chunks} 块写入 Neo4j 失败: {exc}"
            await self._mark_chunk_failed(run_id, chunk_index, write_error)
            await self._set_run_status(
                run_id,
                "failed",
                last_error=write_error,
            )
            raise RuntimeError(write_error) from exc

        await self._mark_chunk_succeeded(
            run_id,
            chunk_index=chunk_index,
            entity_count=int(stats.get("entities") or 0),
            relation_count=int(stats.get("relations") or 0),
            node_batches=node_batches,
            relation_batches=relation_batches,
        )
        progress = await self._recompute_run_progress(run_id)
        next_processed_chars = int(progress["processed_chars"])
        next_completed_chunks = int(progress["completed_chunks"])
        await self._complete_job_stage(
            job_id,
            detail=f"已完成第 {chunk_index}/{total_chunks} 段文本写入",
            metrics={
                "total_chars": total_chars,
                "processed_chars": next_processed_chars,
                "total_chunks": total_chunks,
                "completed_chunks": next_completed_chunks,
                "current_chunk_index": chunk_index,
                "current_chunk_chars": chunk_chars,
                **quality_metrics,
            },
        )
        return {
            "processed_chars": next_processed_chars,
            "completed_chunks": next_completed_chunks,
            "entity_count": int(progress["entity_count"]),
            "relation_count": int(progress["relation_count"]),
            "node_batches": node_batches,
            "relation_batches": relation_batches,
        }
