"""端到端 trace、turn、token 和耗时观测契约测试。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from app.a2a.service import A2AService
from app.a2a.telemetry import (
    empty_token_usage,
    merge_token_usage,
    normalize_token_usage,
)


def _read_single_jsonl(path: Path) -> dict[str, Any]:
    files = list(path.rglob("*.jsonl"))
    assert len(files) == 1
    return json.loads(files[0].read_text(encoding="utf-8").strip())


def test_token_usage_normalizes_and_aggregates_all_fields() -> None:
    usage = empty_token_usage()
    merge_token_usage(
        usage,
        {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
    )
    merge_token_usage(
        usage,
        {"prompt_tokens": 7, "completion_tokens": 1},
    )

    assert usage == {
        "prompt_tokens": 10,
        "completion_tokens": 3,
        "total_tokens": 13,
    }
    assert normalize_token_usage({"prompt_tokens": -1, "total_tokens": -5}) == {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }


def test_session_logs_share_trace_and_turn_contract(monkeypatch, tmp_path) -> None:
    import app.backend.observability.session_logger as logs

    monkeypatch.setattr(logs, "_api_dir", lambda: tmp_path / "api")
    monkeypatch.setattr(logs, "_sessions_dir", lambda: tmp_path / "sessions")
    request_token = logs.set_request_context("trace-1")
    session_tokens = logs.set_session_context("session-1", "user-1")
    turn_token = logs.set_turn_context("session-1:42")
    try:
        logs.log_api_call(
            request_id="trace-1",
            method="POST",
            path="/process_with_session",
            status_code=200,
            is_streaming=True,
            ttfb_ms=12.5,
            stream_total_ms=25.0,
        )
        logs.log_llm_call(
            session_id="session-1",
            model="model",
            temperature=0.1,
            messages=[{"role": "user", "content": "question"}],
            response_content="answer",
            prompt_tokens=8,
            completion_tokens=2,
            total_tokens=10,
            latency_ms=20.0,
            finish_reason="stop",
        )
        logs.log_session_op(
            session_id="session-1",
            turn=42,
            stage="orchestrator",
            action="run_end",
            status="done",
            duration_ms=25.0,
        )
        logs.log_tool_event(
            session_id="session-1",
            turn=42,
            tool_name="rag_search",
            arguments={"query": "question"},
            result_ok=True,
            has_signal=True,
            duration_ms=7.0,
            token_usage={
                "prompt_tokens": 3,
                "completion_tokens": 1,
                "total_tokens": 4,
            },
        )
    finally:
        logs.reset_turn_context(turn_token)
        logs.reset_session_context(session_tokens)
        logs.reset_request_context(request_token)

    api = _read_single_jsonl(tmp_path / "api")
    llm = _read_single_jsonl(tmp_path / "sessions" / "session-1" / "llm")
    operation = _read_single_jsonl(tmp_path / "sessions" / "session-1" / "ops")
    tool = _read_single_jsonl(tmp_path / "sessions" / "session-1" / "tools")
    assert api["request_id"] == api["trace_id"] == "trace-1"
    assert llm["trace_id"] == operation["trace_id"] == tool["trace_id"] == "trace-1"
    assert llm["turn_id"] == operation["turn_id"] == tool["turn_id"] == "session-1:42"
    assert llm["total_tokens"] == 10
    assert tool["token_usage"]["total_tokens"] == 4
    assert api["ttfb_ms"] == 12.5
    assert api["stream_total_ms"] == 25.0


def test_a2a_task_preserves_trace_and_records_duration() -> None:
    async def scenario() -> None:
        async def run_query(**kwargs: Any) -> dict[str, Any]:
            kwargs["progress_callback"](
                {
                    "event": "step",
                    "detail": "working",
                    "tool_name": "orchestrator",
                }
            )
            return {
                "output": "answer",
                "output_type": "final",
                "confidence": 0.9,
                "tool_observations": [{"tool_name": "rag_search"}],
                "evidence_summary": {"evidence_count": 1},
                "question_plan": {"plan_id": "plan-1"},
                "evidence_bundle": {"bundle_id": "bundle-1"},
                "telemetry": {
                    "prompt_tokens": 8,
                    "completion_tokens": 2,
                    "total_tokens": 10,
                },
            }

        service = A2AService(run_query=run_query)
        task = await service.asend_message(
            {
                "parts": [{"kind": "text", "text": "question"}],
                "metadata": {},
                "contextId": "session-1",
                "userId": "user-1",
            },
            trace_id="trace-1",
        )
        task_id = task["id"]
        for _ in range(20):
            snapshot = await service.aget_task_updates(task_id)
            assert snapshot is not None
            if snapshot["terminal"]:
                break
            await asyncio.sleep(0)
        assert snapshot["terminal"] is True
        completed = snapshot["task"]
        telemetry = next(
            artifact["parts"][0]["data"]
            for artifact in completed["artifacts"]
            if artifact["name"] == "telemetry"
        )
        tools = next(
            artifact["parts"][0]["data"]
            for artifact in completed["artifacts"]
            if artifact["name"] == "tool_observations"
        )
        assert telemetry["trace_id"] == "trace-1"
        assert telemetry["duration_ms"] >= 0
        assert tools[0]["trace_id"] == "trace-1"
        artifact_names = {artifact["name"] for artifact in completed["artifacts"]}
        assert {"question_plan", "evidence_bundle", "evidence_summary"}.issubset(
            artifact_names
        )
        assert any(
            event.get("trace_id") == "trace-1"
            for event in completed["events"]
            if event["event"] == "step"
        )

    asyncio.run(scenario())
