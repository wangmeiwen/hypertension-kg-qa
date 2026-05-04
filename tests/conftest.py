from __future__ import annotations

import shutil
import sys
import uuid
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.backend.app_factory import BackendAppTestOverrides, create_app
from app.backend.dependencies.admin_auth import require_admin_login
from app.backend.services.startup_runtime_service import StartupRuntimeBehavior
from app.backend.testing.deterministic_support import build_deterministic_test_overrides


async def await_call(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    result = func(*args, **kwargs)
    if hasattr(result, "__await__"):
        return await result
    return result


def log_api_error(*_: Any, **__: Any) -> None:
    return None


class MemoryServiceStub:
    def __init__(self) -> None:
        self.cleared: list[str] = []
        self.rebuilt: list[str] = []

    async def clear_memory(self, session_id: str) -> None:
        self.cleared.append(session_id)

    async def rebuild_from_messages(self, session_id: str) -> dict[str, Any]:
        self.rebuilt.append(session_id)
        return {"session_id": session_id, "rebuilt": True}

    async def build_runtime_context(self, session_id: str, input_text: str, message_id: int) -> dict[str, Any]:
        return {
            "original_input_text": input_text,
            "memory_context": {"session_id": session_id, "message_id": message_id},
        }


class A2AServiceStub:
    def __init__(self) -> None:
        self.cancelled: list[tuple[str, str]] = []
        self.tasks: dict[str, dict[str, Any]] = {
            "task-1": {
                "id": "task-1",
                "contextId": "session-1",
                "metadata": {"user_id": "user-1"},
                "status": {"state": "running"},
            }
        }

    def get_agent_card(self) -> dict[str, Any]:
        return {"name": "pytest-agent"}

    async def asend_message(self, message: dict[str, Any], request_id: str) -> dict[str, Any]:
        self.tasks.setdefault(
            "task-1",
            {
                "id": "task-1",
                "contextId": message.get("contextId"),
                "metadata": message.get("metadata") or {},
                "status": {"state": "running"},
            },
        )
        _ = request_id
        return {"id": "task-1"}

    async def ahandle_rpc(self, data: dict[str, Any], trace_id: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": data.get("id"), "result": {"trace_id": trace_id}}

    async def alist_tasks(self, context_id: str = "", limit: int = 20) -> list[dict[str, Any]]:
        rows = list(self.tasks.values())
        if context_id:
            rows = [row for row in rows if row.get("contextId") == context_id]
        return rows[:limit]

    async def aget_task(self, task_id: str) -> dict[str, Any] | None:
        return self.tasks.get(task_id)

    async def aget_task_updates(self, task_id: str, cursor: int) -> dict[str, Any] | None:
        if task_id not in self.tasks:
            return None
        return {
            "events": [{"event": "progress", "detail": "step"}] if cursor == 0 else [],
            "next_cursor": 1,
            "state": "completed",
            "terminal": True,
            "task": self.tasks[task_id],
        }

    async def acancel_task(self, task_id: str, reason: str) -> dict[str, Any]:
        if task_id not in self.tasks:
            return {"ok": False, "error": "task_not_found"}
        self.cancelled.append((task_id, reason))
        self.tasks[task_id]["status"] = {"state": "canceled"}
        return {"ok": True, "task": self.tasks[task_id]}


@pytest.fixture()
def test_run_id() -> str:
    return f"pytest_{uuid.uuid4().hex[:12]}"


@pytest.fixture()
def test_data_prefix(test_run_id: str) -> str:
    return f"{test_run_id}_"


@pytest.fixture()
def memory_service_stub() -> MemoryServiceStub:
    return MemoryServiceStub()


@pytest.fixture()
def a2a_service_stub() -> A2AServiceStub:
    return A2AServiceStub()


@pytest.fixture()
def temp_upload_dir(tmp_path: Path) -> Path:
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    return upload_dir


@pytest.fixture()
def fastapi_client_factory() -> Callable[[FastAPI], TestClient]:
    def _factory(app: FastAPI) -> TestClient:
        return TestClient(app)

    return _factory


@pytest.fixture()
def deterministic_test_overrides() -> BackendAppTestOverrides:
    return build_deterministic_test_overrides()


@pytest.fixture()
def app_client_factory() -> Callable[..., TestClient]:
    def _factory(*, overrides: BackendAppTestOverrides | None = None, require_admin: bool = False) -> TestClient:
        resolved_overrides = overrides or build_deterministic_test_overrides()
        if overrides is None:
            resolved_overrides.startup_behavior = StartupRuntimeBehavior(
                initialize_database=False,
                check_databases=False,
                prewarm_retrieval_resources=False,
                bootstrap_mcp=True,
                bootstrap_a2a=True,
                close_neo4j_on_shutdown=False,
            )
        app = create_app(test_overrides=resolved_overrides)
        if require_admin:
            app.dependency_overrides[require_admin_login] = lambda: None
        return TestClient(app)

    return _factory


@pytest.fixture()
def admin_app_client(app_client_factory: Callable[..., TestClient]) -> TestClient:
    return app_client_factory(require_admin=True)


@pytest.fixture()
def cleanup_mysql_prefix() -> Callable[[str], Awaitable[None]]:
    async def _cleanup(prefix: str) -> None:
        import asyncmy

        like_prefix = f"{prefix}%"
        conn = await asyncmy.connect(
            host=os.getenv("MYSQL_HOST", "127.0.0.1"),
            port=int(os.getenv("MYSQL_PORT", "3306")),
            user=os.getenv("MYSQL_USER", "root"),
            password=os.getenv("MYSQL_PASSWORD", ""),
            database=os.getenv("MYSQL_DATABASE", "hypertension_kg"),
            autocommit=True,
        )
        try:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    "DELETE FROM sessions WHERE user_id LIKE %s OR id LIKE %s",
                    (like_prefix, like_prefix),
                )
        finally:
            conn.close()

    return _cleanup


@pytest.fixture()
def cleanup_session_artifacts() -> Callable[[str], None]:
    def _cleanup(session_id: str) -> None:
        safe_id = "".join(ch if (ch.isalnum() or ch in {"-", "_"}) else "_" for ch in str(session_id or "")).strip("_")
        if not safe_id:
            return
        sessions_root = ROOT / "logs" / "sessions" / safe_id
        if sessions_root.exists():
            shutil.rmtree(sessions_root, ignore_errors=True)

    return _cleanup
