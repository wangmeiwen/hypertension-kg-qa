import asyncio
import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class _FakeStreamResponse:
    """模拟 httpx stream 上下文管理器和逐行 SSE 输出。"""

    def __init__(self, lines):
        self._lines = list(lines)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def raise_for_status(self):
        return None

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeAsyncClient:
    """记录 DashScope 异步请求参数的 httpx.AsyncClient 替身。"""

    lines = []
    payloads = []
    post_payloads = []
    instances = []

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.is_closed = False
        self.__class__.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def aclose(self):
        self.is_closed = True

    async def post(self, url, headers=None, json=None, timeout=None):
        self.__class__.post_payloads.append(
            {
                "url": url,
                "headers": headers or {},
                "json": json or {},
                "timeout": timeout,
            }
        )
        return _FakeJsonResponse(
            {
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            }
        )

    def stream(self, method, url, headers=None, json=None, timeout=None):
        self.__class__.payloads.append(
            {
                "method": method,
                "url": url,
                "headers": headers or {},
                "json": json or {},
                "timeout": timeout,
            }
        )
        return _FakeStreamResponse(self.__class__.lines)


class _FakeJsonResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_llm_client_parses_dashscope_stream(monkeypatch):
    """验证 DashScope 兼容流式响应能解析 delta 和 usage。"""
    monkeypatch.setenv("MODEL_API_KEY", "test-key")
    monkeypatch.setenv(
        "MODEL_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    monkeypatch.setenv("MODEL_NAME", "qwen-plus")

    from app.a2a.agent import llm_client as llm_client_module

    _FakeAsyncClient.lines = [
        'data: {"choices":[{"delta":{"content":"你"},"finish_reason":null}]}',
        'data: {"choices":[{"delta":{"content":"好"},"finish_reason":"stop"}],"usage":{"prompt_tokens":8,"completion_tokens":2,"total_tokens":10}}',
        "data: [DONE]",
    ]
    _FakeAsyncClient.payloads = []
    _FakeAsyncClient.post_payloads = []
    _FakeAsyncClient.instances = []
    monkeypatch.setattr(llm_client_module.httpx, "AsyncClient", _FakeAsyncClient)

    client = llm_client_module.LLMClient()

    async def _collect():
        rows = []
        async for event in client.stream_chat_with_tokens_async(
            messages=[{"role": "user", "content": "你好"}],
            timeout=5,
            actor_role="test",
            actor_name="pytest",
        ):
            rows.append(event)
        return rows

    events = asyncio.run(_collect())

    assert [event.type for event in events] == ["delta", "delta", "done"]
    assert "".join(event.delta for event in events if event.type == "delta") == "你好"
    assert events[-1].prompt_tokens == 8
    assert events[-1].completion_tokens == 2
    assert events[-1].total_tokens == 10
    assert _FakeAsyncClient.payloads[0]["json"]["stream"] is True
    assert _FakeAsyncClient.payloads[0]["json"]["stream_options"] == {
        "include_usage": True
    }
    assert len(_FakeAsyncClient.instances) == 1


def test_llm_client_reuses_async_client_for_non_stream_calls(monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", "test-key")
    monkeypatch.setenv(
        "MODEL_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    monkeypatch.setenv("MODEL_NAME", "qwen-plus")

    from app.a2a.agent import llm_client as llm_client_module

    _FakeAsyncClient.lines = []
    _FakeAsyncClient.payloads = []
    _FakeAsyncClient.post_payloads = []
    _FakeAsyncClient.instances = []
    monkeypatch.setattr(llm_client_module.httpx, "AsyncClient", _FakeAsyncClient)

    client = llm_client_module.LLMClient()

    async def _call_twice():
        first = await client.chat_with_tokens_async(
            messages=[{"role": "user", "content": "one"}], timeout=3
        )
        second = await client.chat_with_tokens_async(
            messages=[{"role": "user", "content": "two"}], timeout=4
        )
        await client.aclose()
        return first, second

    first, second = asyncio.run(_call_twice())

    assert first.content == "ok"
    assert second.content == "ok"
    assert len(_FakeAsyncClient.instances) == 1
    assert [item["timeout"] for item in _FakeAsyncClient.post_payloads] == [3, 4]


def test_llm_client_reuses_async_client_for_stream_calls(monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", "test-key")
    monkeypatch.setenv(
        "MODEL_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    monkeypatch.setenv("MODEL_NAME", "qwen-plus")

    from app.a2a.agent import llm_client as llm_client_module

    _FakeAsyncClient.lines = [
        'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}',
        "data: [DONE]",
    ]
    _FakeAsyncClient.payloads = []
    _FakeAsyncClient.post_payloads = []
    _FakeAsyncClient.instances = []
    monkeypatch.setattr(llm_client_module.httpx, "AsyncClient", _FakeAsyncClient)

    client = llm_client_module.LLMClient()

    async def _collect_twice():
        rows = []
        for _ in range(2):
            async for event in client.stream_chat_with_tokens_async(
                messages=[{"role": "user", "content": "hi"}], timeout=6
            ):
                rows.append(event)
        await client.aclose()
        return rows

    events = asyncio.run(_collect_twice())

    assert [event.type for event in events] == ["delta", "done", "delta", "done"]
    assert len(_FakeAsyncClient.instances) == 1
    assert [item["timeout"] for item in _FakeAsyncClient.payloads] == [6, 6]


def test_llm_client_recreates_async_client_after_aclose(monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", "test-key")
    monkeypatch.setenv(
        "MODEL_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    monkeypatch.setenv("MODEL_NAME", "qwen-plus")

    from app.a2a.agent import llm_client as llm_client_module

    _FakeAsyncClient.lines = []
    _FakeAsyncClient.payloads = []
    _FakeAsyncClient.post_payloads = []
    _FakeAsyncClient.instances = []
    monkeypatch.setattr(llm_client_module.httpx, "AsyncClient", _FakeAsyncClient)

    client = llm_client_module.LLMClient()

    async def _call_close_call():
        await client.chat_with_tokens_async(
            messages=[{"role": "user", "content": "one"}]
        )
        await client.aclose()
        await client.chat_with_tokens_async(
            messages=[{"role": "user", "content": "two"}]
        )
        await client.aclose()

    asyncio.run(_call_close_call())

    assert len(_FakeAsyncClient.instances) == 2
    assert all(instance.is_closed for instance in _FakeAsyncClient.instances)


def test_llm_client_reuses_sync_session(monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", "test-key")
    monkeypatch.setenv(
        "MODEL_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    monkeypatch.setenv("MODEL_NAME", "qwen-plus")

    from app.a2a.agent import llm_client as llm_client_module

    class FakeSession:
        instances = []
        payloads = []

        def __init__(self):
            self.closed = False
            self.__class__.instances.append(self)

        def post(self, url, headers=None, data=None, timeout=None):
            self.__class__.payloads.append(
                {
                    "url": url,
                    "headers": headers or {},
                    "data": data,
                    "timeout": timeout,
                }
            )
            return _FakeJsonResponse(
                {
                    "choices": [
                        {"message": {"content": "ok"}, "finish_reason": "stop"}
                    ],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                    },
                }
            )

        def close(self):
            self.closed = True

    monkeypatch.setattr(llm_client_module.requests, "Session", FakeSession)

    client = llm_client_module.LLMClient()
    first = client.chat_with_tokens(
        messages=[{"role": "user", "content": "one"}], timeout=7
    )
    second = client.chat_with_tokens(
        messages=[{"role": "user", "content": "two"}], timeout=8
    )
    asyncio.run(client.aclose())

    assert first.content == "ok"
    assert second.content == "ok"
    assert len(FakeSession.instances) == 1
    assert FakeSession.instances[0].closed is True
    assert [item["timeout"] for item in FakeSession.payloads] == [7, 8]


class _FakeA2AService:
    def __init__(self):
        self._calls = 0

    async def asend_message(self, message, request_id):
        return {"id": "task-1"}

    async def aget_task_updates(self, task_id, cursor):
        self._calls += 1
        if self._calls == 1:
            return {
                "events": [
                    {
                        "event": "answer_delta",
                        "detail": "你",
                        "metadata": {"delta": "你"},
                    },
                    {
                        "event": "answer_delta",
                        "detail": "好",
                        "metadata": {"delta": "好"},
                    },
                ],
                "next_cursor": 2,
                "state": "running",
                "terminal": False,
            }
        return {
            "events": [],
            "next_cursor": 2,
            "state": "completed",
            "terminal": True,
            "task": {
                "artifacts": [
                    {
                        "name": "final_answer",
                        "parts": [{"kind": "text", "text": "你好"}],
                    }
                ],
                "status": {
                    "message": {"parts": [{"kind": "text", "text": "task_completed"}]}
                },
            },
        }


def test_event_stream_forwards_answer_delta_without_duplicate_final_reply():
    from app.backend.services.a2a_event_stream_service import A2AEventStreamService

    service = A2AEventStreamService(get_a2a=lambda: _FakeA2AService())

    async def _collect():
        rows = []
        async for event in service.generate_a2a_stream_events(
            input_text="你好",
            user_id="u1",
            session_id="s1",
            request_id="r1",
        ):
            rows.append(event)
        return rows

    events = asyncio.run(_collect())

    assert events[0].type == "job"
    assert [event.type for event in events[1:]] == ["msg", "reply", "reply", "done"]
    assert [event.delta for event in events if event.type == "reply"] == ["你", "好"]
    assert "msg" not in events[-1].model_dump()
