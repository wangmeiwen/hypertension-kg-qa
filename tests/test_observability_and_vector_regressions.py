import unittest
import asyncio
import ast
import inspect
import json
from pathlib import Path
from unittest.mock import patch
from typing import Any

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from app.a2a.orchestrator.nodes import node_route_by_memory_capability
from app.backend.middleware.request_timing import request_timing_middleware
from app.backend.repositories.database import MessageManager, SessionManager
from app.backend.routers.query_chat import create_query_chat_router
from app.backend.services.session_stream_service import SessionStreamService
from app.backend.services.sessions_history_service import SessionsHistoryService
from app.common.session_logger import log_api_call


class TestObservabilityAndVectorRegressions(unittest.TestCase):
    def test_streaming_identity_should_prioritize_request_state(self) -> None:
        captured_logs: list[dict[str, Any]] = []

        class _FakeReadinessService:
            async def ensure_core_ready(self, endpoint: str):
                _ = endpoint
                return None

        class _FakeAgentRunService:
            pass

        class _FakeEventStreamService:
            async def generate_data_lines(self, **kwargs: Any):
                _ = kwargs
                yield json.dumps({"type": "done", "status": "done", "msg": "ok"}, ensure_ascii=False) + "\n"

        class _FakeSessionTurnService:
            def bind_process_session_trace_metadata(self, trace_id: str, session_id: str, user_id: str) -> None:
                _ = trace_id
                _ = session_id
                _ = user_id

            async def prepare_session_turn(self, **kwargs: Any) -> dict[str, Any]:
                _ = kwargs
                return {
                    "ok": True,
                    "user_id": "u-state",
                    "session_id": "s-state",
                    "user_message_id": 1,
                    "original_input_text": "问题",
                    "resolved_query": {},
                    "memory_context": {},
                }

        class _FakeSessionStreamService:
            async def generate_data_with_session_lines(self, **kwargs: Any):
                _ = kwargs
                yield json.dumps({"type": "done", "status": "done", "msg": "ok"}, ensure_ascii=False) + "\n"

        app = FastAPI()
        app.middleware("http")(request_timing_middleware)
        app.include_router(
            create_query_chat_router(
                agent_run_service=_FakeAgentRunService(),
                session_turn_service=_FakeSessionTurnService(),
                session_stream_service=_FakeSessionStreamService(),
                event_stream_service=_FakeEventStreamService(),
                readiness_service=_FakeReadinessService(),
                get_trace_id=lambda _request: "req-test",
                enforce_rate_limit=lambda **kwargs: None,
                rate_limiter=None,
                endpoint_limits={},
                window_seconds=60.0,
            )
        )

        with patch("app.backend.middleware.request_timing.log_api_call", side_effect=lambda **kwargs: captured_logs.append(kwargs)):
            client = TestClient(app)
            response = client.post(
                "/process_with_session",
                json={"input": "问题", "session_id": "ignored", "user_id": "ignored"},
            )
            self.assertEqual(response.status_code, 200)
            _ = response.text

        self.assertGreaterEqual(len(captured_logs), 1)
        self.assertEqual(str(captured_logs[-1].get("session_id") or ""), "s-state")
        self.assertEqual(str(captured_logs[-1].get("user_id") or ""), "u-state")

    def test_streaming_http_request_timer_should_close_after_stream_finish(self) -> None:
        events: list[str] = []

        class _FakeTimer:
            def __enter__(self):
                events.append("timer_enter")
                return self

            def __exit__(self, exc_type, exc, tb):
                _ = exc_type
                _ = exc
                _ = tb
                events.append("timer_exit")
                return False

        def _fake_log_duration(*args, **kwargs):
            _ = args
            _ = kwargs
            return _FakeTimer()

        app = FastAPI()
        app.middleware("http")(request_timing_middleware)

        @app.get("/stream")
        async def stream_endpoint() -> StreamingResponse:
            async def _body():
                events.append("stream_chunk")
                yield b"x"

            return StreamingResponse(_body(), media_type="text/plain")

        with patch("app.backend.middleware.request_timing.log_duration", side_effect=_fake_log_duration), patch(
            "app.backend.middleware.request_timing.log_api_call", side_effect=lambda **kwargs: events.append("log_api_call")
        ), patch("app.backend.middleware.request_timing.create_request_id", return_value="req-stream"):
            client = TestClient(app)
            response = client.get("/stream")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content, b"x")

        self.assertIn("timer_enter", events)
        self.assertIn("log_api_call", events)
        self.assertIn("timer_exit", events)
        self.assertLess(events.index("log_api_call"), events.index("timer_exit"))

    def test_streaming_timer_exit_should_happen_after_body_consumed_at_runtime(self) -> None:
        from app.backend.middleware.request_timing import _wrap_stream_body_iterator

        events: list[str] = []

        async def _body() -> Any:
            events.append("chunk_1")
            yield b"a"
            events.append("chunk_2")
            yield b"b"

        async def _on_close(err: str) -> None:
            events.append(f"on_close:{err}")

        async def _consume() -> list[bytes]:
            chunks: list[bytes] = []
            async for chunk in _wrap_stream_body_iterator(_body(), _on_close):
                chunks.append(chunk)
            return chunks

        chunks = asyncio.run(_consume())
        self.assertEqual(chunks, [b"a", b"b"])
        self.assertEqual(events, ["chunk_1", "chunk_2", "on_close:"])

    def test_streaming_log_should_keep_stream_total_gte_ttfb(self) -> None:
        captured_payloads = []

        def _capture_jsonl(_file_path: Path, payload: dict) -> None:
            captured_payloads.append(payload)

        with patch("app.common.session_logger._append_jsonl", side_effect=_capture_jsonl), patch(
            "app.common.session_logger._api_dir", return_value=Path(".")
        ):
            log_api_call(
                request_id="req-1",
                method="GET",
                path="/process_with_session",
                status_code=200,
                is_streaming=True,
                ttfb_ms=120.0,
                stream_total_ms=20.0,
            )

        self.assertEqual(len(captured_payloads), 1)
        payload = captured_payloads[0]
        self.assertTrue(bool(payload.get("is_streaming")))
        self.assertGreaterEqual(float(payload.get("stream_total_ms") or 0.0), float(payload.get("ttfb_ms") or 0.0))

    def test_non_streaming_log_should_align_ttfb_and_total(self) -> None:
        captured_payloads = []

        def _capture_jsonl(_file_path: Path, payload: dict) -> None:
            captured_payloads.append(payload)

        with patch("app.common.session_logger._append_jsonl", side_effect=_capture_jsonl), patch(
            "app.common.session_logger._api_dir", return_value=Path(".")
        ):
            log_api_call(
                request_id="req-2",
                method="POST",
                path="/agent/run",
                status_code=200,
                is_streaming=False,
                ttfb_ms=10.0,
                stream_total_ms=55.0,
            )

        self.assertEqual(len(captured_payloads), 1)
        payload = captured_payloads[0]
        self.assertFalse(bool(payload.get("is_streaming")))
        self.assertEqual(float(payload.get("ttfb_ms") or 0.0), float(payload.get("stream_total_ms") or 0.0))

    def test_vector_store_rrf_stage_markers_should_exist(self) -> None:
        stage_events: list[str] = []

        class _Timer:
            def __init__(self, name: str):
                self._name = name

            def __enter__(self):
                stage_events.append(self._name)
                return self

            def __exit__(self, exc_type, exc, tb):
                _ = exc_type
                _ = exc
                _ = tb
                return False

        def _fake_log_duration(name: str, **kwargs: Any):
            _ = kwargs
            return _Timer(name)

        class _SparseRow:
            indices = [1]
            data = [0.5]

        class _SparseEmb:
            def getrow(self, idx: int):
                _ = idx
                return _SparseRow()

        class _Doc:
            def __init__(self, page_content: str):
                self.page_content = page_content
                self.metadata = {"parent_content": page_content}

        class _Reranker:
            @staticmethod
            def predict(_pairs: list[list[str]]) -> list[float]:
                return [0.2, 0.8]

        project_root = Path(__file__).resolve().parents[1]
        target = project_root / "app" / "rag_core" / "vector_store.py"
        source = target.read_text(encoding="utf-8-sig")
        tree = ast.parse(source)

        hybrid_node = None
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == "VectorStore":
                for item in node.body:
                    if isinstance(item, ast.AsyncFunctionDef) and item.name == "hybrid_search_with_rerank":
                        hybrid_node = item
                        break
        self.assertIsNotNone(hybrid_node)

        module_ast = ast.Module(body=[hybrid_node], type_ignores=[])
        ast.fix_missing_locations(module_ast)

        class _Conf:
            RETRIEVAL_K = 5
            CANDIDATE_M = 3
            RAG_DENSE_RECALL_MULTIPLIER = 2
            RAG_SPARSE_RECALL_MULTIPLIER = 2
            RRF_K = 60
            MILVUS_SEARCH_NPROBE = 10

        class _Logger:
            @staticmethod
            def info(*args, **kwargs):
                _ = args
                _ = kwargs

            @staticmethod
            def warning(*args, **kwargs):
                _ = args
                _ = kwargs

        runtime_ns = {
            "conf": _Conf(),
            "log_duration": _fake_log_duration,
            "logger": _Logger(),
            "_to_float32_vector": lambda vec, _dim: vec,
        }
        exec(compile(module_ast, str(target), "exec"), runtime_ns)
        hybrid_search_with_rerank = runtime_ns["hybrid_search_with_rerank"]

        fake_store = type("_FakeStore", (), {})()
        fake_store.embedding_function = lambda _queries: {"dense": [[0.1, 0.2]], "sparse": _SparseEmb()}
        fake_store.dense_dim = 2
        fake_store.collection_fields = {"dense_vector", "sparse_vector"}
        fake_store.collection_name = "test_collection"
        fake_store.reranker = _Reranker()
        fake_store._search_with_params = lambda **kwargs: [[
            {"entity": {"text": "t1", "parent_id": "p1", "parent_content": "P1", "source": "s", "timestamp": "1"}},
            {"entity": {"text": "t2", "parent_id": "p2", "parent_content": "P2", "source": "s", "timestamp": "2"}},
        ]]
        fake_store._reciprocal_rank_fusion = lambda dense, sparse, k: [dense[0]["entity"], sparse[0]["entity"]]
        fake_store._doc_from_hit = lambda hit: _Doc(str(hit.get("parent_content") or ""))
        fake_store._get_unique_parent_docs = lambda _sub_chunks: [_Doc("P1"), _Doc("P2")]

        results = asyncio.run(hybrid_search_with_rerank(fake_store, "高血压", k=2))

        self.assertGreaterEqual(len(results), 1)
        required_order = [
            "milvus.hybrid_search_with_rerank",
            "milvus.embed_query",
            "milvus.search_dense",
            "milvus.search_sparse",
            "milvus.rrf_fusion",
            "milvus.rerank",
        ]
        for stage in required_order:
            self.assertIn(stage, stage_events)

        indices = [stage_events.index(stage) for stage in required_order]
        self.assertEqual(indices, sorted(indices))

    def test_session_stream_should_emit_session_and_persisted_metadata(self) -> None:
        class _FakeMessageManager:
            @staticmethod
            async def add_message(*args, **kwargs):
                _ = args
                _ = kwargs
                return {"id": 222}

        class _FakeMemoryService:
            async def refresh_after_turn(self, *args, **kwargs):
                _ = args
                _ = kwargs
                await asyncio.sleep(0)
                return {}

        async def _await_call(func, *args, **kwargs):
            result = func(*args, **kwargs)
            if inspect.isawaitable(result):
                return await result
            return result

        class _FakeEventStreamService:
            async def iter_a2a_stream_events(self, **kwargs: Any):
                _ = kwargs
                yield {"type": "reply", "msg": "回答"}
                yield {"type": "done", "status": "done", "msg": "回答"}

        service = SessionStreamService(
            await_call=_await_call,
            get_session_memory_service=lambda: _FakeMemoryService(),
            log_api_error=lambda *args, **kwargs: None,
            event_stream_service=_FakeEventStreamService(),
            message_manager=_FakeMessageManager,
        )

        async def _run_case() -> list[dict[str, Any]]:
            rows = []
            generator = service.generate_data_with_session_lines(
                input_text="问题",
                user_id="u1",
                session_id="s1",
                user_message_id=111,
                request_id="req1",
            )
            async for line in generator:
                rows.append(json.loads(line))
            await asyncio.sleep(0)
            return rows

        outputs = asyncio.run(_run_case())
        self.assertEqual(str(outputs[0].get("type") or ""), "session")
        self.assertEqual(str(outputs[0].get("session_id") or ""), "s1")
        self.assertEqual(int(outputs[0].get("user_message_id") or 0), 111)

        persisted_rows = [row for row in outputs if str(row.get("type") or "") == "persisted"]
        self.assertEqual(len(persisted_rows), 1)
        self.assertEqual(int(persisted_rows[0].get("assistant_message_id") or 0), 222)
        self.assertEqual(int(persisted_rows[0].get("user_message_id") or 0), 111)
        self.assertLess(
            [str(row.get("type") or "") for row in outputs].index("done"),
            [str(row.get("type") or "") for row in outputs].index("persisted"),
        )

    def test_rollback_to_question_should_clear_and_rebuild_memory(self) -> None:
        calls: list[str] = []

        class _FakeMemoryService:
            async def clear_memory(self, session_id: str) -> None:
                calls.append(f"clear:{session_id}")

            async def rebuild_from_messages(self, session_id: str) -> dict[str, Any]:
                calls.append(f"rebuild:{session_id}")
                return {"summary": {"last_message_id": 1}}

        async def _await_call(func, *args, **kwargs):
            result = func(*args, **kwargs)
            if inspect.isawaitable(result):
                return await result
            return result

        service = SessionsHistoryService(
            await_call=_await_call,
            is_session_owner=lambda _session_id, _user_id: True,
            get_a2a=lambda: None,
            get_session_memory_service=lambda: _FakeMemoryService(),
            upload_dir=Path("."),
            log_api_error=lambda *args, **kwargs: None,
        )

        with patch.object(SessionManager, "get_session", return_value={"id": "s1", "user_id": "u1"}), patch.object(
            MessageManager,
            "rollback_to_question",
            return_value={"ok": True, "removed_count": 2, "question": "高血压常见症状有哪些？"},
        ):
            result = asyncio.run(service.rollback_to_question("s1", {"user_id": "u1", "message_id": 11}))

        self.assertTrue(bool(result.get("ok")))
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        self.assertEqual(int(data.get("removed_count") or 0), 2)
        self.assertTrue(bool(data.get("memory_cleared")))
        self.assertTrue(bool(data.get("memory_rebuilt")))
        self.assertEqual(calls, ["clear:s1", "rebuild:s1"])

    def test_rollback_memory_rebuild_failure_should_not_fail_rollback(self) -> None:
        logged_stages: list[str] = []

        class _FakeMemoryService:
            async def clear_memory(self, session_id: str) -> None:
                _ = session_id

            async def rebuild_from_messages(self, session_id: str) -> dict[str, Any]:
                _ = session_id
                raise RuntimeError("rebuild_failed")

        async def _await_call(func, *args, **kwargs):
            result = func(*args, **kwargs)
            if inspect.isawaitable(result):
                return await result
            return result

        service = SessionsHistoryService(
            await_call=_await_call,
            is_session_owner=lambda _session_id, _user_id: True,
            get_a2a=lambda: None,
            get_session_memory_service=lambda: _FakeMemoryService(),
            upload_dir=Path("."),
            log_api_error=lambda _endpoint, stage, _exc, **_kwargs: logged_stages.append(stage),
        )

        with patch.object(SessionManager, "get_session", return_value={"id": "s1", "user_id": "u1"}), patch.object(
            MessageManager,
            "rollback_last_question",
            return_value={"ok": True, "removed_count": 2, "question": "高血压常见症状有哪些？"},
        ):
            result = asyncio.run(service.rollback_last_question("s1", {"user_id": "u1"}))

        self.assertTrue(bool(result.get("ok")))
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        self.assertTrue(bool(data.get("memory_cleared")))
        self.assertFalse(bool(data.get("memory_rebuilt")))
        self.assertEqual(str(data.get("memory_error") or ""), "RuntimeError")
        self.assertIn("rollback_last_question_memory", logged_stages)

    def test_memory_refresh_failure_should_not_emit_error_done(self) -> None:
        logged_stages = []

        class _FakeMessageManager:
            calls = 0

            @staticmethod
            async def add_message(*args, **kwargs):
                _ = args
                _ = kwargs
                _FakeMessageManager.calls += 1
                return {"id": 101}

        class _FakeMemoryService:
            async def refresh_after_turn(self, *args, **kwargs):
                _ = args
                _ = kwargs
                await asyncio.sleep(0)
                raise asyncio.TimeoutError()

        async def _await_call(func, *args, **kwargs):
            result = func(*args, **kwargs)
            if inspect.isawaitable(result):
                return await result
            return result

        async def _fake_generate_events(**kwargs):
            _ = kwargs
            yield {"type": "reply", "msg": "回答片段"}
            yield {
                "type": "done",
                "status": "done",
                "msg": "回答片段",
                "processing_steps": [],
                "tool_observations": [],
                "evidence_summary": {},
            }

        def _log_api_error(endpoint, stage, exc, **kwargs):
            _ = endpoint
            _ = exc
            _ = kwargs
            logged_stages.append(stage)

        class _FakeEventStreamService:
            async def iter_a2a_stream_events(self, **kwargs: Any):
                async for msg in _fake_generate_events(**kwargs):
                    yield msg

        service = SessionStreamService(
            await_call=_await_call,
            get_session_memory_service=lambda: _FakeMemoryService(),
            log_api_error=_log_api_error,
            event_stream_service=_FakeEventStreamService(),
            message_manager=_FakeMessageManager,
        )

        async def _run_case():
            rows = []
            generator = service.generate_data_with_session_lines(
                input_text="问题",
                user_id="u1",
                session_id="s1",
                request_id="req1",
            )
            async for line in generator:
                rows.append(json.loads(line))
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            return rows

        outputs = asyncio.run(_run_case())
        done_rows = [row for row in outputs if str(row.get("type") or "") == "done"]
        self.assertEqual(len(done_rows), 1)
        self.assertEqual(str(done_rows[0].get("status") or ""), "done")
        self.assertEqual(_FakeMessageManager.calls, 1)
        self.assertIn("memory_refresh_failed", logged_stages)
        self.assertNotIn("generate_data_with_session_exception", logged_stages)

    def test_memory_refresh_should_use_turn_memory_projection_only(self) -> None:
        captured_turn_artifacts: dict[str, Any] = {}

        class _FakeMessageManager:
            @staticmethod
            async def add_message(*args, **kwargs):
                _ = args
                _ = kwargs
                return {"id": 303}

        class _FakeMemoryService:
            async def refresh_after_turn(self, *args, **kwargs):
                _ = args
                turn_artifacts_obj = kwargs.get("turn_artifacts") if isinstance(kwargs.get("turn_artifacts"), dict) else None
                if turn_artifacts_obj is None and len(args) >= 3 and isinstance(args[2], dict):
                    turn_artifacts_obj = args[2]
                if isinstance(turn_artifacts_obj, dict):
                    captured_turn_artifacts.update(turn_artifacts_obj)
                await asyncio.sleep(0)

        async def _await_call(func, *args, **kwargs):
            result = func(*args, **kwargs)
            if inspect.isawaitable(result):
                return await result
            return result

        async def _fake_generate_events(**kwargs):
            _ = kwargs
            yield {"type": "reply", "msg": "结论：可回答。\n证据支持点：A。\n不确定性与适用边界：B。"}
            yield {
                "type": "done",
                "status": "done",
                "msg": "结论：可回答。",
                "processing_steps": ["step1", "step2"],
                "tool_observations": [{"raw": "long payload"}],
                "evidence_summary": {"missing_points": ["outcome"], "high_risk_tokens": ["高风险"]},
            }

        class _FakeEventStreamService:
            async def iter_a2a_stream_events(self, **kwargs: Any):
                async for msg in _fake_generate_events(**kwargs):
                    yield msg

        service = SessionStreamService(
            await_call=_await_call,
            get_session_memory_service=lambda: _FakeMemoryService(),
            log_api_error=lambda *args, **kwargs: None,
            event_stream_service=_FakeEventStreamService(),
            message_manager=_FakeMessageManager,
        )

        async def _run_case() -> None:
            generator = service.generate_data_with_session_lines(
                input_text="比较复方和单药",
                user_id="u1",
                session_id="s1",
                request_id="req1",
                resolved_query={"entities": ["ACEI", "CCB"], "must_cover": ["疗效", "安全"]},
            )
            async for _ in generator:
                pass
            await asyncio.sleep(0)
            await asyncio.sleep(0)

        asyncio.run(_run_case())
        projection_obj = captured_turn_artifacts.get("turn_memory_projection")
        projection = projection_obj if isinstance(projection_obj, dict) else {}
        self.assertIn("assistant_reply_digest", projection)
        self.assertIn("question_scope", projection)
        self.assertNotIn("tool_observations", captured_turn_artifacts)
        self.assertEqual(str(captured_turn_artifacts.get("projection_mode") or ""), "turn_memory_projection_v1")

    def test_done_emitted_persist_failure_should_retry_in_finally_without_error_done(self) -> None:
        logged_stages = []

        class _FakeMessageManager:
            call_attempts = 0
            persisted_success = 0

            @staticmethod
            async def add_message(*args, **kwargs):
                _ = args
                _ = kwargs
                _FakeMessageManager.call_attempts += 1
                if _FakeMessageManager.call_attempts == 1:
                    raise RuntimeError("persist_failed_once")
                _FakeMessageManager.persisted_success += 1
                return {"id": 202}

        class _FakeMemoryService:
            async def refresh_after_turn(self, *args, **kwargs):
                _ = args
                _ = kwargs
                await asyncio.sleep(0)
                return None

        async def _await_call(func, *args, **kwargs):
            result = func(*args, **kwargs)
            if inspect.isawaitable(result):
                return await result
            return result

        async def _fake_generate_events(**kwargs):
            _ = kwargs
            yield {"type": "reply", "msg": "最终答案"}
            yield {
                "type": "done",
                "status": "done",
                "msg": "最终答案",
                "processing_steps": [],
                "tool_observations": [],
                "evidence_summary": {},
            }

        def _log_api_error(endpoint, stage, exc, **kwargs):
            _ = endpoint
            _ = exc
            _ = kwargs
            logged_stages.append(stage)

        class _FakeEventStreamService:
            async def iter_a2a_stream_events(self, **kwargs: Any):
                async for msg in _fake_generate_events(**kwargs):
                    yield msg

        service = SessionStreamService(
            await_call=_await_call,
            get_session_memory_service=lambda: _FakeMemoryService(),
            log_api_error=_log_api_error,
            event_stream_service=_FakeEventStreamService(),
            message_manager=_FakeMessageManager,
        )

        async def _run_case():
            rows = []
            generator = service.generate_data_with_session_lines(
                input_text="问题",
                user_id="u1",
                session_id="s1",
                request_id="req1",
            )
            async for line in generator:
                rows.append(json.loads(line))
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            return rows

        outputs = asyncio.run(_run_case())
        done_rows = [row for row in outputs if str(row.get("type") or "") == "done"]
        self.assertEqual(len(done_rows), 1)
        self.assertEqual(str(done_rows[0].get("status") or ""), "done")
        self.assertEqual(_FakeMessageManager.persisted_success, 1)
        self.assertEqual(_FakeMessageManager.call_attempts, 2)
        self.assertIn("assistant_persist_failed_after_done", logged_stages)
        self.assertNotIn("generate_data_with_session_exception", logged_stages)

    def test_memory_answer_route_should_fallback_to_retrieval_when_unavailable(self) -> None:
        state = {
            "input_text": "高血压的常见症状有哪些？",
            "resolved_query": {
                "preferred_sources": ["memory"],
                "question_mode": "repeat_medical_question",
                "must_cover": ["高血压常见症状"],
            },
            "memory_context": {
                "summary": {"answered_points": ["高血压常见症状包括头痛"]},
                "retrieval_summary": {"covered_topics": ["高血压常见症状"]},
                "metadata": {"repeat_question_detected": True},
                "topic_shift_signal": {"question_scope": "medical_qa"},
            },
            "classification": {},
            "processing_steps": [],
            "reflection_events": [],
        }

        with patch(
            "app.a2a.orchestrator.nodes.MemoryAnswerService.answer",
            return_value={"can_answer": False, "answer_text": "", "support_type": "", "confidence": 0.0},
        ):
            result = asyncio.run(node_route_by_memory_capability(state))

        self.assertEqual(str(result.get("response_mode") or ""), "medical_retrieval")
        self.assertEqual(str(result.get("memory_route_reason") or ""), "memory_answer_unavailable_fallback_retrieval")
        self.assertEqual(result.get("memory_supported_points"), [])


if __name__ == "__main__":
    unittest.main()
