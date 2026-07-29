from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.backend.dependencies.admin_auth import require_admin_login
from app.backend.infrastructure.errors import UnauthorizedError


pytestmark = pytest.mark.integration


def allow_admin(app: FastAPI) -> None:
    """在测试 app 中绕过管理员登录依赖。"""
    app.dependency_overrides[require_admin_login] = lambda: None


def install_unauthorized_handler(app: FastAPI) -> None:
    """安装 UnauthorizedError 到 401 JSON 响应的测试异常处理器。"""

    @app.exception_handler(UnauthorizedError)
    async def _handle_unauthorized(_, exc: UnauthorizedError) -> JSONResponse:
        return JSONResponse(status_code=401, content={"ok": False, "error": str(exc)})


def test_admin_auth_router_success_failure_and_logout(monkeypatch) -> None:
    import app.backend.services.admin_auth_service as service_module
    from app.backend.routers.admin_auth import create_admin_auth_router
    from app.backend.services.admin_auth_service import AdminAuthService

    state = {"logged_in": False, "user": None}

    def fake_verify_login(username: str, password: str) -> str | None:
        if username == "admin" and password == "secret":
            state["logged_in"] = True
            state["user"] = username
            return "token-1"
        return None

    monkeypatch.setattr(service_module, "verify_login", fake_verify_login)
    monkeypatch.setattr(service_module, "is_logged_in", lambda: state["logged_in"])
    monkeypatch.setattr(service_module, "get_current_admin_user", lambda: state["user"])
    monkeypatch.setattr(
        service_module,
        "logout",
        lambda: state.update({"logged_in": False, "user": None}) or True,
    )

    app = FastAPI()
    app.include_router(create_admin_auth_router(AdminAuthService()))
    client = TestClient(app)

    assert (
        client.post(
            "/admin/login", json={"username": "admin", "password": "bad"}
        ).json()["ok"]
        is False
    )
    login = client.post(
        "/admin/login", json={"username": "admin", "password": "secret"}
    )
    assert login.status_code == 200
    assert login.json()["token"] == "token-1"
    assert client.get("/admin/status").json()["logged_in"] is True
    assert client.post("/admin/logout").json()["ok"] is True
    assert client.get("/admin/status").json()["logged_in"] is False


def test_admin_runtime_router_protected_routes() -> None:
    from app.backend.routers.admin_runtime import create_admin_runtime_router

    class RuntimeServiceStub:
        async def admin_runtime_flags(self) -> dict[str, Any]:
            return {"ok": True, "data": {"flags": {}}}

        async def admin_perf_baseline(self, days: int = 1) -> dict[str, Any]:
            return {"ok": True, "data": {"window_days": days}}

        async def admin_qa_queue_status(self) -> dict[str, Any]:
            return {"ok": True, "data": {"queue": []}}

        async def admin_cancel_qa_job(self, job_id: str) -> dict[str, Any]:
            return {"ok": True, "job_id": job_id}

        async def admin_get_stats(self) -> dict[str, Any]:
            return {"ok": True, "data": {"total_sessions": 0}}

    app = FastAPI()
    app.include_router(create_admin_runtime_router(RuntimeServiceStub()))
    install_unauthorized_handler(app)
    client = TestClient(app)
    assert client.get("/admin/runtime/flags").status_code == 401

    allow_admin(app)
    assert client.get("/admin/runtime/flags").json()["ok"] is True
    assert (
        client.get("/admin/perf/baseline", params={"days": 3}).json()["data"][
            "window_days"
        ]
        == 3
    )
    assert client.get("/admin/qa/queue/status").json()["data"]["queue"] == []
    assert client.post("/admin/qa/jobs/job-1/cancel").json()["job_id"] == "job-1"
    assert client.get("/admin/stats").json()["data"]["total_sessions"] == 0


def test_graph_public_router_routes() -> None:
    from app.backend.routers.graph_public import create_graph_public_router

    class GraphPublicServiceStub:
        async def graph_visualization(
            self, keyword: str = "", limit: int = 200
        ) -> dict[str, Any]:
            return {
                "ok": True,
                "keyword": keyword,
                "limit": limit,
                "nodes": [],
                "links": [],
            }

        async def get_entity_detail(self, entity_id: str) -> dict[str, Any]:
            return {"ok": True, "entity": {"id": entity_id}}

    app = FastAPI()
    app.include_router(create_graph_public_router(GraphPublicServiceStub()))
    client = TestClient(app)

    graph = client.get("/graph/visualization", params={"keyword": "hyper", "limit": 12})
    assert graph.status_code == 200
    assert graph.json()["keyword"] == "hyper"
    detail = client.get("/graph/entity/123")
    assert detail.json()["entity"]["id"] == "123"


def test_system_mcp_router_readiness_tools_invoke_and_rate_limit() -> None:
    from app.backend.routers.system_mcp import create_system_mcp_router

    class McpStub:
        """MCP router 测试用最小工具服务桩。"""

        async def list_tools(self) -> list[dict[str, Any]]:
            return [{"name": "search"}]

        async def ainvoke_batch(
            self, rows: list[dict[str, Any]]
        ) -> list[dict[str, Any]]:
            return [{"ok": True, "result": rows[0]["payload"], "meta": {}}]

    async def await_call(func, *args, **kwargs):
        """兼容同步/异步依赖调用的本地测试辅助函数。"""
        result = func(*args, **kwargs)
        if hasattr(result, "__await__"):
            return await result
        return result

    def enforce_rate_limit(**kwargs):
        if kwargs.get("user_id") == "limited":
            return JSONResponse(
                status_code=429, content={"ok": False, "error": "rate_limited"}
            )
        return None

    app = FastAPI()
    app.include_router(
        create_system_mcp_router(
            await_call=await_call,
            build_readiness_report=lambda: {"level": "ready", "ready": True},
            get_mcp=lambda: McpStub(),
            log_api_error=lambda *args, **kwargs: None,
            get_trace_id=lambda request=None: "trace-1",
            enforce_rate_limit=enforce_rate_limit,
            rate_limiter=None,
            endpoint_limits={},
            window_seconds=60,
        )
    )
    client = TestClient(app)

    readiness = client.get("/health/readiness")
    assert readiness.status_code == 200
    assert readiness.json()["ok"] is True
    assert client.get("/agent/tools").json()["mcp_tools"][0]["name"] == "search"
    assert client.get("/mcp/tools").json()["tools"][0]["name"] == "search"
    assert (
        client.post(
            "/mcp/invoke", json={"tool_name": "search", "payload": {"q": "x"}}
        ).json()["meta"]["trace_id"]
        == "trace-1"
    )
    assert (
        client.post("/mcp/invoke", json={"payload": {}}).json()["error"]
        == "tool_name_required"
    )
    assert (
        client.post(
            "/mcp/invoke", json={"tool_name": "search", "user_id": "limited"}
        ).status_code
        == 429
    )


def test_a2a_router_task_lifecycle(a2a_service_stub) -> None:
    from app.backend.routers.a2a import create_a2a_router
    from app.backend.services.a2a_dispatch_service import A2ADispatchService

    dispatch = A2ADispatchService(
        get_a2a=lambda: a2a_service_stub,
        get_a2a_graph=lambda: a2a_service_stub,
        get_a2a_retrieval=lambda: a2a_service_stub,
        log_api_error=lambda *args, **kwargs: None,
    )
    app = FastAPI()
    app.include_router(
        create_a2a_router(
            lambda request=None: "trace-1", lambda: a2a_service_stub, dispatch
        )
    )
    client = TestClient(app)

    assert client.get("/a2a/agent-card").json()["agent_card"]["name"] == "pytest-agent"
    rpc = client.post("/a2a/rpc", json={"jsonrpc": "2.0", "id": 1, "method": "x"})
    assert rpc.json()["result"]["trace_id"] == "trace-1"
    assert client.get("/a2a/tasks").json()["tasks"][0]["id"] == "task-1"
    assert client.get("/a2a/tasks/task-1").json()["task"]["id"] == "task-1"
    assert client.get("/a2a/tasks/missing").status_code == 404
    stream = client.get("/a2a/tasks/task-1/events")
    assert stream.status_code == 200
    assert "event: done" in stream.text
    assert (
        client.post("/a2a/tasks/task-1/cancel", json={"reason": "test"}).json()["ok"]
        is True
    )
    assert client.post("/a2a/tasks/missing/cancel").status_code == 404


def test_query_chat_router_agent_and_streaming_routes() -> None:
    from app.backend.routers.query_chat import create_query_chat_router

    class ReadinessStub:
        async def ensure_core_ready(self, endpoint: str):
            return None

    class AgentRunServiceStub:
        def resolve_agent_run_mode(self, data: dict[str, Any]) -> str:
            if data.get("a2a_mode") == "bad":
                raise ValueError("bad_mode")
            return "auto"

        def bind_agent_trace_metadata(self, **kwargs) -> None:
            return None

        async def run_agent(self, **kwargs) -> dict[str, Any]:
            return {
                "ok": True,
                "answer": kwargs["input_text"],
                "trace_id": kwargs["trace_id"],
            }

    class SessionTurnServiceStub:
        def bind_process_session_trace_metadata(self, **kwargs) -> None:
            return None

        async def prepare_session_turn(self, **kwargs) -> dict[str, Any]:
            if kwargs["input_text"] == "bad":
                return {"ok": False, "error": "bad_input"}
            return {
                "ok": True,
                "user_id": "user-1",
                "session_id": "session-1",
                "user_message_id": 11,
                "original_input_text": kwargs["input_text"],
                "memory_context": {},
            }

    class StreamServiceStub:
        async def generate_data_with_session_lines(self, **kwargs):
            yield '{"type":"session","session_id":"session-1"}\n'
            yield '{"type":"done","status":"done"}\n'

    class EventStreamServiceStub:
        async def generate_data_lines(self, **kwargs):
            yield '{"type":"job","job_id":"job-1"}\n'
            yield '{"type":"done","status":"done"}\n'

    app = FastAPI()
    app.include_router(
        create_query_chat_router(
            agent_run_service=AgentRunServiceStub(),
            session_turn_service=SessionTurnServiceStub(),
            session_stream_service=StreamServiceStub(),
            event_stream_service=EventStreamServiceStub(),
            readiness_service=ReadinessStub(),
            get_trace_id=lambda request=None: "trace-1",
            enforce_rate_limit=lambda **kwargs: None,
            rate_limiter=None,
            endpoint_limits={},
            window_seconds=60,
        )
    )
    client = TestClient(app)

    process = client.post("/process", json={"input": "question", "user_id": "user-1"})
    assert process.status_code == 200
    assert process.headers["content-type"].startswith("application/x-ndjson")
    assert '"type":"done"' in process.text
    assert (
        client.post("/agent/run", json={"input": ""}).json()["error"]
        == "input_required"
    )
    assert (
        client.post("/agent/run", json={"input": "question"}).json()["answer"]
        == "question"
    )
    assert (
        client.post("/agent/run", json={"input": "question", "a2a_mode": "bad"}).json()[
            "ok"
        ]
        is False
    )
    with_session = client.post(
        "/process_with_session", json={"input": "question", "user_id": "user-1"}
    )
    assert with_session.headers["content-type"].startswith("application/x-ndjson")
    assert '"session_id":"session-1"' in with_session.text
    assert (
        client.post(
            "/process_with_session", json={"input": "bad", "user_id": "user-1"}
        ).json()["error"]
        == "bad_input"
    )
