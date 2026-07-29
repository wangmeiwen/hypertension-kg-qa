"""公开问答流事件契约测试。"""

import json

import pytest
from pydantic import ValidationError

from app.backend.domain.stream_events import (
    DoneStreamEvent,
    ReplyStreamEvent,
    STREAM_EVENT_SCHEMA_VERSION,
    stream_event_json_line,
    stream_event_json_schema,
    validate_stream_event,
)

pytestmark = pytest.mark.unit


def test_reply_and_done_events_keep_body_and_terminal_metadata_separate() -> None:
    reply = json.loads(
        stream_event_json_line(ReplyStreamEvent(delta="回答片段", task_id="task-1"))
    )
    done = json.loads(
        stream_event_json_line(DoneStreamEvent(status="done", task_id="task-1"))
    )

    assert reply == {"type": "reply", "delta": "回答片段", "task_id": "task-1"}
    assert "msg" not in done
    assert "delta" not in done


def test_stream_contract_rejects_legacy_or_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        validate_stream_event(
            {"type": "reply", "msg": "旧正文事件", "task_id": "task-1"}
        )

    with pytest.raises(ValidationError):
        validate_stream_event(
            {
                "type": "done",
                "status": "done",
                "task_id": "task-1",
                "msg": "不应重复出现的正文",
            }
        )


def test_stream_contract_exports_versioned_discriminated_json_schema() -> None:
    schema = stream_event_json_schema()

    assert schema["$id"] == "/schemas/stream-events.json"
    assert schema["x-schema-version"] == STREAM_EVENT_SCHEMA_VERSION
    assert schema["discriminator"]["propertyName"] == "type"
    assert set(schema["discriminator"]["mapping"]) == {
        "job",
        "session",
        "msg",
        "reply",
        "done",
        "error",
        "persisted",
    }


async def test_failed_a2a_task_emits_error_without_done() -> None:
    from app.backend.services.a2a_event_stream_service import A2AEventStreamService

    class FailedA2AService:
        async def asend_message(self, message, request_id):
            _ = message, request_id
            return {"id": "task-1"}

        async def aget_task_updates(self, task_id, cursor):
            _ = task_id, cursor
            return {
                "events": [],
                "next_cursor": 0,
                "state": "failed",
                "terminal": True,
                "task": {
                    "status": {
                        "message": {
                            "parts": [{"kind": "text", "text": "上游模型调用失败"}],
                        }
                    }
                },
            }

    service = A2AEventStreamService(get_a2a=lambda: FailedA2AService())
    events = [
        event
        async for event in service.generate_a2a_stream_events(
            input_text="问题",
            user_id="user-1",
            session_id="session-1",
            request_id="request-1",
        )
    ]

    assert [event.type for event in events] == ["job", "error"]
    assert events[-1].message == "上游模型调用失败"


def test_ragas_stream_reader_accumulates_deltas_and_reports_explicit_errors(
    monkeypatch,
) -> None:
    from tests.ragas import run_eval_dataset

    class FakeResponse:
        @staticmethod
        def raise_for_status() -> None:
            return None

    monkeypatch.setattr(
        run_eval_dataset.requests, "post", lambda *args, **kwargs: FakeResponse()
    )
    monkeypatch.setattr(
        run_eval_dataset,
        "iter_json_lines",
        lambda response: iter(
            [
                {"type": "job", "task_id": "task-1"},
                {"type": "reply", "delta": "第一段"},
                {"type": "reply", "delta": "第二段"},
                {"type": "done", "status": "done", "traces": []},
            ]
        ),
    )

    succeeded = run_eval_dataset.run_stream_request(
        "http://localhost", "/process", "问题", 1.0
    )
    assert succeeded["answer"] == "第一段第二段"
    assert succeeded["status"] == "done"
    assert succeeded["error"] == ""

    monkeypatch.setattr(
        run_eval_dataset,
        "iter_json_lines",
        lambda response: iter(
            [
                {"type": "job", "task_id": "task-2"},
                {"type": "error", "code": "a2a_task_failed", "message": "模型失败"},
            ]
        ),
    )
    failed = run_eval_dataset.run_stream_request(
        "http://localhost", "/process", "问题", 1.0
    )
    assert failed["status"] == "failed"
    assert failed["error"] == "模型失败"
