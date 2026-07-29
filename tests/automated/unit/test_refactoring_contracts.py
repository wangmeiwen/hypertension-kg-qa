"""重构后共享契约和组件的回归测试。"""

from __future__ import annotations

import asyncio
from datetime import datetime
import json
import logging
from pathlib import Path
from typing import Any

from app.backend.domain.job_status import BackgroundJobStatus
from app.backend.repositories.row_utils import to_iso_timestamp
from app.backend.services.graph_crud_mixin import GraphCrudMixin
from app.backend.services.graph_import_cache_mixin import GraphImportCacheMixin
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
