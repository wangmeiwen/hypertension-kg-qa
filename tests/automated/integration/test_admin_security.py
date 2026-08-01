"""管理端权限、限流和审计集成测试。"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from app.backend.config import BackendConfig
from app.backend.dependencies.admin_auth import (
    require_admin_access,
    require_admin_login,
)
from app.backend.infrastructure.errors import ForbiddenError, RateLimitedError
from app.backend.infrastructure.rate_limiter import InMemorySlidingWindowRateLimiter
from app.backend.infrastructure.responses import (
    forbidden_response,
    rate_limited_response,
)
from app.backend.infrastructure.runtime_container import BackendRuntimeContainer
from app.backend.middleware.request_timing import request_timing_middleware
from app.backend.security.admin_access import (
    ADMIN_ALL_PERMISSIONS,
    resolve_admin_access_rule,
)


def _security_app(session: dict[str, Any]) -> FastAPI:
    config = BackendConfig(
        allowed_origins=[],
        upload_dir=Path("uploads"),
        preload_kg_entity_relation_embedder=False,
        rate_limit_window_seconds=60.0,
        rate_limit_max_keys=1000,
        heavy_endpoint_limits={},
    )
    app = FastAPI()
    runtime = BackendRuntimeContainer(config=config)
    runtime.rate_limiter = InMemorySlidingWindowRateLimiter(max_keys=1000)
    app.state.runtime = runtime
    app.dependency_overrides[require_admin_login] = lambda: session
    app.middleware("http")(request_timing_middleware)

    @app.exception_handler(ForbiddenError)
    async def _forbidden(_: Request, exc: ForbiddenError):
        return forbidden_response(str(exc))

    @app.exception_handler(RateLimitedError)
    async def _rate_limited(_: Request, exc: RateLimitedError):
        return rate_limited_response(
            endpoint=exc.endpoint,
            dimension=exc.dimension,
            retry_after_ms=exc.retry_after_ms,
            trace_id=exc.trace_id,
            limit=exc.limit,
            window_seconds=exc.window_seconds,
        )

    @app.get(
        "/admin/graph/jobs",
        dependencies=[Depends(require_admin_access)],
    )
    async def _read_graph_jobs() -> dict[str, bool]:
        return {"ok": True}

    @app.post(
        "/admin/graph/nodes",
        dependencies=[Depends(require_admin_access)],
    )
    async def _write_graph_node() -> dict[str, bool]:
        return {"ok": True}

    @app.post(
        "/admin/graph/jobs/embed-milvus",
        dependencies=[Depends(require_admin_access)],
    )
    async def _operate_graph_embedding() -> dict[str, bool]:
        return {"ok": True}

    return app


def test_admin_access_rules_distinguish_read_write_and_operate() -> None:
    assert resolve_admin_access_rule("GET", "/admin/graph/jobs").permission == (
        "graph:read"
    )
    assert resolve_admin_access_rule("POST", "/admin/graph/nodes").permission == (
        "graph:write"
    )
    assert (
        resolve_admin_access_rule("POST", "/admin/graph/jobs/embed-milvus").permission
        == "graph:operate"
    )
    assert (
        resolve_admin_access_rule("POST", "/admin/graph/execute-cypher").permission
        == "graph:read"
    )


def test_admin_login_session_receives_explicit_permissions(monkeypatch) -> None:
    import app.backend.security.admin_auth as auth

    monkeypatch.setattr(
        auth,
        "DEFAULT_ADMIN",
        {"username": "admin", "password": "secret"},
    )
    token = auth.verify_login("admin", "secret")
    assert token is not None
    token_handle = auth.set_current_admin_token(token)
    try:
        session = auth.get_current_admin_session()
        assert session is not None
        assert frozenset(session["permissions"]) == ADMIN_ALL_PERMISSIONS
        assert auth.logout() is True
    finally:
        auth.reset_current_admin_token(token_handle)


def test_admin_access_denies_missing_permission() -> None:
    client = TestClient(
        _security_app(
            {
                "username": "reader",
                "permissions": ["graph:read"],
            }
        )
    )

    assert client.get("/admin/graph/jobs").status_code == 200
    denied = client.post("/admin/graph/nodes")
    assert denied.status_code == 403
    assert denied.json()["error"] == "permission_denied:graph:write"


def test_admin_expensive_operation_is_rate_limited() -> None:
    client = TestClient(
        _security_app(
            {
                "username": "operator",
                "permissions": ["graph:operate"],
            }
        )
    )

    for _ in range(6):
        assert client.post("/admin/graph/jobs/embed-milvus").status_code == 200
    limited = client.post("/admin/graph/jobs/embed-milvus")
    assert limited.status_code == 429
    assert limited.json()["endpoint"] == "admin:graph:operate"
    assert limited.json()["dimension"] == "admin"


def test_admin_audit_excludes_query_values_and_records_decision(caplog) -> None:
    client = TestClient(
        _security_app(
            {
                "username": "auditor",
                "permissions": ["graph:read"],
            }
        )
    )

    with caplog.at_level(logging.INFO, logger="admin_audit"):
        response = client.get("/admin/graph/jobs?secret=never-log-this")

    assert response.status_code == 200
    audit_message = next(
        record.getMessage() for record in caplog.records if record.name == "admin_audit"
    )
    payload = json.loads(audit_message.removeprefix("admin_audit "))
    assert payload["actor"] == "auditor"
    assert payload["permission"] == "graph:read"
    assert payload["outcome"] == "allowed"
    assert payload["status_code"] == 200
    assert "never-log-this" not in audit_message
