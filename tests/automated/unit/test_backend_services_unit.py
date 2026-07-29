"""后端服务层单元测试集合。

该文件集中覆盖 A2A 流式事件、会话历史、文档任务、图谱管理、
公开图谱、管理员会话观测和会话流式持久化等服务层行为。
"""

import asyncio
import io
import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest
from starlette.datastructures import UploadFile

from tests.conftest import await_call, log_api_error


pytestmark = pytest.mark.unit


def upload_file(name: str, body: bytes) -> UploadFile:
    """构造 Starlette UploadFile，供服务层上传接口测试复用。"""
    return UploadFile(filename=name, file=io.BytesIO(body))


def test_a2a_event_helpers_parse_task_payloads() -> None:
    from app.backend.services.a2a_event_stream_service import (
        build_processing_steps_from_progress_events,
        extract_a2a_artifact,
        extract_reply_delta,
        extract_task_final_text,
        extract_text_from_parts,
        map_a2a_state_to_done_status,
        normalize_progress_event,
        safe_float,
    )

    task = {
        "artifacts": [
            {"name": "final_answer", "parts": [{"kind": "text", "text": "final answer"}]},
            {"name": "telemetry", "parts": [{"kind": "data", "data": {"tokens": 9}}]},
        ],
        "status": {"message": {"parts": [{"kind": "text", "text": "task_completed"}]}},
    }

    assert safe_float("3.5") == 3.5
    assert safe_float("bad", default=2.0) == 2.0
    assert extract_text_from_parts([{"kind": "text", "text": "a"}, {"kind": "image", "text": "b"}]) == "a"
    assert extract_a2a_artifact(task, "telemetry") == {"tokens": 9}
    assert build_processing_steps_from_progress_events([{"detail": "search"}, {"event": "answer"}]) == ["search", "answer"]
    assert extract_reply_delta({"event": "answer_delta", "metadata": {"delta": "chunk"}}) == "chunk"
    assert normalize_progress_event({"event": "subagent_search", "detail": "retrieval"})["stage"] == "subagent"
    assert map_a2a_state_to_done_status("canceled") == "cancelled"
    assert extract_task_final_text(task, "completed") == "final answer"


async def test_a2a_event_stream_emits_job_reply_and_done(a2a_service_stub) -> None:
    from app.backend.services.a2a_event_stream_service import A2AEventStreamService

    a2a_service_stub.tasks["task-1"] = {
        "id": "task-1",
        "contextId": "session-1",
        "metadata": {"user_id": "user-1"},
        "status": {"state": "completed", "message": {"parts": [{"kind": "text", "text": "task_completed"}]}},
        "artifacts": [{"name": "final_answer", "parts": [{"kind": "text", "text": "final"}]}],
    }
    service = A2AEventStreamService(get_a2a=lambda: a2a_service_stub)

    events = [
        event
        async for event in service.generate_a2a_stream_events(
            input_text="question",
            user_id="user-1",
            session_id="session-1",
            request_id="request-1",
        )
    ]

    assert events[0]["type"] == "job"
    assert events[-1]["type"] == "done"
    assert events[-1]["status"] == "done"
    assert any(event["type"] == "reply" and event["msg"] == "final" for event in events)


async def test_agent_run_service_maps_completed_a2a_task(monkeypatch) -> None:
    from app.backend.services.agent_run_service import AgentRunService

    monkeypatch.setenv("AGENT_RUN_A2A_POLL_MS", "100")

    class FakeA2A:
        async def asend_message(self, message: dict[str, Any], request_id: str) -> dict[str, Any]:
            assert message["metadata"]["trace_id"] == request_id
            return {"id": "task-1"}

        async def aget_task(self, task_id: str) -> dict[str, Any]:
            return {
                "id": task_id,
                "status": {
                    "state": "completed",
                    "message": {"metadata": {"confidence": 0.8, "output_type": "answer"}, "parts": []},
                },
                "artifacts": [
                    {"name": "final_answer", "parts": [{"kind": "text", "text": "answer"}]},
                    {"name": "progress_events", "parts": [{"kind": "data", "data": [{"detail": "search"}]}]},
                    {"name": "tool_observations", "parts": [{"kind": "data", "data": [{"tool_name": "kg"}]}]},
                    {"name": "telemetry", "parts": [{"kind": "data", "data": {"llm_calls": 1}}]},
                ],
            }

    service = AgentRunService(get_a2a=lambda: FakeA2A(), log_api_error=log_api_error)

    assert service.resolve_agent_run_mode({}) == "auto"
    with pytest.raises(ValueError):
        service.resolve_agent_run_mode({"a2a_mode": "legacy"})

    result = await service.run_agent(
        input_text="question",
        session_id="session-1",
        user_id="user-1",
        strategy="iterative",
        max_turns=2,
        trace_id="trace-1",
        a2a_mode="auto",
    )

    assert result["ok"] is True
    assert result["answer"] == "answer"
    assert result["traces"] == [{"step": 1, "action": "search"}]
    assert result["a2a_task_id"] == "task-1"


async def test_sessions_history_service_validates_and_mutates(monkeypatch, temp_upload_dir, memory_service_stub) -> None:
    import app.backend.services.sessions_history_service as module
    from app.backend.services.sessions_history_service import SessionsHistoryService

    sessions: dict[str, dict[str, Any]] = {}
    messages: dict[str, list[dict[str, Any]]] = {}

    class FakeSessionManager:
        @staticmethod
        async def create_session(user_id: str, title: str) -> dict[str, Any]:
            session = {"id": "session-1", "user_id": user_id, "title": title}
            sessions[session["id"]] = session
            messages[session["id"]] = []
            return session

        @staticmethod
        async def get_sessions(user_id: str, limit: int) -> list[dict[str, Any]]:
            return [row for row in sessions.values() if row["user_id"] == user_id][:limit]

        @staticmethod
        async def get_session(session_id: str) -> dict[str, Any] | None:
            return sessions.get(session_id)

        @staticmethod
        async def update_session_title(session_id: str, title: str) -> bool:
            if session_id not in sessions:
                return False
            sessions[session_id]["title"] = title
            return True

        @staticmethod
        async def delete_session(session_id: str) -> bool:
            return sessions.pop(session_id, None) is not None

    class FakeMessageManager:
        @staticmethod
        async def add_message(session_id: str, role: str, content: str, think_content: str = "") -> dict[str, Any]:
            row = {"id": len(messages.setdefault(session_id, [])) + 1, "role": role, "content": content, "think_content": think_content}
            messages[session_id].append(row)
            return row

        @staticmethod
        async def get_messages(session_id: str) -> list[dict[str, Any]]:
            return list(messages.get(session_id, []))

        @staticmethod
        async def rollback_to_question(session_id: str, message_id: int) -> dict[str, Any]:
            rows = messages.get(session_id, [])
            kept = [row for row in rows if int(row["id"]) <= message_id]
            if not kept:
                return {"ok": False}
            removed = len(rows) - len(kept)
            messages[session_id] = kept
            return {"ok": True, "removed_count": removed, "question": kept[-1]["content"]}

    monkeypatch.setattr(module, "SessionManager", FakeSessionManager)
    monkeypatch.setattr(module, "MessageManager", FakeMessageManager)

    service = SessionsHistoryService(
        await_call=await_call,
        is_session_owner=lambda session_id, user_id: _is_session_owner(sessions, session_id, user_id),
        get_a2a=lambda: None,
        get_session_memory_service=lambda: memory_service_stub,
        upload_dir=temp_upload_dir,
        log_api_error=log_api_error,
    )

    async def fake_upload_to_aliyun_file_api(filename: str, content: bytes) -> dict[str, Any]:
        return {"id": f"file-{Path(filename).suffix.lower().lstrip('.')}", "purpose": "file-extract", "bytes": len(content)}

    monkeypatch.setattr(service, "_upload_to_aliyun_file_api", fake_upload_to_aliyun_file_api)

    created = await service.create_session({"user_id": "user-1", "title": "Title"})
    assert created["data"]["id"] == "session-1"
    assert (await service.list_sessions(user_id=""))["error"] == "user_id_required"
    assert (await service.update_session("session-1", {"user_id": "user-1", "title": "Renamed"}))["ok"] is True
    assert sessions["session-1"]["title"] == "Renamed"
    assert (await service.add_message("session-1", {"user_id": "user-1", "role": "user", "content": "q"}))["ok"] is True
    assert (await service.rollback_to_question("session-1", {"user_id": "user-1", "message_id": 1}))["data"]["memory_rebuilt"] is True
    assert (await service.add_message("session-1", {"user_id": "user-1", "role": "assistant", "content": "a", "think_content": "debug trace"}))["ok"] is True

    uploaded = await service.upload_file(upload_file("note.txt", b"hello"))
    assert uploaded["ok"] is True
    assert Path(temp_upload_dir / uploaded["data"]["filename"]).exists()
    assert uploaded["data"]["file_id"] == "file-txt"
    assert (await service.upload_file(upload_file("sheet.xlsx", b"fake-xlsx")))["ok"] is True
    assert (await service.upload_file(upload_file("bad.exe", b"x")))["ok"] is False

    session_detail = await service.get_session("session-1", user_id="user-1")
    assert session_detail["ok"] is True
    assert session_detail["data"]["messages"][0]["content"] == "q"
    assert "think_content" not in session_detail["data"]["messages"][1]
    assert (await service.get_session("session-1", user_id="other"))["error"] == "session_not_found"
    public_messages = await service.get_messages("session-1", user_id="user-1")
    assert public_messages["ok"] is True
    assert "think_content" not in public_messages["data"][1]
    assert (await service.get_messages("session-1", user_id=""))["error"] == "user_id_required"
    assert (await service.delete_session("session-1", user_id=""))["error"] == "user_id_required"
    assert (await service.delete_session("session-1", user_id="other"))["error"] == "session_not_found"

    class FakeA2A:
        async def aget_task(self, job_id: str) -> dict[str, Any] | None:
            if job_id == "missing":
                return None
            return {
                "contextId": "session-1",
                "metadata": {"user_id": "user-1"},
                "status": {"state": "running"},
            }

        async def acancel_task(self, job_id: str, reason: str) -> dict[str, Any]:
            return {"ok": True, "task": {"status": {"state": "canceled"}}}

    service._get_a2a = lambda: FakeA2A()
    assert (await service.cancel_qa_job("job-1", {"user_id": "", "session_id": ""}))["error"] == "user_id_and_session_id_required"
    assert (await service.cancel_qa_job("missing", {"user_id": "user-1", "session_id": "session-1"}))["error"] == "job_not_found"
    assert (await service.cancel_qa_job("job-1", {"user_id": "other", "session_id": "session-1"}))["error"] == "job_not_found"
    assert (await service.cancel_qa_job("job-1", {"user_id": "user-1", "session_id": "session-1"}))["ok"] is True
    assert (await service.delete_session("session-1", user_id="user-1"))["ok"] is True


async def test_documents_service_jobs_and_validation(monkeypatch) -> None:
    from app.backend.services.admin_documents_service import AdminDocumentsService

    service = AdminDocumentsService(
        await_call=await_call,
        get_shared_vector_store=lambda: object(),
        log_api_error=log_api_error,
    )

    assert service._validate_file(b"hello", "guide.md") == ".md"
    with pytest.raises(RuntimeError):
        service._validate_file(b"", "guide.md")
    with pytest.raises(RuntimeError):
        service._validate_file(b"hello", "guide.exe")

    async def fake_embed(file_bytes: bytes, filename: str, job_id: str) -> dict[str, Any]:
        await service._enter_job_stage(job_id, "embed", "running")
        await service._complete_job_stage(job_id, "done")
        return {"file_name": filename, "bytes": len(file_bytes)}

    monkeypatch.setattr(service, "_embed_text_document_job", fake_embed)

    submitted = await service.submit_text_embedding_job(upload_file("guide.txt", b"hello"))
    assert submitted["ok"] is True
    job_id = submitted["data"]["job_id"]
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    detail = await service.get_text_embedding_job(job_id)
    assert detail["ok"] is True
    assert detail["data"]["status"] in {"running", "succeeded"}
    jobs = await service.list_text_embedding_jobs(limit=1)
    assert jobs["data"][0]["job_id"] == job_id
    assert (await service.get_text_embedding_job("missing"))["ok"] is False

    class FakeEmbedder:
        def __init__(self, vector_store: Any) -> None:
            self.vector_store = vector_store

        def get_collection_stats(self) -> dict[str, Any]:
            return {"ok": True, "row_count": 12}

    fake_module = types.SimpleNamespace(DocumentEmbedder=FakeEmbedder)
    monkeypatch.setitem(sys.modules, "app.rag_core.document_embedding", fake_module)
    assert (await service.get_document_stats())["row_count"] == 12

    async def bad_store() -> Any:
        raise RuntimeError("store_failed")

    bad_service = AdminDocumentsService(
        await_call=await_call,
        get_shared_vector_store=bad_store,
        log_api_error=log_api_error,
    )
    assert (await bad_service.get_document_stats())["ok"] is False


async def test_graph_admin_service_crud_and_readonly_cypher() -> None:
    from app.backend.services.graph_admin_service import GraphAdminService

    class FakeNeo4j:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, Any] | None]] = []

        async def run_cypher(self, query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
            self.calls.append((query, params))
            if "RETURN DISTINCT label" in query:
                return [{"label": "Disease"}]
            if "RETURN DISTINCT type" in query:
                return [{"rel_type": "TREATS"}]
            if "RETURN count(n) AS total" in query:
                return [{"total": 1}]
            if "RETURN id(n) AS id, labels(n) AS labels, properties(n) AS properties" in query:
                return [{"id": 1, "labels": ["Disease"], "properties": {"name": "Hypertension"}}]
            if "CREATE (n" in query:
                return [{"id": 2}]
            if "SET " in query:
                return [{"id": params["node_id"], "properties": {"name": "Updated"}}]
            if "DETACH DELETE" in query:
                return [{"deleted_count": 1}]
            if "CREATE (source)-[r" in query:
                return [{"rel_id": 3}]
            if "DELETE r" in query:
                return [{"deleted_count": 1}]
            return [{"value": 1}]

    service = GraphAdminService(
        await_call=await_call,
        neo4j_client=FakeNeo4j(),
        get_shared_kg_milvus_store=lambda: object(),
        validate_cypher_identifier=lambda value: str(value) if str(value).replace("_", "").isalnum() else None,
        sanitize_graph_properties=lambda value: dict(value or {}),
        log_api_error=log_api_error,
    )

    assert (await service.get_graph_labels())["data"] == ["Disease"]
    assert (await service.get_graph_relationship_types())["data"] == ["TREATS"]
    assert (await service.get_graph_nodes(label="Disease", keyword="Hyper", limit=10, offset=0))["total"] == 1
    assert (await service.get_graph_node(1))["data"]["node"]["id"] == 1
    assert (await service.create_graph_node({"label": "Disease", "properties": {"name": "Hypertension"}}))["data"]["id"] == 2
    assert (await service.create_graph_node({"label": "", "properties": {}}))["ok"] is False
    assert (await service.update_graph_node(2, {"properties": {"name": "Updated"}}))["ok"] is True
    assert (await service.delete_graph_node(2))["ok"] is True
    assert (await service.create_graph_relationship({"source_id": "1", "target_id": "2", "rel_type": "TREATS"}))["data"]["rel_id"] == 3
    assert (await service.create_graph_relationship({"source_id": "x", "target_id": "2", "rel_type": "TREATS"}))["ok"] is False
    assert (await service.delete_graph_relationship(3))["ok"] is True
    assert (await service.execute_cypher({"query": "MATCH (n) RETURN n LIMIT 1"}))["ok"] is True
    assert (await service.execute_cypher({"query": "MATCH (n) DELETE n"}))["ok"] is False


async def test_graph_admin_service_job_helpers_and_cache(monkeypatch, tmp_path) -> None:
    from app.backend.services.graph_admin_service import GraphAdminService
    from app.backend.services.graph_extraction_lane_pool import (
        GraphExtractionLanePool,
    )

    class FakeNeo4j:
        async def run_cypher(self, query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
            _ = (query, params)
            return []

    service = GraphAdminService(
        await_call=await_call,
        neo4j_client=FakeNeo4j(),
        get_shared_kg_milvus_store=lambda: object(),
        validate_cypher_identifier=lambda value: str(value),
        sanitize_graph_properties=lambda value: dict(value or {}),
        log_api_error=log_api_error,
    )

    monkeypatch.setattr(GraphAdminService, "_get_graph_import_cache_dir", classmethod(lambda cls: tmp_path))
    file_hash = service._compute_file_hash(b"cache")
    prepared = {
        "file_name": "guide.txt",
        "parsed_text_length": 11,
        "total_chunk_chars": 11,
        "parser_used": "text",
        "chunk_count": 1,
        "chunk_limit": service.GRAPH_IMPORT_CHUNK_LIMIT,
        "chunk_strategy_version": service.GRAPH_IMPORT_CHUNK_STRATEGY_VERSION,
        "message": "ok",
    }
    service._write_graph_import_cache(file_hash, prepared, ["chunk"])
    loaded, reason = service._load_graph_import_cache(file_hash)
    assert reason is None
    assert loaded is not None
    assert loaded["_chunks"] == ["chunk"]
    assert service._load_graph_import_cache("missing")[0] is None

    assert service._json_loads('{"a":1}', {}) == {"a": 1}
    assert service._json_loads("bad", {"fallback": True}) == {"fallback": True}
    assert GraphExtractionLanePool.normalize_chunk_status("unknown") == "pending"
    assert service._format_resume_message(3)
    assert GraphExtractionLanePool.classify_lane_error("403 forbidden") == "permanent"
    assert GraphExtractionLanePool.count_active_lanes(
        [{"status": "ready"}, {"status": "failed"}]
    ) == 1
    assert GraphExtractionLanePool.count_viable_lanes(
        [{"status": "backoff"}, {"status": "failed"}]
    ) == 1
    assert (
        GraphExtractionLanePool.format_lane_label(
            {"lane_id": 1, "model_name": "m", "source": "s"}
        )
        == "lane1:m (s)"
    )

    job = await service._register_job("graph_extract_import", seed={"progress_text": "queued", "total_chunks": 2})
    job_id = job["job_id"]
    await service._enter_job_stage(job_id, "parse", "parsing")
    await service._heartbeat_job_stage(job_id, detail="working")
    await service._add_job_warning(job_id, "slow")
    await service._complete_job_stage(job_id, {"ok": True, "value": 1})
    await service._set_job_state(job_id, status="succeeded", result={"ok": True}, finished=True)
    assert (await service.get_graph_job(job_id))["data"]["status"] == "succeeded"
    assert (await service.list_graph_jobs(limit=5))["data"][0]["job_id"] == job_id
    assert (await service.get_graph_job("missing"))["ok"] is False

    job2 = await service._register_job("graph_embed_milvus")
    await service._set_job_state(job2["job_id"], status="failed", error="boom")
    failed = await service.get_graph_job(job2["job_id"])
    assert failed["data"]["error"] == "boom"


async def test_graph_public_service_visualization_and_detail() -> None:
    from app.backend.services.graph_public_service import GraphPublicService

    class FakeNeo4j:
        async def run_cypher(self, query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
            if "WHERE toString(id(n)) = $entity_id" in query:
                return [{"id": 1, "labels": ["Disease"], "name": "Hypertension", "props": {"name": "Hypertension", "code": "I10"}}]
            if "WHERE toString(id(b)) = $entity_id" in query:
                return [{"source_id": 2, "source_name": "Salt", "source_labels": ["Risk"], "rel_type": "CAUSES", "rel_props": {}}]
            if "WHERE toString(id(a)) = $entity_id" in query:
                return [{"target_id": 3, "target_name": "Stroke", "target_labels": ["Outcome"], "rel_type": "RAISES", "rel_props": {}}]
            return [
                {
                    "source_id": 1,
                    "target_id": 2,
                    "source_name": "Hypertension",
                    "target_name": "Stroke",
                    "source_labels": ["Disease"],
                    "target_labels": ["Outcome"],
                    "rel_type": "RAISES",
                }
            ]

    service = GraphPublicService(await_call=await_call, neo4j_client=FakeNeo4j(), log_api_error=log_api_error)
    graph = await service.graph_visualization(keyword="", limit=10)
    assert graph["ok"] is True
    assert graph["count"] == 1

    detail = await service.get_entity_detail("1")
    assert detail["ok"] is True
    assert detail["entity"]["properties"] == {"code": "I10"}
    assert len(detail["relationships"]["incoming"]) == 1
    assert (await service.get_entity_detail("bad"))["ok"] is False


async def test_observability_service_aggregates_runs(monkeypatch) -> None:
    from app.backend.services.admin_observability_service import AdminObservabilityService

    service = AdminObservabilityService(log_api_error=log_api_error)
    events = [
        {
            "session_id": "s1",
            "duration_ms": "100.5",
            "llm_calls": "2",
            "tool_calls": 4,
            "attempted_tool_actions": 5,
            "blocked_tool_actions": 1,
            "tool_success_rate": 0.5,
            "total_tokens": "90",
            "error_types": {"planner_error": 1, "tool_error": 2},
            "strategy_error_types": {"planner_error": 1},
            "execution_error_types": {"tool_error": 2},
        }
    ]
    monkeypatch.setattr(service, "_read_turn_logs", lambda limit=200: events[:limit])

    summary = await service.admin_agent_observability_summary()
    assert summary["data"]["total_runs"] == 1
    assert summary["data"]["strategy_error_types"] == {"planner_error": 1}
    assert summary["data"]["execution_error_types"] == {"tool_error": 2}

    runs = await service.admin_agent_observability_runs(limit=1)
    assert runs["count"] == 1
    assert runs["data"][0]["rank"] == 1


async def test_observability_service_reads_perf_and_turn_logs(monkeypatch, tmp_path) -> None:
    from app.backend.services.admin_observability_service import AdminObservabilityService

    service = AdminObservabilityService(log_api_error=log_api_error)
    logs_dir = tmp_path / "logs"
    sessions_turn_dir = logs_dir / "sessions" / "session-1" / "turns"
    sessions_turn_dir.mkdir(parents=True)
    (logs_dir / "perf.log").write_text(
        '\n'.join(
            [
                'INFO [性能事件] {"event":"iterative_agent_run","session_id":"s1","duration_ms":12,"tool_success_rate":1}',
                'INFO [性能事件] {"event":"other","session_id":"s2"}',
            ]
        ),
        encoding="utf-8",
    )
    turn_file = sessions_turn_dir / "turn-1.jsonl"
    turn_file.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "record_type": "turn_observability",
                        "turn_id": "t1",
                        "session_id": "s1",
                        "duration_ms": 20,
                        "tool_success_rate": 0.5,
                        "finished_at": "2026-01-01T00:00:00",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "record_type": "turn_memory_update",
                        "turn_id": "t1",
                        "llm_calls": 3,
                        "total_tokens": 90,
                        "error_types": {"planner_error": 1},
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "record_type": "turn_observability",
                        "turn_id": "t2",
                        "session_id": "s2",
                        "finished_at": "2026-01-01T00:01:00",
                        "llm_calls": 0,
                        "total_tokens": 0,
                        "agent_lanes": {
                            "主控编排": [
                                {
                                    "title": "主控统一决策",
                                    "full_decision": json.dumps({"total_tokens": 128}, ensure_ascii=False),
                                }
                            ]
                        },
                    },
                    ensure_ascii=False,
                ),
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(service, "_project_root", staticmethod(lambda: tmp_path))
    perf_rows = service._read_perf_event_logs("iterative_agent_run", limit=5)
    assert perf_rows[0]["session_id"] == "s1"
    turn_rows = service._read_turn_logs(limit=5)
    assert turn_rows[0]["turn_id"] == "t2"
    assert turn_rows[0]["llm_calls"] == 1
    assert turn_rows[0]["total_tokens"] == 128
    assert turn_rows[1]["turn_id"] == "t1"
    assert turn_rows[1]["llm_calls"] == 3


async def test_admin_runtime_service_cancel_and_stats(monkeypatch, a2a_service_stub) -> None:
    import app.backend.services.admin_runtime_service as module
    from app.backend.services.admin_runtime_service import AdminRuntimeService

    async def fake_fetch_one(statement: str, params: dict[str, Any] | None = None) -> dict[str, int]:
        if "messages" in statement:
            return {"count": 4}
        return {"count": 2}

    monkeypatch.setattr(module, "fetch_one", fake_fetch_one)

    service = AdminRuntimeService(
        measure_event_loop_lag=_measure_event_loop_lag,
        build_a2a_runtime_snapshot=_build_runtime_snapshot,
        get_a2a=lambda: a2a_service_stub,
    )

    flags = await service.admin_runtime_flags()
    assert flags["ok"] is True
    assert "updated_at" in flags["data"]
    assert (await service.admin_qa_queue_status())["data"]["orchestrator"]["tasks"] == 1
    cancelled = await service.admin_cancel_qa_job("task-1")
    assert cancelled["ok"] is True
    assert cancelled["status"] == "canceled"
    assert (await service.admin_cancel_qa_job("missing"))["error"] == "job_not_found_or_finished"
    stats = await service.admin_get_stats()
    assert stats["data"]["total_sessions"] == 2
    assert stats["data"]["total_messages"] == 4


async def _is_session_owner(sessions: dict[str, dict[str, Any]], session_id: str, user_id: str) -> bool:
    return sessions.get(session_id, {}).get("user_id") == user_id


async def _measure_event_loop_lag(**kwargs) -> dict[str, Any]:
    return {"p95": 1.2}


async def _build_runtime_snapshot(**kwargs) -> dict[str, Any]:
    return {"orchestrator": {"tasks": 1}}


async def test_readiness_service_reports_ready_degraded_and_unavailable(monkeypatch) -> None:
    import app.backend.services.readiness_service as module
    from app.backend.services.readiness_service import ReadinessService

    fake_mcp_module = types.SimpleNamespace(get_mcp_runtime_status=lambda mcp: {"core_ready": True, "optional_issues": [], "tool_names": [tool["name"] for tool in mcp.list_tools()]})
    monkeypatch.setitem(sys.modules, "app.mcp.tools", fake_mcp_module)

    class McpStub:
        def list_tools(self) -> list[dict[str, Any]]:
            return [{"name": "search"}]

    monkeypatch.setattr(module, "check_databases", lambda: {"all_running": True, "neo4j": {"is_running": True, "host": "127.0.0.1"}, "milvus": {"is_running": True, "host": "127.0.0.1"}})
    service = ReadinessService(
        get_mcp=lambda: McpStub(),
        mysql_initialized_resolver=lambda: True,
        log_api_error=log_api_error,
        await_call=await_call,
    )
    ready = await service.build_readiness_report()
    assert ready["level"] == "ready"
    assert await service.ensure_core_ready("/process") is None

    monkeypatch.setattr(module, "check_databases", lambda: {"all_running": False, "neo4j": {"is_running": False, "host": "127.0.0.1"}, "milvus": {"is_running": True, "host": "127.0.0.1"}})
    degraded = await service.build_readiness_report()
    assert degraded["level"] == "degraded"

    unavailable_service = ReadinessService(
        get_mcp=lambda: McpStub(),
        mysql_initialized_resolver=lambda: False,
        log_api_error=log_api_error,
        await_call=await_call,
    )
    unavailable = await unavailable_service.build_readiness_report()
    assert unavailable["level"] == "unavailable"
    blocked = await unavailable_service.ensure_core_ready("/process")
    assert blocked is not None
    assert blocked.status_code == 503


async def test_mcp_server_degrades_when_milvus_tools_fail(monkeypatch) -> None:
    import app.mcp.tools as module

    class MilvusLikeError(Exception):
        """模拟 pymilvus 抛出的外部依赖异常。"""

    class BrokenRagSearchTool:
        def __init__(self, conf: Any) -> None:
            raise MilvusLikeError("milvus unavailable")

    class BrokenKgEntityRelationSearchTool:
        def __init__(self, conf: Any) -> None:
            raise MilvusLikeError("kg milvus unavailable")

    await module.close_mcp_server()
    monkeypatch.setattr(module, "RagSearchTool", BrokenRagSearchTool)
    monkeypatch.setattr(module, "KgEntityRelationSearchTool", BrokenKgEntityRelationSearchTool)

    server = module.build_mcp_server()
    try:
        tool_names = {row["name"] for row in server.list_tools()}
        assert {"kg_query", "rag_search", "kg_entity_relation_search", "history_read"} <= tool_names

        rag_result = await server.ainvoke("rag_search", {"query": "高血压", "top_k": 1})
        assert rag_result["ok"] is True
        assert rag_result["result"]["unavailable"] is True

        status = await module.get_mcp_runtime_status(server)
        assert status["core_ready"] is False
        assert "rag_search" in status["runtime_unavailable_required"]
        assert "kg_entity_relation_search" in status["runtime_unavailable_optional"]
    finally:
        await server.aclose()


async def test_session_turn_service_creates_titles_and_recovers_from_memory_errors() -> None:
    from app.backend.services.session_turn_service import SessionTurnService

    sessions: dict[str, dict[str, Any]] = {}
    messages: dict[str, list[dict[str, Any]]] = {}

    class FakeSessionManager:
        @staticmethod
        async def create_session(user_id: str, title: str) -> dict[str, Any]:
            session = {"id": "session-new", "user_id": user_id, "title": title}
            sessions[session["id"]] = session
            return session

        @staticmethod
        async def get_session(session_id: str) -> dict[str, Any] | None:
            return sessions.get(session_id)

        @staticmethod
        async def update_session_title(session_id: str, title: str) -> bool:
            sessions[session_id]["title"] = title
            return True

    class FakeMessageManager:
        @staticmethod
        async def count_messages(session_id: str) -> int:
            return len(messages.get(session_id, []))

        @staticmethod
        async def add_message(session_id: str, role: str, content: str) -> dict[str, Any]:
            row = {"id": len(messages.setdefault(session_id, [])) + 1, "role": role, "content": content}
            messages[session_id].append(row)
            return row

    class BrokenMemoryService:
        async def build_runtime_context(self, session_id: str, input_text: str, current_message_id: int) -> dict[str, Any]:
            _ = (session_id, input_text, current_message_id)
            raise RuntimeError("memory_failed")

    service = SessionTurnService(
        await_call=await_call,
        get_session_memory_service=lambda: BrokenMemoryService(),
        log_api_error=log_api_error,
        message_manager=FakeMessageManager,
        session_manager=FakeSessionManager,
    )

    prepared = await service.prepare_session_turn(
        input_text="高血压患者需要注意哪些生活方式？",
        session_id="",
        user_id="user-1",
        file_name="guide.pdf",
    )
    assert prepared["ok"] is True
    assert prepared["session_id"] == "session-new"
    assert messages["session-new"][0]["content"].startswith("[文件:guide.pdf]")
    assert prepared["memory_context"] == {}

    sessions["owned-session"] = {"id": "owned-session", "user_id": "user-1", "title": "新会话"}
    messages["owned-session"] = []
    reused = await service.prepare_session_turn(
        input_text="第二问",
        session_id="owned-session",
        user_id="user-1",
    )
    assert reused["ok"] is True
    assert sessions["owned-session"]["title"] == "第二问"

    missing = await service.prepare_session_turn(
        input_text="bad",
        session_id="missing",
        user_id="user-1",
    )
    assert missing["ok"] is False
    assert missing["error"] == "session_not_found"


async def test_a2a_runtime_service_validates_identifiers_and_tracks_shutdown() -> None:
    from app.backend.config import build_backend_config
    from app.backend.infrastructure.runtime_container import BackendRuntimeContainer
    from app.backend.services.a2a_runtime_service import A2ARuntimeService

    class FakeA2A:
        def __init__(self, rows: list[dict[str, Any]]) -> None:
            self._rows = rows
            self.closed = False

        async def alist_tasks(self, limit: int = 200) -> list[dict[str, Any]]:
            _ = limit
            return self._rows

        async def ashutdown(self) -> None:
            self.closed = True

        def get_agent_card(self) -> dict[str, Any]:
            return {"name": "fake"}

    runtime = BackendRuntimeContainer(config=build_backend_config())
    runtime.a2a_service = FakeA2A([{"status": {"state": "running"}}])
    runtime.a2a_graph_service = FakeA2A([{"status": {"state": "completed"}}])
    runtime.a2a_retrieval_service = FakeA2A([{"status": {"state": "failed"}}])
    service = A2ARuntimeService(runtime_container=runtime, log_api_error=log_api_error)

    assert service.validate_cypher_identifier("valid_name") == "valid_name"
    assert service.validate_cypher_identifier("123bad") is None
    assert service.sanitize_graph_properties({"name": "x"}) == {"name": "x"}
    with pytest.raises(ValueError):
        service.sanitize_graph_properties({"bad-key": "x"})

    snapshot = await service.build_a2a_runtime_snapshot(limit_per_service=10)
    assert snapshot["recent_total_tasks"] == 3
    assert snapshot["services"]["orchestrator"]["states"]["running"] == 1

    await service.shutdown_all()
    assert runtime.a2a_service is None
    assert runtime.a2a_graph_service is None
    assert runtime.a2a_retrieval_service is None

    await service.shutdown_all()
    lag = await service.measure_event_loop_lag(samples=1, interval_ms=0)
    assert lag["samples"] == 5
    assert lag["interval_ms"] == 1


async def test_a2a_runtime_service_lazy_init_and_subagent_queries(monkeypatch) -> None:
    import app.a2a as a2a_module
    import app.a2a.agent as agent_module
    from app.backend.config import build_backend_config
    from app.backend.infrastructure.runtime_container import BackendRuntimeContainer
    from app.backend.services.a2a_runtime_service import A2ARuntimeService

    created: list[dict[str, Any]] = []

    class FakeA2AService:
        def __init__(self, **kwargs: Any) -> None:
            created.append(kwargs)
            self.kwargs = kwargs

        async def alist_tasks(self, limit: int = 200) -> list[dict[str, Any]]:
            _ = limit
            return []

        async def ashutdown(self) -> None:
            return None

    class FakeSubagent:
        def __init__(self, signal_count: int) -> None:
            self.signal_count = signal_count
            self.requests: list[dict[str, Any]] = []

        async def invoke_batch(self, **kwargs: Any):
            self.requests = kwargs["requests"]
            return (
                {
                    "result": {
                        "summary": {
                            "signal_count": self.signal_count,
                            "total_requests": len(self.requests),
                            "ok_count": self.signal_count,
                            "note": "",
                        },
                        "items": [{"ok": True}],
                    }
                },
                {"source": "fake"},
            )

    async def fake_process_query(**kwargs: Any) -> dict[str, Any]:
        return {
            "output": kwargs["input_text"],
            "session_id": kwargs["session_id"],
            "user_id": kwargs["user_id"],
            "strategy": kwargs["strategy"],
            "memory_context": kwargs["memory_context"],
        }

    monkeypatch.setattr(a2a_module, "A2AService", FakeA2AService)
    monkeypatch.setattr(agent_module, "process_query", fake_process_query)

    runtime = BackendRuntimeContainer(config=build_backend_config())
    runtime.graph_subagent = FakeSubagent(signal_count=1)
    runtime.rag_subagent = FakeSubagent(signal_count=0)
    service = A2ARuntimeService(runtime_container=runtime, log_api_error=log_api_error)

    assert service.get_a2a() is service.get_a2a()
    assert service.get_a2a_graph() is service.get_a2a_graph()
    assert service.get_a2a_retrieval() is service.get_a2a_retrieval()
    assert {item["agent_url"] for item in created} == {"/a2a/rpc", "/a2a/graph/rpc", "/a2a/retrieval/rpc"}

    main_result = await service._run_a2a_query(
        "question",
        metadata={"session_id": "s1", "user_id": "u1", "strategy": "iterative", "memory_context": {"qa_pairs": []}},
    )
    assert main_result["session_id"] == "s1"
    assert main_result["user_id"] == "u1"

    progress_events: list[dict[str, Any]] = []
    graph_result = await service._run_graph_a2a_query(
        "graph question",
        context_id="s2",
        user_id="u2",
        metadata={"requests": [{"subquery": "kg", "arguments": {"k": 1}}]},
        progress_callback=progress_events.append,
    )
    assert graph_result["output_type"] == "graph_subagent_batch"
    assert graph_result["confidence"] == 0.75
    assert progress_events[0]["event"] == "graph_batch_start"
    assert runtime.graph_subagent.requests[0]["tool_name"] == "kg_auto"

    retrieval_result = await service._run_retrieval_a2a_query(
        "rag question",
        metadata={"subquery": "rag", "arguments": {"top_k": 3}},
        progress_callback=progress_events.append,
    )
    assert retrieval_result["output_type"] == "retrieval_subagent_batch"
    assert retrieval_result["confidence"] == 0.42
    assert runtime.rag_subagent.requests[0]["tool_name"] == "rag_search"


async def test_kg_subagent_selects_mcp_tool_internally() -> None:
    from app.a2a.subagents.kg.nodes import node_generate_query_statements

    complex_state = {
        "input_question": "高血压合并 CKD 且有蛋白尿时优先考虑哪些药物类别和代表药？",
        "subtask_id": 1,
        "constraints": {"allow_extra_retrieval": False, "max_tool_calls": 3},
        "tool_name": "kg_auto",
        "arguments": {},
        "query_terms": [],
        "attempt_count": 0,
        "tool_results": [],
    }
    complex_output = await node_generate_query_statements(complex_state)
    complex_tools = [row["tool_name"] for row in complex_output["query_statements"]]
    assert complex_tools == ["kg_entity_relation_search", "kg_query"]

    exact_state = {
        "input_question": "列出所有高血压相关节点",
        "subtask_id": 1,
        "constraints": {"allow_extra_retrieval": False, "max_tool_calls": 3},
        "tool_name": "kg_auto",
        "arguments": {},
        "query_terms": [],
        "attempt_count": 0,
        "tool_results": [],
    }
    exact_output = await node_generate_query_statements(exact_state)
    assert exact_output["query_statements"][0]["tool_name"] == "kg_query"


async def test_orchestrator_hides_mcp_tool_selection_from_model() -> None:
    from app.a2a.client.subagent_client import SubAgentClientFactory
    from app.a2a.orchestrator.controller import UnifiedOrchestratorController

    class FakeLlmClient:
        async def aclose(self) -> None:
            return None

    controller = UnifiedOrchestratorController(
        SubAgentClientFactory(graph_subagent=None, rag_subagent=None),
        llm_client=FakeLlmClient(),
    )
    prompt = controller._build_system_prompt()
    assert "kg_query" not in prompt
    assert "kg_entity_relation_search" not in prompt
    assert "history_read" not in prompt
    assert "\"arguments\"" not in prompt
    task = controller._build_subtask(
        action={
            "action": "call_kg",
            "question": "高血压合并 CKD 和蛋白尿怎么用药？",
            "tool_name": "kg_query",
            "arguments": {"cypher": "MATCH (n) RETURN n"},
        },
        default_question="默认问题",
        max_tool_calls=3,
    )
    assert task.tool_name == "kg_auto"
    assert task.arguments == {}


async def test_startup_runtime_service_respects_behavior_flags(monkeypatch) -> None:
    import app.a2a.agent as agent_module
    import app.mcp.tools as mcp_tools_module
    from app.backend.config import build_backend_config
    from app.backend.infrastructure.runtime_container import BackendRuntimeContainer
    from app.backend.services.startup_runtime_service import StartupRuntimeBehavior, StartupRuntimeService

    events: list[str] = []

    class FakeA2ARuntime:
        def get_mcp(self):
            events.append("mcp")

            class Mcp:
                def list_tools(self):
                    return [{"name": "tool", "description": "desc"}]

            return Mcp()

        def get_a2a(self):
            events.append("a2a")

            class Agent:
                def get_agent_card(self):
                    return {"name": "agent", "protocolVersion": "0.2.5"}

            return Agent()

        def get_a2a_graph(self):
            events.append("graph")

            class Agent:
                def get_agent_card(self):
                    return {"name": "graph", "protocolVersion": "0.2.5"}

            return Agent()

        def get_a2a_retrieval(self):
            events.append("retrieval")

            class Agent:
                def get_agent_card(self):
                    return {"name": "retrieval", "protocolVersion": "0.2.5"}

            return Agent()

        async def shutdown_all(self):
            events.append("shutdown")

    class FakeNeo4j:
        async def close(self):
            events.append("neo4j_close")

    async def init_db() -> bool:
        return True

    async def check_db() -> dict[str, bool]:
        return {"all_running": True}

    async def close_iterative_agent() -> None:
        events.append("agent_close")

    async def close_mcp_server() -> None:
        events.append("global_mcp_close")

    class RuntimeMcp:
        async def aclose(self):
            events.append("runtime_mcp_close")

    monkeypatch.setattr(agent_module, "close_iterative_agent", close_iterative_agent)
    monkeypatch.setattr(mcp_tools_module, "close_mcp_server", close_mcp_server)

    runtime = BackendRuntimeContainer(config=build_backend_config())
    runtime.mcp_server = RuntimeMcp()
    service = StartupRuntimeService(
        runtime_container=runtime,
        a2a_runtime=FakeA2ARuntime(),
        log_api_error=log_api_error,
        behavior=StartupRuntimeBehavior(
            initialize_database=True,
            check_databases=True,
            prewarm_retrieval_resources=True,
            bootstrap_mcp=True,
            bootstrap_a2a=True,
            close_neo4j_on_shutdown=True,
        ),
        database_initializer=init_db,
        database_checker=check_db,
        get_shared_vector_store=lambda: events.append("vector"),
        get_shared_kg_milvus_store=lambda: types.SimpleNamespace(ensure_embedder_loaded=lambda: events.append("kg")),
        neo4j_client=FakeNeo4j(),
    )

    await service.startup()
    assert runtime.mysql_initialized is True
    assert {"vector", "kg", "mcp", "a2a", "graph", "retrieval"}.issubset(set(events))
    await service.shutdown()
    assert events[-5:] == ["shutdown", "agent_close", "runtime_mcp_close", "global_mcp_close", "neo4j_close"]
    assert runtime.mcp_server is None


async def test_session_memory_service_builds_runtime_and_refreshes(monkeypatch) -> None:
    import app.backend.services.session_memory_service as module
    from app.backend.services.session_memory_service import SessionMemoryService

    messages = [
        {"id": 1, "role": "user", "content": "第一问"},
        {"id": 2, "role": "assistant", "content": "第一答"},
        {"id": 3, "role": "user", "content": "第二问"},
        {"id": 4, "role": "assistant", "content": "第二答"},
    ]
    stored_rows: dict[str, Any] = {}

    async def fake_fetch_one(statement, params=None):
        _ = params
        if "FROM session_memory" not in statement:
            return None
        return {
            "summary_text": stored_rows.get("summary_text", ""),
            "retrieval_summary_text": stored_rows.get("retrieval_summary_text", ""),
            "updated_at": None,
        }

    async def fake_fetch_all(statement, params=None):
        _ = (statement, params)
        return messages

    async def fake_upsert_summary(session_id: str, summary_text: str):
        stored_rows["session_id"] = session_id
        stored_rows["summary_text"] = summary_text
        return True

    async def fake_upsert_retrieval_summary(session_id: str, retrieval_summary_text: str):
        stored_rows["session_id"] = session_id
        stored_rows["retrieval_summary_text"] = retrieval_summary_text
        return True

    async def fake_clear_summary(session_id: str):
        _ = session_id
        stored_rows.clear()
        return True

    monkeypatch.setattr(module, "fetch_one", fake_fetch_one)
    monkeypatch.setattr(module, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(module.SessionMemoryManager, "upsert_summary", fake_upsert_summary)
    monkeypatch.setattr(module.SessionMemoryManager, "upsert_retrieval_summary", fake_upsert_retrieval_summary)
    monkeypatch.setattr(module.SessionMemoryManager, "clear_summary", fake_clear_summary)

    service = SessionMemoryService()
    runtime_payload = await service.build_runtime_context("session-1", "第二问", 3)
    assert runtime_payload["original_input_text"] == "第二问"
    assert runtime_payload["memory_context"]["qa_pairs"][0]["user_question"] == "第一问"

    refreshed = await service.refresh_after_turn(
        "session-1",
        4,
        {
            "user_question": "第二问",
            "answer_digest": "第二答",
            "supported_points": ["控制盐摄入"],
            "retrieval_queries": ["高血压 限盐"],
            "retrieval_hits": ["指南 A"],
        },
    )
    assert refreshed["qa_memory"]["qa_pairs"][-1]["assistant_answer"] == "第二答"
    rebuilt = await service.rebuild_from_messages("session-1")
    assert rebuilt["qa_memory"]["qa_pairs"][0]["user_question"] == "第一问"
    await service.clear_memory("session-1")
    assert stored_rows == {}


async def test_session_memory_service_sanitizes_and_reads_history(monkeypatch) -> None:
    from datetime import datetime

    from app.backend.services.session_memory_service import SessionMemoryService

    service = SessionMemoryService()

    assert service._sanitize_list(["a", "a", "", "b"], max_items=3, max_len=10) == ["a", "b"]
    assert service._sanitize_list("bad", max_items=3, max_len=10) == []
    assert service._parse_qa_memory("not-json")["qa_pairs"] == []
    assert service._parse_retrieval_memory("")["questions"] == []
    assert service._sanitize_qa_entry({"user_question": "", "assistant_answer": "a"}) is None
    assert service._sanitize_retrieval_record({"query": "q", "result_content": ""}) is None
    assert service._sanitize_retrieval_entry({"history_id": "h1", "user_question": "q", "records": []}) is None

    row_snapshot = {
        "summary_text": json.dumps(
            {
                "version": 1,
                "last_message_id": 2,
                "qa_pairs": [
                    {
                        "user_question": "What is BP?",
                        "assistant_answer": "Blood pressure",
                        "user_message_id": 1,
                        "assistant_message_id": 2,
                        "created_at": "2026-04-24",
                    }
                ],
            }
        ),
        "retrieval_summary_text": json.dumps(
            {
                "questions": [
                    {
                        "history_id": "h-1-2",
                        "user_question": "hypertension diet",
                        "created_at": "2026-04-24",
                        "records": [
                            {
                                "agent": "rag",
                                "query": "salt",
                                "result_content": "reduce sodium",
                                "captured_at": "2026-04-24",
                                "citations": ["guide", "guide"],
                                "page_num": "3",
                                "chunk_id": "chunk-1",
                            }
                        ],
                    }
                ]
            }
        ),
        "updated_at": datetime(2026, 4, 24, 10, 0, 0),
    }

    async def fake_load_row(session_id: str) -> dict[str, Any]:
        assert session_id == "session-1"
        return row_snapshot

    monkeypatch.setattr(service, "_load_memory_row", fake_load_row)
    loaded = await service.load_memory("session-1")
    assert loaded["updated_at"].startswith("2026-04-24T10:00:00")
    assert loaded["qa_memory"]["version"] == 2
    assert loaded["retrieval_memory"]["questions"][0]["records"][0]["page_num"] == 3

    history_by_id = await service.read_retrieval_history(session_id="session-1", history_id="h-1-2")
    assert history_by_id["matched"] is True
    assert history_by_id["records"][0]["result_content"] == "reduce sodium"

    history_by_question = await service.read_retrieval_history(session_id="session-1", user_question="hypertension food")
    assert history_by_question["matched"] is True

    missing = await service.read_retrieval_history(session_id="session-1", user_question="unrelated")
    assert missing["matched"] is False


async def test_session_memory_service_refresh_appends_query_records(monkeypatch) -> None:
    from app.backend.services.session_memory_service import SessionMemoryService

    service = SessionMemoryService()
    saved: dict[str, Any] = {}

    async def fake_load_memory(session_id: str) -> dict[str, Any]:
        _ = session_id
        return {
            "qa_memory": {"version": 2, "last_message_id": 0, "qa_pairs": []},
            "retrieval_memory": {"version": 2, "last_message_id": 0, "questions": []},
            "updated_at": "",
        }

    async def fake_get_messages(session_id: str) -> list[dict[str, Any]]:
        _ = session_id
        return [
            {"id": 10, "role": "user", "content": "How much salt?", "created_at": "2026-04-24"},
            {"id": 11, "role": "assistant", "content": "Limit sodium.", "created_at": "2026-04-24"},
        ]

    async def fake_save_memory(session_id: str, snapshot: dict[str, Any]) -> None:
        saved["session_id"] = session_id
        saved["snapshot"] = snapshot

    monkeypatch.setattr(service, "load_memory", fake_load_memory)
    monkeypatch.setattr(service, "_get_messages", fake_get_messages)
    monkeypatch.setattr(service, "save_memory", fake_save_memory)

    refreshed = await service.refresh_after_turn(
        "session-1",
        11,
        {
            "user_question": "How much salt?",
            "user_message_id": 10,
            "captured_at": "2026-04-24T10:00:00",
            "query_records": [
                {
                    "agent": "rag",
                    "query": "salt intake",
                    "result_content": "keep sodium low",
                    "captured_at": "2026-04-24T10:00:00",
                    "citations": ["guide"],
                }
            ],
        },
    )

    assert saved["session_id"] == "session-1"
    assert refreshed["retrieval_memory"]["questions"][0]["history_id"] == "h-10-11"
    assert refreshed["retrieval_memory"]["questions"][0]["records"][0]["query"] == "salt intake"


async def test_admin_sessions_service_builds_flow_events_and_cleans_logs(monkeypatch, tmp_path, memory_service_stub) -> None:
    import app.backend.services.admin_sessions_service as module
    from app.backend.services.admin_sessions_service import AdminSessionsService

    session_id = "session-observe"
    logs_root = tmp_path / "logs" / "sessions" / session_id
    turns_dir = logs_root / "turns"
    llm_dir = logs_root / "llm"
    tools_dir = logs_root / "tools"
    turns_dir.mkdir(parents=True)
    llm_dir.mkdir(parents=True)
    tools_dir.mkdir(parents=True)

    (turns_dir / "2026-04-23.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "record_type": "turn_observability",
                        "turn_id": "turn-1",
                        "request_id": "req-1",
                        "user_message_id": 1,
                        "assistant_message_id": 2,
                        "question": "高血压如何控制饮食",
                        "answer_digest": "控制盐摄入",
                        "started_at": "2026-04-23T10:00:00",
                        "finished_at": "2026-04-23T10:00:05",
                        "status": "done",
                        "duration_ms": 5000,
                        "llm_calls": 1,
                        "total_tokens": 90,
                        "tool_observations": [
                            {"agent": "kg", "tool_name": "kg_entity_relation_search", "subquery": "高血压", "result": {"ok": True, "result": {"summary": "命中实体"}}}
                        ],
                        "agent_lanes": {},
                        "memory_compression": [],
                        "timeline": [],
                        "orchestrator_decision": {"controller_mode": "explicit_memory_with_history_read"},
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "record_type": "turn_memory_update",
                        "turn_id": "turn-1",
                        "updated_at": "2026-04-23T10:00:06",
                        "llm_calls": 2,
                        "total_tokens": 120,
                        "snapshot": {
                            "qa_memory": {"qa_pairs": [{"user_question": "高血压如何控制饮食"}]},
                            "retrieval_memory": {"questions": ["高血压 限盐"]},
                        },
                    },
                    ensure_ascii=False,
                ),
            ]
        ),
        encoding="utf-8",
    )
    (llm_dir / "2026-04-23.jsonl").write_text(
        json.dumps(
            {
                "request_id": "req-1",
                "ts": "2026-04-23T10:00:02",
                "actor_role": "controller",
                "messages_compact": "主控agent 输入",
                "response_compact": '{"action":"call_kg"}',
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (tools_dir / "2026-04-23.jsonl").write_text(
        json.dumps(
            {
                "request_id": "req-1",
                "ts": "2026-04-23T10:00:03",
                "tool_name": "kg_entity_relation_search",
                "arguments": {"query": "高血压"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(module.AdminSessionsService, "_project_root", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(module.SessionManager, "get_all_sessions", lambda limit, offset: [{"id": session_id, "title": "标题", "message_count": 2}])
    monkeypatch.setattr(module.SessionManager, "get_total_sessions_count", lambda: 1)
    monkeypatch.setattr(module.SessionManager, "get_session", lambda sid: {"id": sid, "title": "标题"} if sid == session_id else None)
    monkeypatch.setattr(module.SessionManager, "update_session_time", lambda sid: True)
    monkeypatch.setattr(module.SessionManager, "delete_session", lambda sid: sid == session_id)
    monkeypatch.setattr(module.MessageManager, "get_messages", lambda sid: [{"id": 1, "content": "问"}, {"id": 2, "content": "答"}])
    monkeypatch.setattr(module.MessageManager, "delete_messages", lambda sid: True)
    service = AdminSessionsService(await_call=await_call, get_session_memory_service=lambda: memory_service_stub)
    sessions = await service.admin_get_sessions(limit=20, offset=0)
    assert sessions["total"] == 1

    observability = await service.admin_get_session_observability(session_id)
    assert observability["ok"] is True
    assert observability["turns"][0]["flow_events"]

    cleared = await service.admin_clear_session_messages(session_id)
    assert cleared["ok"] is True
    assert cleared["log_artifacts_deleted"] is True

    deleted = await service.admin_delete_session(session_id)
    assert deleted["ok"] is True


async def test_admin_sessions_service_memory_and_missing_session_paths(monkeypatch, tmp_path) -> None:
    import app.backend.services.admin_sessions_service as module
    from app.backend.services.admin_sessions_service import AdminSessionsService

    class MemoryService:
        def __init__(self) -> None:
            self.cleared: list[str] = []
            self.rebuilt: list[str] = []

        async def load_memory(self, session_id: str) -> dict[str, Any]:
            return {"session_id": session_id, "qa_memory": {"qa_pairs": []}}

        async def clear_memory(self, session_id: str) -> None:
            self.cleared.append(session_id)

        async def rebuild_from_messages(self, session_id: str) -> dict[str, Any]:
            self.rebuilt.append(session_id)
            return {"session_id": session_id, "rebuilt": True}

    memory = MemoryService()
    sessions = {"session-1": {"id": "session-1", "title": "Session"}}
    messages = {"session-1": [{"id": 1, "role": "user", "content": "hello"}]}

    monkeypatch.setattr(module.AdminSessionsService, "_project_root", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(module.SessionManager, "get_session", lambda sid: sessions.get(sid))
    monkeypatch.setattr(module.SessionManager, "update_session_time", lambda sid: True)
    monkeypatch.setattr(module.SessionManager, "delete_session", lambda sid: sessions.pop(sid, None) is not None)
    monkeypatch.setattr(module.MessageManager, "get_messages", lambda sid: list(messages.get(sid, [])))
    monkeypatch.setattr(module.MessageManager, "delete_messages", lambda sid: messages.update({sid: []}) or True)

    service = AdminSessionsService(await_call=await_call, get_session_memory_service=lambda: memory)

    assert (await service.admin_get_session_messages("missing"))["ok"] is False
    assert (await service.admin_get_session_memory("missing"))["ok"] is False
    assert (await service.admin_rebuild_session_memory("missing"))["ok"] is False

    detail = await service.admin_get_session_messages("session-1")
    assert detail["messages"][0]["content"] == "hello"

    memory_detail = await service.admin_get_session_memory("session-1")
    assert memory_detail["data"]["session_id"] == "session-1"

    rebuilt = await service.admin_rebuild_session_memory("session-1")
    assert rebuilt["data"]["rebuilt"] is True
    assert memory.cleared == ["session-1"]
    assert memory.rebuilt == ["session-1"]

    cleared = await service.admin_clear_session_messages("session-1")
    assert cleared["removed_count"] == 1
    assert messages["session-1"] == []

    failed_delete = await service.admin_delete_session("missing")
    assert failed_delete["ok"] is False


async def test_admin_sessions_service_surfaces_memory_cleanup_failures(monkeypatch, tmp_path) -> None:
    import app.backend.services.admin_sessions_service as module
    from app.backend.services.admin_sessions_service import AdminSessionsService

    class BrokenMemoryService:
        async def clear_memory(self, session_id: str) -> None:
            _ = session_id
            raise RuntimeError("memory failed")

    monkeypatch.setattr(module.AdminSessionsService, "_project_root", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(module.SessionManager, "get_session", lambda sid: {"id": sid})
    monkeypatch.setattr(module.SessionManager, "update_session_time", lambda sid: True)
    monkeypatch.setattr(module.SessionManager, "delete_session", lambda sid: True)
    monkeypatch.setattr(module.MessageManager, "get_messages", lambda sid: [])

    service = AdminSessionsService(await_call=await_call, get_session_memory_service=lambda: BrokenMemoryService())

    cleared = await service.admin_clear_session_messages("session-1")
    assert cleared["ok"] is True
    assert cleared["memory_cleared"] is False
    assert cleared["memory_error_type"] == "RuntimeError"

    deleted = await service.admin_delete_session("session-1")
    assert deleted["ok"] is True
    assert deleted["memory_cleared"] is False
    assert deleted["memory_error_type"] == "RuntimeError"


def test_admin_sessions_service_flow_helpers_cover_edge_cases(tmp_path, monkeypatch) -> None:
    import app.backend.services.admin_sessions_service as module
    from app.backend.services.admin_sessions_service import AdminSessionsService

    monkeypatch.setattr(module.AdminSessionsService, "_project_root", staticmethod(lambda: tmp_path))

    assert AdminSessionsService._safe_log_id("a/b c") == "a_b_c"
    assert AdminSessionsService._is_relative_to(tmp_path / "child", tmp_path) is True
    assert AdminSessionsService._is_relative_to(tmp_path, tmp_path / "child") is False
    assert AdminSessionsService._safe_json_text({"a": 1}) == '{"a": 1}'
    assert AdminSessionsService._safe_json_text(object())
    assert AdminSessionsService._allowed_flow_lane("unknown")
    assert AdminSessionsService._resolve_llm_lane({"actor_name": "graph worker"})[0]
    assert AdminSessionsService._resolve_llm_lane({"response_compact": '{"action":"call_rag"}'})[0]
    assert AdminSessionsService._infer_tool_lane({"tool_name": "rag_search"})
    assert AdminSessionsService._extract_json_object("```json\n{\"action\":\"call_kg\"}\n```")["action"] == "call_kg"
    assert AdminSessionsService._expected_tool_lane_for_action("call_history_read")
    assert AdminSessionsService._summarize_tool_observation({"result": {"error": "boom"}}) == "boom"
    assert AdminSessionsService._format_tool_input({"arguments": {"q": "x"}}, {"tool_name": "tool", "subquery": "q"}, 1)
    assert AdminSessionsService._format_tool_result({"result": {"ok": False, "error": "bad"}}, 1)

    session_id = "session-flow"
    request_id = "request-flow"
    llm_dir = tmp_path / "logs" / "sessions" / session_id / "llm"
    tools_dir = tmp_path / "logs" / "sessions" / session_id / "tools"
    llm_dir.mkdir(parents=True)
    tools_dir.mkdir(parents=True)
    (llm_dir / "2026-04-24.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "request_id": request_id,
                        "ts": "2026-04-24T10:00:00",
                        "actor_role": "controller",
                        "messages_compact": "controller input",
                        "response_compact": '{"action":"call_kg"}',
                    },
                    ensure_ascii=False,
                ),
                "not-json",
                json.dumps({"request_id": "other", "ts": "2026-04-24T10:00:01"}, ensure_ascii=False),
            ]
        ),
        encoding="utf-8",
    )
    (tools_dir / "2026-04-24.jsonl").write_text(
        json.dumps(
            {
                "request_id": request_id,
                "ts": "2026-04-24T10:00:02",
                "tool_name": "kg_entity_relation_search",
                "arguments": {"query": "hypertension"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    turn = {
        "turn_id": "turn-1",
        "request_id": request_id,
        "started_at": "2026-04-24T10:00:00",
        "finished_at": "2026-04-24T10:00:03",
        "tool_observations": [
            {
                "agent": "kg",
                "tool_name": "kg_entity_relation_search",
                "subquery": "hypertension",
                "result": {"ok": True, "result": {"summary": "entity found"}},
            },
            {
                "agent": "history",
                "tool_name": "history_read",
                "action": "call_history_read",
                "result": {"ok": True, "message": "history"},
            },
        ],
    }

    events = AdminSessionsService._build_flow_events(session_id=session_id, request_id=request_id, turn=turn)
    assert events
    assert any(event["detail"]["tool_call_count"] >= 1 for event in events)
    assert AdminSessionsService._read_session_llm_records(session_id, "") == []
    assert AdminSessionsService._read_session_tool_records(session_id, "missing") == []

    fallback_events = AdminSessionsService._build_flow_events(
        session_id=session_id,
        request_id="missing-llm-log",
        turn={
            "turn_id": "turn-fallback",
            "started_at": "2026-04-24T11:00:00",
            "finished_at": "2026-04-24T11:00:03",
            "agent_lanes": {
                "主控编排": [
                    {
                        "title": "主控统一决策",
                        "started_at": "2026-04-24T11:00:00",
                        "prompt_preview": "question",
                        "decision_preview": "call_rag",
                        "full_prompt": "question",
                        "full_decision": "{\"action\":\"call_rag\"}",
                        "status": "完成",
                    }
                ],
                "图谱代理": [],
                "检索代理": [
                    {
                        "lane_name": "检索代理",
                        "title": "执行工具调用",
                        "occurred_at": "2026-04-24T11:00:03",
                        "prompt_preview": "retrieval query",
                        "decision_preview": "命中指南",
                        "full_prompt": "retrieval query",
                        "full_decision": "{\"ok\":true}",
                        "status": "成功",
                    }
                ],
            },
        },
    )
    assert {event["lane"] for event in fallback_events} == {"主控编排", "检索代理"}
    assert any(event["detail"]["merge_strategy"] == "agent_lanes_fallback" for event in fallback_events)
    merged_metrics = AdminSessionsService._merge_turn_memory_update(
        {
            "turn_id": "turn-fallback",
            "llm_calls": 0,
            "total_tokens": 0,
            "agent_lanes": {
                "主控编排": [
                    {
                        "full_decision": json.dumps({"total_tokens": 128}, ensure_ascii=False),
                    }
                ]
            },
        },
        {},
    )
    assert merged_metrics["llm_calls"] == 1
    assert merged_metrics["total_tokens"] == 128


async def test_session_stream_service_persists_reply_and_disclaimer(tmp_path, monkeypatch) -> None:
    import app.backend.services.session_stream_service as module
    from app.backend.services.session_stream_service import (
        MEDICAL_DISCLAIMER_TEXT,
        SessionStreamService,
        append_medical_disclaimer_if_needed,
    )

    assert append_medical_disclaimer_if_needed("建议规律监测血压", "medical_qa").endswith(MEDICAL_DISCLAIMER_TEXT)
    markdown_disclaimer_reply = f"建议规律监测血压\n\n*{MEDICAL_DISCLAIMER_TEXT} *"
    assert append_medical_disclaimer_if_needed(markdown_disclaimer_reply, "medical_qa") == markdown_disclaimer_reply

    persisted: list[dict[str, Any]] = []
    refreshed: list[int] = []

    class FakeMessageManager:
        @staticmethod
        async def add_message(session_id: str, role: str, content: str, think_content: str = "") -> dict[str, Any]:
            persisted.append({"session_id": session_id, "role": role, "content": content, "think_content": think_content})
            return {"id": len(persisted), "session_id": session_id}

    class FakeMemoryService:
        async def refresh_after_turn(self, session_id: str, assistant_message_id: int, turn_artifacts: dict[str, Any]) -> dict[str, Any]:
            _ = (session_id, turn_artifacts)
            refreshed.append(assistant_message_id)
            return {"qa_memory": {"qa_pairs": []}, "retrieval_memory": {"questions": []}}

    class FakeEventStreamService:
        async def iter_a2a_stream_events(self, **kwargs):
            _ = kwargs
            yield {"type": "reply", "msg": "建议规律监测血压"}
            yield {
                "type": "done",
                "status": "done",
                "processing_steps": ["主控分析"],
                "tool_observations": [],
                "evidence_summary": {"supported_points": ["规律监测血压"]},
                "telemetry": {"llm_calls": 1},
            }

    monkeypatch.setattr(module, "_project_root", lambda: tmp_path)
    service = SessionStreamService(
        await_call=await_call,
        get_session_memory_service=lambda: FakeMemoryService(),
        log_api_error=log_api_error,
        event_stream_service=FakeEventStreamService(),
        message_manager=FakeMessageManager,
    )

    lines = [
        json.loads(line)
        async for line in service.generate_data_with_session_lines(
            input_text="我是不是高血压",
            user_id="user-1",
            session_id="session-1",
            user_message_id=1,
            request_id="req-1",
            original_input_text="我是不是高血压",
            memory_context={},
        )
    ]
    assert lines[0]["type"] == "session"
    assert any(item["type"] == "persisted" for item in lines)
    assert persisted[0]["role"] == "assistant"
    assert MEDICAL_DISCLAIMER_TEXT in persisted[0]["content"]


async def test_session_stream_helper_payload_builders(tmp_path, monkeypatch) -> None:
    import app.backend.services.session_stream_service as module

    monkeypatch.setattr(module, "_project_root", lambda: tmp_path)
    session_id = "session-helper"
    request_id = "req-helper"
    llm_dir = tmp_path / "logs" / "sessions" / session_id / "llm"
    llm_dir.mkdir(parents=True)
    (llm_dir / "2026-04-23.jsonl").write_text(
        json.dumps(
            {
                "request_id": request_id,
                "ts": "2026-04-23T10:00:00",
                "messages_compact": "Graph Agent input",
                "response_compact": "graph output",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assert module._sanitize_text(" a\nb ", 10) == "a b"
    assert module._json_text_len({"a": 1}) > 0
    assert module._safe_session_log_id("a/b c") == "a_b_c"
    assert module._to_iso("") != ""
    assert module._normalize_question_scope("bad") == "unknown"
    assert module._infer_question_scope("what can you do") == "capability_query"
    assert module._sanitize_list(["a", "a", "b"], 3, 10) == ["a", "b"]
    assert module._summarize_tool_result({"result": {"items": [1, 2]}})
    assert module._read_session_llm_records(session_id, request_id)[0]["response_compact"] == "graph output"

    answer = "结论：控制盐摄入。证据支持点：指南建议监测血压。不确定性与适用边界：需遵医嘱。"
    assert module._extract_section_sentences(answer, "结论", ("证据支持点",))
    assert module._extract_answered_points({}, answer)
    assert module._build_reply_digest(answer)

    observation = {
        "agent": "kg",
        "tool_name": "kg_entity_relation_search",
        "subquery": "高血压",
        "result": {"ok": True, "result": {"summary": "命中实体"}},
    }
    lane_card = module._build_lane_card_from_tool_observation(observation, "2026-04-23T10:00:00")
    assert lane_card["lane_name"]

    orchestrator_cards = module._build_orchestrator_cards(
        input_text="高血压如何控制？",
        memory_context={"qa_memory_text": "问答记忆", "retrieval_question_index_text": "检索记忆"},
        telemetry={"llm_calls": 1, "retrieval_round": 1},
        llm_rows=module._read_session_llm_records(session_id, request_id),
        started_at="2026-04-23T10:00:00",
        finished_at="2026-04-23T10:00:01",
    )
    assert orchestrator_cards

    memory_stages = module._build_memory_compression_stages(
        input_text="高血压如何控制？",
        full_reply=answer,
        memory_context={"qa_pairs": [{"user_question": "旧问题"}]},
        turn_projection={
            "user_question": "高血压如何控制？",
            "answer_digest": "控制盐摄入",
            "supported_points": ["控制盐摄入"],
            "retrieval_queries": ["高血压 限盐"],
            "retrieval_hits": ["指南"],
        },
        post_memory={"qa_memory": {"qa_pairs": [{"user_question": "高血压如何控制？"}]}, "retrieval_memory": {"questions": ["高血压 限盐"]}},
        started_at="2026-04-23T10:00:00",
        finished_at="2026-04-23T10:00:01",
        compressed_at="2026-04-23T10:00:02",
    )
    assert memory_stages

    turn_projection = module._build_turn_memory_projection(
        input_text="高血压如何控制？",
        user_message_id=1,
        full_reply=answer,
        processing_steps=["主控分析"],
        tool_observations=[observation],
        evidence_summary={"supported_points": ["控制盐摄入"]},
    )
    assert turn_projection["user_question"] == "高血压如何控制？"
    metrics = module._derive_tool_metrics([observation, {"result": {"ok": False, "error": "blocked"}}])
    assert metrics["attempted_tool_actions"] == 2

    payload = module._build_turn_observability_payload(
        session_id=session_id,
        user_id="user-1",
        request_id=request_id,
        user_message_id=1,
        assistant_message_id=2,
        input_text="高血压如何控制？",
        full_reply=answer,
        memory_context={"qa_pairs": []},
        processing_steps=["主控分析"],
        tool_observations=[observation],
        evidence_summary={"supported_points": ["控制盐摄入"]},
        telemetry={"llm_calls": 1, "total_tokens": 10},
        started_at="2026-04-23T10:00:00",
        finished_at="2026-04-23T10:00:01",
        duration_ms=1000.0,
        status="done",
        turn_artifacts=turn_projection,
    )
    assert payload["record_type"] == "turn_observability"
    assert payload["llm_calls"] == 1
    assert payload["total_tokens"] == 10

    update_payload = module._build_turn_memory_update_payload(
        session_id=session_id,
        request_id=request_id,
        user_message_id=1,
        assistant_message_id=2,
        updated_at="2026-04-23T10:00:02",
        snapshot={"qa_memory": {"qa_pairs": []}, "retrieval_memory": {"questions": []}},
        llm_metrics={"llm_calls": 1, "total_tokens": 10},
    )
    assert update_payload["record_type"] == "turn_memory_update"
