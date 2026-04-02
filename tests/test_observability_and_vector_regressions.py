import unittest
import asyncio
import ast
import copy
import inspect
import json
import time
from pathlib import Path
from unittest.mock import patch
from typing import Any, Callable, Dict, List, Optional

from app.common.session_logger import log_api_call


class TestObservabilityAndVectorRegressions(unittest.TestCase):
    def test_streaming_identity_should_prioritize_request_state(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        target = project_root / "app" / "backend" / "main.py"
        text = target.read_text(encoding="utf-8")

        self.assertIn("def _resolve_api_session_user(request: Request)", text)
        self.assertIn('state_session_id = str(getattr(request.state, "session_id", "") or "")', text)
        self.assertIn('state_user_id = str(getattr(request.state, "user_id", "") or "")', text)
        self.assertIn('query_session_id = str(request.query_params.get("session_id") or "")', text)
        self.assertIn('query_user_id = str(request.query_params.get("user_id") or "")', text)
        self.assertIn('"session_id": state_session_id or query_session_id', text)
        self.assertIn('"user_id": state_user_id or query_user_id', text)

        self.assertIn("request.state.user_id = str(user_id)", text)
        self.assertIn('request.state.session_id = str(session_id or "")', text)
        self.assertIn("request.state.session_id = str(session_id)", text)

    def test_streaming_http_request_timer_should_close_after_stream_finish(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        target = project_root / "app" / "backend" / "main.py"
        text = target.read_text(encoding="utf-8")

        self.assertIn('request_timer_ctx = log_duration("http_request"', text)
        self.assertNotIn('with log_duration("http_request"', text)

        stream_start = text.index("async def _log_stream_finished(stream_error_type: str) -> None:")
        stream_end = text.index("    try:", stream_start)
        stream_section = text[stream_start:stream_end]
        self.assertIn("log_api_call(", stream_section)
        self.assertIn("_finish_request_timer()", stream_section)
        self.assertLess(stream_section.index("log_api_call("), stream_section.rindex("_finish_request_timer()"))

        call_next_idx = text.index("response = await call_next(request)")
        streaming_branch_idx = text.index("if isinstance(response, StreamingResponse):")
        self.assertLess(call_next_idx, streaming_branch_idx)

    def test_streaming_timer_exit_should_happen_after_body_consumed_at_runtime(self) -> None:
        from fastapi import Request
        from fastapi.responses import StreamingResponse
        events = []

        project_root = Path(__file__).resolve().parents[1]
        target = project_root / "app" / "backend" / "main.py"
        source = target.read_text(encoding="utf-8-sig")
        tree = ast.parse(source)

        extracted_nodes = {}
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in {
                "_resolve_api_session_user",
                "request_timing_middleware",
            }:
                node_copy = copy.deepcopy(node)
                node_copy.decorator_list = []
                extracted_nodes[node.name] = node_copy

        self.assertIn("_resolve_api_session_user", extracted_nodes)
        self.assertIn("request_timing_middleware", extracted_nodes)

        module_ast = ast.Module(
            body=[
                extracted_nodes["_resolve_api_session_user"],
                extracted_nodes["request_timing_middleware"],
            ],
            type_ignores=[],
        )
        ast.fix_missing_locations(module_ast)

        class _FakeTimer:
            def __enter__(self):
                events.append("timer_enter")
                return self

            def __exit__(self, exc_type, exc, tb):
                events.append("timer_exit")
                return False

        def _fake_log_duration(*args, **kwargs):
            _ = args
            _ = kwargs
            return _FakeTimer()

        def _fake_wrap_stream_body_iterator(body_iterator, on_close):
            async def _wrapped():
                async for chunk in body_iterator:
                    yield chunk
                events.append("before_on_close")
                callback_ret = on_close("")
                if inspect.isawaitable(callback_ret):
                    await callback_ret
                events.append("after_on_close")

            return _wrapped()

        async def _call_next(_request):
            async def _body():
                events.append("stream_chunk")
                yield b"x"

            return StreamingResponse(_body(), media_type="text/plain")

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/process_with_session",
            "headers": [],
            "query_string": b"",
            "client": ("127.0.0.1", 12345),
        }
        request = Request(scope)

        runtime_ns = {
            "Request": Request,
            "StreamingResponse": StreamingResponse,
            "Optional": Optional,
            "Dict": Dict,
            "Any": Any,
            "Callable": Callable,
            "BaseException": BaseException,
            "time": time,
            "set_trace_metadata": lambda *args, **kwargs: None,
            "create_request_id": lambda: "req-runtime",
            "set_request_context": lambda _request_id: None,
            "_extract_bearer_token": lambda _auth: "",
            "set_current_admin_token": lambda _token: None,
            "reset_current_admin_token": lambda _token_handle: None,
            "reset_request_context": lambda _request_token: None,
            "log_api_call": lambda **kwargs: events.append("log_api_call"),
            "log_duration": _fake_log_duration,
            "_wrap_stream_body_iterator": _fake_wrap_stream_body_iterator,
        }
        exec(compile(module_ast, str(target), "exec"), runtime_ns)

        middleware = runtime_ns["request_timing_middleware"]
        response = asyncio.run(middleware(request, _call_next))
        self.assertIsInstance(response, StreamingResponse)
        self.assertNotIn("timer_exit", events)

        async def _consume() -> None:
            async for _ in response.body_iterator:
                pass

        asyncio.run(_consume())

        self.assertIn("timer_exit", events)
        self.assertIn("log_api_call", events)
        self.assertLess(events.index("log_api_call"), events.index("timer_exit"))

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
        project_root = Path(__file__).resolve().parents[1]
        target = project_root / "app" / "rag_core" / "vector_store.py"
        text = target.read_text(encoding="utf-8")

        required_markers = [
            'log_duration("milvus.hybrid_search_with_rerank"',
            'log_duration("milvus.embed_query"',
            'log_duration("milvus.search_dense"',
            'log_duration("milvus.search_sparse"',
            'log_duration("milvus.rrf_fusion"',
            'log_duration("milvus.rerank"',
        ]
        for marker in required_markers:
            self.assertIn(marker, text)

        # 关键子阶段应按调用顺序出现在函数体中。
        embed_idx = text.index('log_duration("milvus.embed_query"')
        rrf_anchor_idx = text.index("使用 RRF 算法融合检索结果")
        self.assertLess(embed_idx, rrf_anchor_idx)

        rrf_section = text[rrf_anchor_idx:]
        dense_idx = rrf_section.index('log_duration("milvus.search_dense"')
        sparse_idx = rrf_section.index('log_duration("milvus.search_sparse"')
        fusion_idx = rrf_section.index('log_duration("milvus.rrf_fusion"')
        rerank_idx = rrf_section.index('log_duration("milvus.rerank"')
        self.assertTrue(dense_idx < sparse_idx < fusion_idx < rerank_idx)

    def test_memory_refresh_failure_should_not_emit_error_done(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        target = project_root / "app" / "backend" / "main.py"
        source = target.read_text(encoding="utf-8-sig")
        tree = ast.parse(source)

        target_node = None
        for node in tree.body:
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "generate_data_with_session":
                target_node = copy.deepcopy(node)
                break

        self.assertIsNotNone(target_node)
        module_ast = ast.Module(body=[target_node], type_ignores=[])
        ast.fix_missing_locations(module_ast)

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

        runtime_ns = {
            "asyncio": asyncio,
            "json": json,
            "Optional": Optional,
            "Dict": Dict,
            "Any": Any,
            "List": List,
            "MessageManager": _FakeMessageManager,
            "_await_call": _await_call,
            "get_session_memory_service": lambda: _FakeMemoryService(),
            "_generate_a2a_stream_events": _fake_generate_events,
            "_log_api_error": _log_api_error,
        }
        exec(compile(module_ast, str(target), "exec"), runtime_ns)

        async def _run_case():
            rows = []
            generator = runtime_ns["generate_data_with_session"](
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

    def test_done_emitted_persist_failure_should_retry_in_finally_without_error_done(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        target = project_root / "app" / "backend" / "main.py"
        source = target.read_text(encoding="utf-8-sig")
        tree = ast.parse(source)

        target_node = None
        for node in tree.body:
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "generate_data_with_session":
                target_node = copy.deepcopy(node)
                break

        self.assertIsNotNone(target_node)
        module_ast = ast.Module(body=[target_node], type_ignores=[])
        ast.fix_missing_locations(module_ast)

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

        runtime_ns = {
            "asyncio": asyncio,
            "json": json,
            "Optional": Optional,
            "Dict": Dict,
            "Any": Any,
            "List": List,
            "MessageManager": _FakeMessageManager,
            "_await_call": _await_call,
            "get_session_memory_service": lambda: _FakeMemoryService(),
            "_generate_a2a_stream_events": _fake_generate_events,
            "_log_api_error": _log_api_error,
        }
        exec(compile(module_ast, str(target), "exec"), runtime_ns)

        async def _run_case():
            rows = []
            generator = runtime_ns["generate_data_with_session"](
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


if __name__ == "__main__":
    unittest.main()
