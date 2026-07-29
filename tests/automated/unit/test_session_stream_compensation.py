"""会话流取消、持久化与记忆刷新补偿测试。"""

import asyncio
import json
from typing import Any

import pytest

from app.backend.services.session_stream_service import SessionStreamService
from tests.conftest import await_call


pytestmark = pytest.mark.unit


class NoopMemoryService:
    """记录后台记忆刷新是否实际执行。"""

    def __init__(self) -> None:
        self.refreshed = asyncio.Event()

    async def refresh_after_turn(
        self,
        session_id: str,
        assistant_message_id: int,
        turn_artifacts: dict[str, Any],
    ) -> dict[str, Any]:
        _ = session_id, assistant_message_id, turn_artifacts
        self.refreshed.set()
        return {}


def prepare_stream_test_module(monkeypatch, tmp_path):
    """隔离会话流测试的文件与观测副作用。"""
    import app.backend.services.session_stream_service as module

    operations: list[dict[str, Any]] = []
    monkeypatch.setattr(module, "_project_root", lambda: tmp_path)
    monkeypatch.setattr(module, "ensure_session_meta", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "_append_jsonl", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "log_perf_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "log_tool_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        module,
        "log_session_op",
        lambda **kwargs: operations.append(kwargs),
    )
    return operations


async def collect_stream(service: SessionStreamService) -> list[dict[str, Any]]:
    """收集一次固定会话流的 JSON 事件。"""
    return [
        json.loads(line)
        async for line in service.generate_data_with_session_lines(
            input_text="高血压如何管理",
            user_id="user-1",
            session_id="session-1",
            user_message_id=1,
            request_id="request-1",
            original_input_text="高血压如何管理",
            memory_context={},
        )
    ]


async def test_cancelled_stream_persists_partial_reply_once(
    monkeypatch,
    tmp_path,
) -> None:
    operations = prepare_stream_test_module(monkeypatch, tmp_path)
    persisted: list[str] = []
    errors: list[str] = []
    waiting = asyncio.Event()
    blocker = asyncio.Future()
    memory_service = NoopMemoryService()
    received: list[dict[str, Any]] = []

    class BlockingEventStream:
        async def iter_a2a_stream_events(self, **kwargs):
            _ = kwargs
            yield {"type": "reply", "delta": "请规律监测血压", "task_id": "task-1"}
            waiting.set()
            await blocker

    class MessageManager:
        @staticmethod
        async def add_message(
            session_id: str,
            role: str,
            content: str,
            think_content: str = "",
        ) -> dict[str, Any]:
            _ = session_id, role, think_content
            persisted.append(content)
            return {"id": 7}

    def log_error(endpoint: str, stage: str, exc: BaseException, **metadata) -> None:
        _ = endpoint, exc, metadata
        errors.append(stage)

    service = SessionStreamService(
        await_call=await_call,
        get_session_memory_service=lambda: memory_service,
        log_api_error=log_error,
        event_stream_service=BlockingEventStream(),
        message_manager=MessageManager,
    )

    async def consume_stream() -> None:
        async for line in service.generate_data_with_session_lines(
            input_text="高血压如何管理",
            user_id="user-1",
            session_id="session-1",
            user_message_id=1,
            request_id="request-1",
            original_input_text="高血压如何管理",
            memory_context={},
        ):
            received.append(json.loads(line))

    consumer = asyncio.create_task(consume_stream())
    await asyncio.wait_for(waiting.wait(), timeout=1)
    assert [event["type"] for event in received] == ["session", "reply"]
    assert not consumer.done()
    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer

    await asyncio.wait_for(memory_service.refreshed.wait(), timeout=1)
    assert len(persisted) == 1
    assert "generate_data_with_session_cancelled" in errors
    assert any(
        row.get("action") == "run_end" and row.get("status") == "cancelled"
        for row in operations
    )


async def test_persistence_exhaustion_emits_recoverable_error(
    monkeypatch,
    tmp_path,
) -> None:
    prepare_stream_test_module(monkeypatch, tmp_path)
    attempts = 0
    errors: list[tuple[str, int]] = []

    class CompletedEventStream:
        async def iter_a2a_stream_events(self, **kwargs):
            _ = kwargs
            yield {"type": "reply", "delta": "请规律监测血压", "task_id": "task-1"}
            yield {"type": "done", "status": "done", "task_id": "task-1"}

    class BrokenMessageManager:
        @staticmethod
        async def add_message(*args, **kwargs):
            nonlocal attempts
            _ = args, kwargs
            attempts += 1
            raise RuntimeError("database unavailable")

    def log_error(endpoint: str, stage: str, exc: BaseException, **metadata) -> None:
        _ = endpoint, exc
        errors.append((stage, int(metadata.get("attempt") or 0)))

    service = SessionStreamService(
        await_call=await_call,
        get_session_memory_service=NoopMemoryService,
        log_api_error=log_error,
        event_stream_service=CompletedEventStream(),
        message_manager=BrokenMessageManager,
    )
    events = await collect_stream(service)

    assert attempts == 2
    assert events[-1] == {
        "type": "error",
        "code": "assistant_persist_failed",
        "message": "回答已生成，但会话保存失败，请稍后重试",
        "task_id": "task-1",
        "recoverable": True,
    }
    assert not any(event["type"] in {"persisted", "done"} for event in events)
    assert errors == [
        ("assistant_persist_failed_after_done", 1),
        ("assistant_persist_failed_after_done", 2),
    ]


async def test_transient_persistence_and_memory_schedule_failures_keep_done(
    monkeypatch,
    tmp_path,
) -> None:
    prepare_stream_test_module(monkeypatch, tmp_path)
    attempts = 0
    errors: list[str] = []

    class CompletedEventStream:
        async def iter_a2a_stream_events(self, **kwargs):
            _ = kwargs
            yield {"type": "reply", "delta": "请规律监测血压", "task_id": "task-1"}
            yield {"type": "done", "status": "done", "task_id": "task-1"}

    class TransientMessageManager:
        @staticmethod
        async def add_message(*args, **kwargs):
            nonlocal attempts
            _ = args, kwargs
            attempts += 1
            if attempts == 1:
                raise RuntimeError("temporary database error")
            return {"id": 9}

    def fail_create_task(coro, *, name: str):
        _ = coro, name
        raise RuntimeError("task scheduler unavailable")

    def log_error(endpoint: str, stage: str, exc: BaseException, **metadata) -> None:
        _ = endpoint, exc, metadata
        errors.append(stage)

    service = SessionStreamService(
        await_call=await_call,
        get_session_memory_service=NoopMemoryService,
        log_api_error=log_error,
        event_stream_service=CompletedEventStream(),
        message_manager=TransientMessageManager,
        create_task=fail_create_task,
    )
    events = await collect_stream(service)

    assert attempts == 2
    assert [event["type"] for event in events[-2:]] == ["persisted", "done"]
    assert events[-2]["assistant_message_id"] == 9
    assert "assistant_persist_failed_after_done" in errors
    assert "memory_refresh_schedule_failed" in errors


async def test_memory_refresh_failure_does_not_change_persisted_done_sequence(
    monkeypatch,
    tmp_path,
) -> None:
    prepare_stream_test_module(monkeypatch, tmp_path)
    memory_failure_logged = asyncio.Event()
    errors: list[str] = []

    class CompletedEventStream:
        async def iter_a2a_stream_events(self, **kwargs):
            _ = kwargs
            yield {"type": "reply", "delta": "请规律监测血压", "task_id": "task-1"}
            yield {"type": "done", "status": "done", "task_id": "task-1"}

    class MessageManager:
        @staticmethod
        async def add_message(*args, **kwargs):
            _ = args, kwargs
            return {"id": 11}

    class BrokenMemoryService:
        async def refresh_after_turn(self, *args, **kwargs):
            _ = args, kwargs
            raise RuntimeError("memory refresh failed")

    def log_error(endpoint: str, stage: str, exc: BaseException, **metadata) -> None:
        _ = endpoint, exc, metadata
        errors.append(stage)
        if stage == "memory_refresh_failed":
            memory_failure_logged.set()

    service = SessionStreamService(
        await_call=await_call,
        get_session_memory_service=BrokenMemoryService,
        log_api_error=log_error,
        event_stream_service=CompletedEventStream(),
        message_manager=MessageManager,
    )
    events = await collect_stream(service)

    await asyncio.wait_for(memory_failure_logged.wait(), timeout=1)
    assert [event["type"] for event in events[-2:]] == ["persisted", "done"]
    assert errors == ["memory_refresh_failed"]
