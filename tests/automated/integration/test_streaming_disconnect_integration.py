"""NDJSON StreamingResponse 的 ASGI 断连传播测试。"""

import asyncio

import pytest

from app.backend.infrastructure.responses import ndjson_streaming_response


pytestmark = pytest.mark.integration


async def test_asgi_disconnect_cancels_ndjson_body_iterator() -> None:
    """Uvicorn 使用的 ASGI 2.3 断连应取消正在等待的流生成器。"""
    reply_sent = asyncio.Event()
    body_cancelled = asyncio.Event()
    blocker = asyncio.Future()
    request_delivered = False
    sent_messages: list[dict] = []

    async def event_lines():
        try:
            yield '{"type":"session","session_id":"session-1","user_message_id":1}\n'
            yield '{"type":"reply","delta":"部分回答","task_id":"task-1"}\n'
            await blocker
        except asyncio.CancelledError:
            body_cancelled.set()
            raise

    async def receive() -> dict:
        nonlocal request_delivered
        if not request_delivered:
            request_delivered = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await reply_sent.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict) -> None:
        sent_messages.append(message)
        body = message.get("body")
        if isinstance(body, bytes) and b'"type":"reply"' in body:
            reply_sent.set()

    response = ndjson_streaming_response(event_lines())
    await response(
        {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/process_with_session",
            "raw_path": b"/process_with_session",
            "query_string": b"",
            "root_path": "",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        },
        receive,
        send,
    )

    await asyncio.wait_for(body_cancelled.wait(), timeout=1)
    response_bodies = [
        message["body"]
        for message in sent_messages
        if message["type"] == "http.response.body"
    ]
    assert any(b'"type":"reply"' in body for body in response_bodies)
    assert not any(message.get("more_body") is False for message in sent_messages)
