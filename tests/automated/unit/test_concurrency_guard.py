import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest


pytestmark = pytest.mark.unit


def test_concurrency_guard_reader_times_out_while_writer_holds_lock() -> None:
    """验证写锁持有期间读锁会按 timeout 快速失败。"""
    from app.core.concurrency import ConcurrencyGuard, LockAcquireTimeout

    async def run_case() -> None:
        guard = ConcurrencyGuard()
        async with guard.writer_lock("resource"):
            with pytest.raises(LockAcquireTimeout):
                async with guard.reader_lock("resource", timeout=0.01):
                    pass

    asyncio.run(run_case())


def test_concurrency_guard_keyed_lock_serializes_same_key() -> None:
    from app.core.concurrency import ConcurrencyGuard

    async def run_case() -> None:
        guard = ConcurrencyGuard()
        active = 0
        max_active = 0

        async def worker() -> None:
            nonlocal active, max_active
            async with guard.keyed_lock("same-key"):
                active += 1
                max_active = max(max_active, active)
                await asyncio.sleep(0.01)
                active -= 1

        await asyncio.gather(*(worker() for _ in range(8)))
        assert max_active == 1

    asyncio.run(run_case())


def test_append_jsonl_atomic_concurrent_writes_are_complete(tmp_path: Path) -> None:
    """验证多线程追加 JSONL 时不会出现行丢失或半行写入。"""
    from app.backend.observability.session_logger import append_jsonl_atomic

    target = tmp_path / "logs" / "sessions" / "session-1" / "turns.jsonl"

    def write_line(index: int) -> None:
        append_jsonl_atomic(target, {"index": index, "message": f"value-{index}"})

    threads = [
        threading.Thread(target=write_line, args=(index,)) for index in range(25)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    rows = [
        json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()
    ]
    assert len(rows) == 25
    assert sorted(row["index"] for row in rows) == list(range(25))


def test_session_logger_project_root_points_to_repo_root() -> None:
    import app.backend.observability.session_logger as module

    assert module._project_root() == Path(__file__).resolve().parents[3]


def test_document_embedding_jobs_serialize_same_collection_writes() -> None:
    from app.backend.services.admin_documents_service import AdminDocumentsService
    from tests.conftest import await_call, log_api_error

    async def run_case() -> None:
        service = AdminDocumentsService(
            await_call=await_call,
            get_shared_vector_store=lambda: object(),
            log_api_error=log_api_error,
        )
        service._config.MILVUS_COLLECTION_NAME = f"pytest_{uuid4().hex}"
        active = 0
        max_active = 0

        def fake_blocking(
            file_bytes: bytes, filename: str, stage_callback: Any
        ) -> dict[str, Any]:
            nonlocal active, max_active
            _ = (file_bytes, stage_callback)
            active += 1
            max_active = max(max_active, active)
            time.sleep(0.02)
            active -= 1
            return {"file_name": filename}

        service._embed_text_document_blocking = fake_blocking  # type: ignore[method-assign]
        job1 = await service._register_job(service.TEXT_EMBED_JOB_TYPE)
        job2 = await service._register_job(service.TEXT_EMBED_JOB_TYPE)

        await asyncio.gather(
            service._embed_text_document_job(b"one", "one.txt", job1["job_id"]),
            service._embed_text_document_job(b"two", "two.txt", job2["job_id"]),
        )
        assert max_active == 1

    asyncio.run(run_case())


def test_graph_import_register_job_deduplicates_active_same_file() -> None:
    from app.backend.services.graph_admin_service import GraphAdminService
    from tests.conftest import await_call, log_api_error

    class FakeNeo4j:
        async def run_cypher(
            self, query: str, params: dict[str, Any] | None = None
        ) -> list[dict[str, Any]]:
            _ = (query, params)
            return []

    async def run_case() -> None:
        service = GraphAdminService(
            await_call=await_call,
            neo4j_client=FakeNeo4j(),
            get_shared_kg_milvus_store=lambda: object(),
            validate_cypher_identifier=lambda value: str(value),
            sanitize_graph_properties=lambda value: dict(value or {}),
            log_api_error=log_api_error,
        )
        seed = {
            "file_hash": "same-file",
            "chunk_limit": service.GRAPH_IMPORT_CHUNK_LIMIT,
            "chunk_strategy_version": service.GRAPH_IMPORT_CHUNK_STRATEGY_VERSION,
            "run_id": "run-1",
        }

        first = await service._register_job(
            service.IMPORT_JOB_TYPE, seed=seed, dedupe_active=True
        )
        second = await service._register_job(
            service.IMPORT_JOB_TYPE, seed=seed, dedupe_active=True
        )

        assert second["_deduplicated"] is True
        assert second["existing_job_id"] == first["job_id"]
        assert second["existing_run_id"] == "run-1"

    asyncio.run(run_case())
