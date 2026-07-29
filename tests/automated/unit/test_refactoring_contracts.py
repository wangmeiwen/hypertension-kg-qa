"""重构后共享契约和组件的回归测试。"""

from __future__ import annotations

import asyncio
from datetime import datetime
import json
import logging
from pathlib import Path
from typing import Any

import pytest

from app.backend.domain.job_status import BackgroundJobStatus
from app.backend.repositories.row_utils import to_iso_timestamp
from app.backend.services.background_job_runtime import BackgroundJobRuntimeMixin
from app.backend.services.graph_extraction_lane_pool import GraphExtractionLanePool
from app.backend.services.graph_crud_mixin import GraphCrudMixin
from app.backend.services.graph_import_cache_mixin import GraphImportCacheMixin
from app.backend.services.graph_import_parser import GraphImportParser
from app.backend.services.graph_import_writer import GraphImportWriter
from app.backend.services.session_log_repository import (
    read_request_records,
    safe_log_id,
)


class _Neo4jStub:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def run_cypher(
        self, query: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        self.queries.append(query)
        if "count(n) AS total" in query:
            return [{"total": 1}]
        return [{"id": 1, "created_at": "2026-07-27"}]


class _GraphCrudHarness(GraphCrudMixin):
    def __init__(self) -> None:
        self._neo4j_client = _Neo4jStub()
        self.errors: list[tuple[Any, ...]] = []

    async def _await_call(self, callback, *args):
        return callback(*args)

    @staticmethod
    def _validate_cypher_identifier(value: Any) -> str | None:
        text = str(value)
        return (
            text if text.replace("_", "").isalnum() and not text[:1].isdigit() else None
        )

    @staticmethod
    def _sanitize_graph_properties(value: Any) -> dict[str, Any]:
        return dict(value) if isinstance(value, dict) else {}

    def _log_api_error(self, *args: Any, **kwargs: Any) -> None:
        self.errors.append((*args, kwargs))


class _GraphCacheHarness(GraphImportCacheMixin):
    GRAPH_IMPORT_CACHE_VERSION = 2
    GRAPH_IMPORT_CHUNK_LIMIT = 10000
    GRAPH_IMPORT_CHUNK_STRATEGY_VERSION = 2

    @staticmethod
    def _utc_now_iso() -> str:
        return "2026-07-27T00:00:00Z"


class _BackgroundJobRuntimeHarness(BackgroundJobRuntimeMixin):
    JOB_STATUS_QUEUED = "queued"
    JOB_STATUS_RUNNING = "running"
    JOB_STATUS_PAUSED = "paused"
    JOB_STATUS_SUCCEEDED = "succeeded"
    JOB_STATUS_FAILED = "failed"
    JOB_STATUS_CANCELLED = "cancelled"
    GRAPH_IMPORT_CHUNK_LIMIT = 10000
    GRAPH_IMPORT_CHUNK_STRATEGY_VERSION = 2
    GRAPH_LLM_MAX_ATTEMPTS = 3
    SOFT_TIMEOUT_SECONDS = 300
    SOFT_TIMEOUT_REPEAT_SECONDS = 60

    def __init__(self) -> None:
        self._initialize_background_job_runtime()

    @staticmethod
    def _clone_lane_statuses(
        lane_statuses: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return [dict(item) for item in lane_statuses]

    @staticmethod
    def _run_row_to_job_response(row: Any) -> dict[str, Any]:
        return dict(row)


class _GraphWriterNeo4jStub:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.batches: list[list[str]] = []

    async def run_multiple_cypher(self, queries: list[str]) -> None:
        self.batches.append(queries)
        if self.fail:
            raise RuntimeError("neo4j unavailable")


class _GraphWriterRuntimeStub:
    def __init__(self) -> None:
        self.events: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.progress = {
            "processed_chars": 12,
            "completed_chunks": 1,
            "entity_count": 2,
            "relation_count": 1,
        }

    async def enter(self, *args: Any, **kwargs: Any) -> None:
        self.events.append(("enter", args, kwargs))

    async def heartbeat(self, *args: Any, **kwargs: Any) -> None:
        self.events.append(("heartbeat", args, kwargs))

    async def complete(self, *args: Any, **kwargs: Any) -> None:
        self.events.append(("complete", args, kwargs))

    async def mark_succeeded(self, *args: Any, **kwargs: Any) -> None:
        self.events.append(("succeeded", args, kwargs))

    async def mark_failed(self, *args: Any, **kwargs: Any) -> None:
        self.events.append(("failed", args, kwargs))

    async def set_run_status(self, *args: Any, **kwargs: Any) -> None:
        self.events.append(("run_status", args, kwargs))

    async def recompute_progress(self, *args: Any) -> dict[str, int]:
        self.events.append(("recompute", args, {}))
        return self.progress


class _LanePoolRuntimeStub:
    def __init__(self) -> None:
        self.events: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    async def _update_job_parallel_state(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        self.events.append(("parallel", args, kwargs))

    async def _mark_graph_chunk_running(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        self.events.append(("running", args, kwargs))

    async def _mark_graph_chunk_failed(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        self.events.append(("failed", args, kwargs))

    async def _set_graph_run_status(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        self.events.append(("run_status", args, kwargs))

    async def _heartbeat_job_stage(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        self.events.append(("heartbeat", args, kwargs))

    async def _add_job_warning(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        self.events.append(("warning", args, kwargs))


class _LanePoolWriterStub:
    def __init__(self) -> None:
        self.chunk_indexes: list[int] = []
        self.processed_chars = 0

    async def write_chunk(self, **kwargs: Any) -> dict[str, int]:
        chunk_index = int(kwargs["chunk_index"])
        self.chunk_indexes.append(chunk_index)
        self.processed_chars += int(kwargs["chunk_chars"])
        return {
            "processed_chars": self.processed_chars,
            "completed_chunks": len(self.chunk_indexes),
            "entity_count": len(self.chunk_indexes),
            "relation_count": len(self.chunk_indexes),
            "node_batches": 1,
            "relation_batches": 1,
        }


def _create_graph_import_writer(
    neo4j: _GraphWriterNeo4jStub,
    runtime: _GraphWriterRuntimeStub,
) -> GraphImportWriter:
    return GraphImportWriter(
        neo4j_client=neo4j,
        enter_job_stage=runtime.enter,
        heartbeat_job_stage=runtime.heartbeat,
        complete_job_stage=runtime.complete,
        mark_chunk_succeeded=runtime.mark_succeeded,
        mark_chunk_failed=runtime.mark_failed,
        set_run_status=runtime.set_run_status,
        recompute_run_progress=runtime.recompute_progress,
    )


def test_background_job_status_contract_is_shared() -> None:
    assert [status.value for status in BackgroundJobStatus] == [
        "queued",
        "running",
        "paused",
        "succeeded",
        "failed",
        "cancelled",
    ]


def test_database_timestamp_conversion_handles_nullable_rows() -> None:
    assert to_iso_timestamp(datetime(2026, 7, 27, 1, 2, 3)) == "2026-07-27T01:02:03"
    assert to_iso_timestamp(None) is None


def test_session_log_repository_sanitizes_and_sorts_records(tmp_path: Path) -> None:
    records_dir = tmp_path / "logs" / "sessions" / "unsafe_id" / "llm"
    records_dir.mkdir(parents=True)
    records = [
        {"request_id": "request-1", "ts": "2026-07-27T00:00:02", "value": 2},
        {"request_id": "other", "ts": "2026-07-27T00:00:00", "value": 0},
        {"request_id": "request-1", "ts": "2026-07-27T00:00:01", "value": 1},
    ]
    (records_dir / "records.jsonl").write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records),
        encoding="utf-8",
    )

    rows = read_request_records(
        project_root=tmp_path,
        session_id="../unsafe/id",
        request_id="request-1",
        category="llm",
        logger=logging.getLogger(__name__),
        warning_event="test_invalid_log",
    )

    assert safe_log_id("../unsafe/id") == "unsafe_id"
    assert [row["value"] for row in rows] == [1, 2]


def test_graph_node_label_is_validated_before_query() -> None:
    service = _GraphCrudHarness()

    result = asyncio.run(service.get_graph_nodes(label="Disease`) MATCH (x)"))

    assert result["ok"] is False
    assert service._neo4j_client.queries == []


def test_graph_import_cache_round_trip(monkeypatch, tmp_path: Path) -> None:
    service = _GraphCacheHarness()
    monkeypatch.setattr(
        _GraphCacheHarness,
        "_get_graph_import_cache_dir",
        classmethod(lambda cls: tmp_path),
    )
    prepared = {
        "file_name": "guide.pdf",
        "parsed_text_length": 4,
        "total_chunk_chars": 4,
        "chunk_count": 1,
    }

    service._write_graph_import_cache("file-hash", prepared, ["text"])
    loaded, invalidation_reason = service._load_graph_import_cache("file-hash")

    assert invalidation_reason is None
    assert loaded is not None
    assert loaded["_chunks"] == ["text"]
    assert loaded["cache_used"] is True


def test_graph_import_parser_splits_plain_text_and_reuses_cache(
    monkeypatch,
    tmp_path: Path,
) -> None:
    service = _GraphCacheHarness()
    monkeypatch.setattr(_GraphCacheHarness, "GRAPH_IMPORT_CHUNK_LIMIT", 8)
    monkeypatch.setattr(
        _GraphCacheHarness,
        "_get_graph_import_cache_dir",
        classmethod(lambda cls: tmp_path),
    )
    errors: list[tuple[Any, ...]] = []
    parser = GraphImportParser(
        cache=service,
        chunk_limit=8,
        chunk_strategy_version=service.GRAPH_IMPORT_CHUNK_STRATEGY_VERSION,
        log_api_error=lambda *args, **kwargs: errors.append((*args, kwargs)),
    )
    events: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def stage_callback(*args: Any, **kwargs: Any) -> None:
        events.append((args, kwargs))

    file_bytes = "第一句。第二句稍微长一些。第三句。".encode()

    prepared = parser.parse_and_split(file_bytes, "guide.txt", stage_callback)
    cached = parser.parse_and_split(file_bytes, "guide.txt", stage_callback)

    assert prepared["parser_used"] == "plain_text"
    assert prepared["chunk_limit"] == 8
    assert prepared["cache_used"] is False
    assert all(len(chunk) <= 8 for chunk in prepared["_chunks"])
    assert "".join(prepared["_chunks"]) == file_bytes.decode()
    assert cached["cache_used"] is True
    assert cached["_chunks"] == prepared["_chunks"]
    assert errors == []
    assert events[0][0] == ("enter", "validate_file", "正在校验文件")


def test_graph_import_parser_hard_splits_text_without_sentence_marks() -> None:
    assert GraphImportParser.split_text_by_sentence("abcdefghij", 4) == [
        "abcd",
        "efgh",
        "ij",
    ]


def test_graph_lane_statuses_are_initialized_for_available_models() -> None:
    statuses = GraphExtractionLanePool.build_lane_statuses(
        [
            {
                "lane_id": 1,
                "model_name": "graph-model",
                "source": "primary",
            }
        ]
    )

    assert statuses == [
        {
            "lane_id": 1,
            "label": "lane1:graph-model (primary)",
            "model_name": "graph-model",
            "status": "ready",
            "current_chunk_index": None,
            "current_attempt": 0,
            "last_error": "",
            "backoff_until": None,
        }
    ]


def test_graph_lane_pool_classifies_and_updates_lane_state() -> None:
    statuses = GraphExtractionLanePool.build_lane_statuses(
        [{"lane_id": 1, "model_name": "graph-model", "source": "primary"}]
    )

    GraphExtractionLanePool.update_lane_status(
        statuses,
        1,
        status="backoff",
        current_chunk_index=2,
        current_attempt=1,
        last_error="429 rate limit",
        backoff_until="2026-07-29T00:00:00Z",
    )

    assert (
        GraphExtractionLanePool.classify_lane_error("401 Unauthorized") == "permanent"
    )
    assert GraphExtractionLanePool.classify_lane_error("429 rate limit") == "transient"
    assert GraphExtractionLanePool.count_active_lanes(statuses) == 0
    assert GraphExtractionLanePool.count_viable_lanes(statuses) == 1
    assert statuses[0]["status"] == "backoff"
    assert statuses[0]["current_chunk_index"] == 2


def test_graph_lane_pool_schedules_chunks_and_writes_in_order(monkeypatch) -> None:
    pool = GraphExtractionLanePool()
    runtime = _LanePoolRuntimeStub()
    writer = _LanePoolWriterStub()

    monkeypatch.setattr(
        "app.knowledge_graph.extract_graph_data_utils.create_graph_llm_bundle",
        lambda **kwargs: kwargs,
    )
    monkeypatch.setattr(
        pool,
        "extract_single_chunk_blocking",
        lambda chunk, chunk_index, *_args: {
            "chunk_index": chunk_index,
            "chunk_chars": len(chunk),
            "node_queries": [f"node-{chunk_index}"],
            "relation_queries": [f"relation-{chunk_index}"],
            "stats": {"entities": 1, "relations": 1},
            "quality": {
                "rejected": [],
                "metrics": {
                    "accepted_entities": 1,
                    "accepted_relations": 1,
                },
            },
        },
    )

    def stage_callback(*_args: Any, **_kwargs: Any) -> None:
        return

    result = asyncio.run(
        pool.run(
            runtime=runtime,
            writer=writer,
            run_id="run-1",
            job_id="job-1",
            chunks=["first", "second"],
            chunk_map={},
            prepared={"parsed_text_length": 11},
            progress={
                "processed_chars": 0,
                "completed_chunks": 0,
                "entity_count": 0,
                "relation_count": 0,
                "node_batches": 0,
                "relation_batches": 0,
            },
            stage_callback=stage_callback,
            lane_configs=[
                {
                    "lane_id": 1,
                    "api_key": "key",
                    "base_url": "https://example.test",
                    "model_name": "graph-model",
                    "source": "primary",
                }
            ],
            max_parallelism=1,
            request_timeout_seconds=10,
            max_attempts=3,
            transient_backoff_seconds=0,
        )
    )

    assert writer.chunk_indexes == [1, 2]
    assert result["completed_chunks"] == 2
    assert result["entity_count"] == 2
    assert result["node_batches"] == 2
    assert result["current_attempt"] == 1
    assert result["lane_statuses"][0]["status"] == "ready"


def test_background_job_runtime_deduplicates_active_resources() -> None:
    runtime = _BackgroundJobRuntimeHarness()

    async def register_jobs() -> tuple[dict[str, Any], dict[str, Any]]:
        seed = {
            "file_hash": "same-file",
            "chunk_limit": 10000,
            "chunk_strategy_version": 2,
        }
        first = await runtime._register_job(
            "graph_extract_import",
            seed=seed,
            dedupe_active=True,
        )
        duplicate = await runtime._register_job(
            "graph_extract_import",
            seed=seed,
            dedupe_active=True,
        )
        return first, duplicate

    first, duplicate = asyncio.run(register_jobs())

    assert duplicate["job_id"] == first["job_id"]
    assert duplicate["_deduplicated"] is True
    assert duplicate["existing_job_id"] == first["job_id"]


def test_background_job_runtime_finishes_successful_runner() -> None:
    runtime = _BackgroundJobRuntimeHarness()

    async def run_job() -> dict[str, Any]:
        async def runner(job_id: str) -> dict[str, Any]:
            await runtime._enter_job_stage(job_id, "work", "正在执行")
            await runtime._complete_job_stage(job_id, detail="执行完成")
            return {"value": 1}

        response = await runtime._start_background_job(
            job_type="graph_embed_milvus",
            runner=runner,
        )
        job_id = response["data"]["job_id"]
        await runtime._jobs[job_id]["_task"]
        return runtime._jobs[job_id]

    job = asyncio.run(run_job())

    assert job["status"] == "succeeded"
    assert job["result"] == {"value": 1}
    assert job["trace"][0]["status"] == "succeeded"
    assert job["_watchdog_task"] is None


def test_background_job_soft_timeout_only_adds_warning(monkeypatch) -> None:
    runtime = _BackgroundJobRuntimeHarness()
    runtime.SOFT_TIMEOUT_SECONDS = 0
    sleep_count = 0

    async def fake_sleep(_seconds: float) -> None:
        nonlocal sleep_count
        sleep_count += 1
        if sleep_count > 1:
            next(iter(runtime._jobs.values()))["status"] = "succeeded"

    monkeypatch.setattr(
        "app.backend.services.background_job_runtime.asyncio.sleep",
        fake_sleep,
    )

    async def watch_job() -> dict[str, Any]:
        job = await runtime._register_job("graph_extract_import")
        await runtime._set_job_state(
            job["job_id"],
            status="running",
            started=True,
        )
        await runtime._enter_job_stage(job["job_id"], "extract", "正在抽取")
        await runtime._watch_job_soft_timeout(job["job_id"])
        return runtime._jobs[job["job_id"]]

    job = asyncio.run(watch_job())

    assert job["status"] == "succeeded"
    assert len(job["warnings"]) == 1
    assert job["warnings"][0]["stage"] == "extract"
    assert "持续" in job["warnings"][0]["message"]


def test_graph_import_writer_writes_nodes_before_relations() -> None:
    neo4j = _GraphWriterNeo4jStub()
    runtime = _GraphWriterRuntimeStub()
    writer = _create_graph_import_writer(neo4j, runtime)

    result = asyncio.run(
        writer.write_chunk(
            run_id="run-1",
            job_id="job-1",
            chunk_index=1,
            total_chunks=2,
            chunk_chars=12,
            chunk_result={
                "node_queries": ["node-1", "node-2"],
                "relation_queries": ["relation-1"],
                "stats": {"entities": 2, "relations": 1},
                "lane_label": "lane1:model",
                "quality": {
                    "rejected": [],
                    "metrics": {
                        "accepted_entities": 2,
                        "accepted_relations": 1,
                    },
                },
            },
            total_chars=24,
            processed_chars=0,
            completed_chunks=0,
        )
    )

    assert neo4j.batches == [
        ["node-1", "node-2"],
        ["relation-1"],
    ]
    assert result == {
        "processed_chars": 12,
        "completed_chunks": 1,
        "entity_count": 2,
        "relation_count": 1,
        "node_batches": 2,
        "relation_batches": 1,
    }
    assert [event[0] for event in runtime.events] == [
        "enter",
        "heartbeat",
        "heartbeat",
        "succeeded",
        "recompute",
        "complete",
    ]


def test_graph_import_writer_marks_chunk_and_run_failed() -> None:
    neo4j = _GraphWriterNeo4jStub(fail=True)
    runtime = _GraphWriterRuntimeStub()
    writer = _create_graph_import_writer(neo4j, runtime)

    with pytest.raises(RuntimeError, match="第 1/1 块写入 Neo4j 失败"):
        asyncio.run(
            writer.write_chunk(
                run_id="run-1",
                job_id="job-1",
                chunk_index=1,
                total_chunks=1,
                chunk_chars=12,
                chunk_result={
                    "node_queries": ["node-1"],
                    "relation_queries": ["relation-1"],
                    "stats": {"entities": 1, "relations": 1},
                    "quality": {
                        "rejected": [],
                        "metrics": {
                            "accepted_entities": 1,
                            "accepted_relations": 1,
                        },
                    },
                },
                total_chars=12,
                processed_chars=0,
                completed_chunks=0,
            )
        )

    assert [event[0] for event in runtime.events] == [
        "enter",
        "heartbeat",
        "failed",
        "run_status",
    ]
    assert "neo4j unavailable" in runtime.events[-1][2]["last_error"]


def test_readonly_cypher_allows_field_names_and_blocks_write_capabilities() -> None:
    service = _GraphCrudHarness()

    allowed = asyncio.run(
        service.execute_cypher({"query": "MATCH (n) RETURN n.created_at"})
    )
    blocked_call = asyncio.run(service.execute_cypher({"query": "CALL db.labels()"}))
    blocked_write = asyncio.run(
        service.execute_cypher({"query": "MATCH (n) SET n.name = 'x'"})
    )

    assert allowed["ok"] is True
    assert blocked_call["ok"] is False
    assert blocked_write["ok"] is False
