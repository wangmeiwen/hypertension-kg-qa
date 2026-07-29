"""后端应用装配入口。"""

# ruff: noqa: E402

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import inspect
import json
import logging
import sys
from typing import Any, Callable

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.backend.config import build_backend_config
from app.backend.dependencies.guards import (
    enforce_heavy_api_rate_limit,
    is_session_owner,
)
from app.backend.dependencies.request_context import (
    get_trace_id as _resolve_trace_id_from_request,
)
from app.backend.infrastructure.errors import UnauthorizedError
from app.backend.infrastructure.responses import unauthorized_response
from app.backend.infrastructure.runtime_container import BackendRuntimeContainer
from app.backend.middleware.request_timing import request_timing_middleware
from app.backend.routers.a2a import create_a2a_router
from app.backend.routers.admin_auth import create_admin_auth_router
from app.backend.routers.admin_documents import create_admin_documents_router
from app.backend.routers.admin_graph import create_admin_graph_router
from app.backend.routers.admin_observability import create_admin_observability_router
from app.backend.routers.admin_runtime import create_admin_runtime_router
from app.backend.routers.admin_sessions import create_admin_sessions_router
from app.backend.routers.admin_storage import create_admin_storage_router
from app.backend.routers.graph_public import create_graph_public_router
from app.backend.routers.query_chat import create_query_chat_router
from app.backend.routers.sessions_history import create_sessions_router
from app.backend.routers.system_mcp import create_system_mcp_router
from app.backend.services.a2a_dispatch_service import A2ADispatchService
from app.backend.services.a2a_event_stream_service import A2AEventStreamService
from app.backend.services.a2a_runtime_service import A2ARuntimeService
from app.backend.services.admin_auth_service import AdminAuthService
from app.backend.services.admin_documents_service import AdminDocumentsService
from app.backend.services.admin_observability_service import AdminObservabilityService
from app.backend.services.admin_runtime_service import AdminRuntimeService
from app.backend.services.admin_sessions_service import AdminSessionsService
from app.backend.services.agent_run_service import AgentRunService
from app.backend.services.graph_admin_service import GraphAdminService
from app.backend.services.graph_public_service import GraphPublicService
from app.backend.services.readiness_service import ReadinessService
from app.backend.services.session_stream_service import SessionStreamService
from app.backend.services.session_turn_service import SessionTurnService
from app.backend.services.sessions_history_service import SessionsHistoryService
from app.backend.services.startup_runtime_service import (
    StartupRuntimeBehavior,
    StartupRuntimeService,
)
from app.backend.services.storage_schema_service import StorageSchemaService
from app.backend.infrastructure.rate_limiter import InMemorySlidingWindowRateLimiter
from app.backend.repositories.database import fetch_all
from app.core.config import Config
from app.rag_core.document_pipeline import DOCUMENT_EMBEDDING_DENSE_DIM


@dataclass(slots=True)
class BackendAppTestOverrides:
    """测试场景下可选的应用装配覆盖项。"""

    startup_behavior: StartupRuntimeBehavior = field(
        default_factory=StartupRuntimeBehavior
    )
    neo4j_client: Any = None
    mcp_server: Any = None
    a2a_service: Any = None
    a2a_graph_service: Any = None
    a2a_retrieval_service: Any = None
    session_memory_service: Any = None
    get_shared_vector_store: Callable[[], Any] | None = None
    get_shared_kg_milvus_store: Callable[[], Any] | None = None
    readiness_service: Any = None
    a2a_dispatch_service: A2ADispatchService | None = None
    a2a_event_stream_service: Any = None
    agent_run_service: AgentRunService | None = None
    session_turn_service: Any = None
    session_stream_service: Any = None
    sessions_history_service: Any = None
    admin_auth_service: AdminAuthService | None = None
    admin_runtime_service: Any = None
    storage_schema_service: Any = None
    admin_sessions_service: Any = None
    admin_observability_service: Any = None
    admin_documents_service: Any = None
    graph_admin_service: Any = None
    graph_public_service: Any = None


def _log_api_error(endpoint: str, stage: str, exc: Exception, **kwargs: Any) -> None:
    """记录 API 异常并输出结构化日志。"""
    logger = logging.getLogger(__name__)
    payload = {
        "endpoint": endpoint,
        "stage": stage,
        "error_type": type(exc).__name__,
        "error": str(exc),
    }
    payload.update(kwargs)
    logger.exception("[api_error] %s", json.dumps(payload, ensure_ascii=False))


async def _await_call(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """统一等待可等待对象。"""
    result = func(*args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


def create_app(*, test_overrides: BackendAppTestOverrides | None = None) -> FastAPI:
    """创建并返回 FastAPI 应用实例。"""
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    overrides = test_overrides or BackendAppTestOverrides()

    class _LazyNeo4jClient:
        @staticmethod
        def _resolve() -> Any:
            from app.knowledge_graph.neo4j_manager import neo4j_client

            return neo4j_client

        def __getattr__(self, name: str) -> Any:
            return getattr(self._resolve(), name)

    def _get_shared_vector_store_lazy() -> Any:
        if overrides.get_shared_vector_store is not None:
            return overrides.get_shared_vector_store()
        from app.rag_core.runtime_resources import get_shared_vector_store

        return get_shared_vector_store()

    def _get_shared_kg_milvus_store_lazy() -> Any:
        if overrides.get_shared_kg_milvus_store is not None:
            return overrides.get_shared_kg_milvus_store()
        from app.rag_core.runtime_resources import get_shared_kg_milvus_store

        return get_shared_kg_milvus_store()

    neo4j_client = overrides.neo4j_client or _LazyNeo4jClient()
    backend_config = build_backend_config()
    storage_config = Config()
    runtime_container = BackendRuntimeContainer(config=backend_config)
    runtime_container.mcp_server = overrides.mcp_server
    runtime_container.a2a_service = overrides.a2a_service
    runtime_container.a2a_graph_service = overrides.a2a_graph_service
    runtime_container.a2a_retrieval_service = overrides.a2a_retrieval_service
    runtime_container.session_memory_service = overrides.session_memory_service

    a2a_runtime_service = A2ARuntimeService(
        runtime_container=runtime_container,
        log_api_error=_log_api_error,
    )
    startup_runtime_service = StartupRuntimeService(
        runtime_container=runtime_container,
        a2a_runtime=a2a_runtime_service,
        log_api_error=_log_api_error,
        behavior=overrides.startup_behavior,
        get_shared_vector_store=_get_shared_vector_store_lazy,
        get_shared_kg_milvus_store=_get_shared_kg_milvus_store_lazy,
        neo4j_client=neo4j_client,
    )

    rate_limiter = InMemorySlidingWindowRateLimiter(
        max_keys=backend_config.rate_limit_max_keys
    )
    runtime_container.rate_limiter = rate_limiter
    rate_limit_window_seconds = backend_config.rate_limit_window_seconds
    heavy_endpoint_limits = backend_config.heavy_endpoint_limits

    readiness_service = overrides.readiness_service or ReadinessService(
        get_mcp=a2a_runtime_service.get_mcp,
        mysql_initialized_resolver=lambda: runtime_container.mysql_initialized,
        log_api_error=_log_api_error,
        await_call=_await_call,
    )

    a2a_dispatch_service = overrides.a2a_dispatch_service or A2ADispatchService(
        get_a2a=a2a_runtime_service.get_a2a,
        get_a2a_graph=a2a_runtime_service.get_a2a_graph,
        get_a2a_retrieval=a2a_runtime_service.get_a2a_retrieval,
        log_api_error=_log_api_error,
    )

    a2a_event_stream_service = (
        overrides.a2a_event_stream_service
        or A2AEventStreamService(
            get_a2a=a2a_runtime_service.get_a2a,
        )
    )

    agent_run_service = overrides.agent_run_service or AgentRunService(
        get_a2a=a2a_runtime_service.get_a2a,
        log_api_error=_log_api_error,
    )

    session_turn_service = overrides.session_turn_service or SessionTurnService(
        await_call=_await_call,
        get_session_memory_service=a2a_runtime_service.get_session_memory_service,
        log_api_error=_log_api_error,
    )

    session_stream_service = overrides.session_stream_service or SessionStreamService(
        await_call=_await_call,
        get_session_memory_service=a2a_runtime_service.get_session_memory_service,
        log_api_error=_log_api_error,
        event_stream_service=a2a_event_stream_service,
    )

    sessions_history_service = (
        overrides.sessions_history_service
        or SessionsHistoryService(
            await_call=_await_call,
            is_session_owner=is_session_owner,
            get_a2a=a2a_runtime_service.get_a2a,
            get_session_memory_service=a2a_runtime_service.get_session_memory_service,
            upload_dir=backend_config.upload_dir,
            log_api_error=_log_api_error,
        )
    )

    admin_auth_service = overrides.admin_auth_service or AdminAuthService()

    admin_runtime_service = overrides.admin_runtime_service or AdminRuntimeService(
        get_a2a=a2a_runtime_service.get_a2a,
        measure_event_loop_lag=a2a_runtime_service.measure_event_loop_lag,
        build_a2a_runtime_snapshot=a2a_runtime_service.build_a2a_runtime_snapshot,
    )

    def _create_milvus_diagnostic_client() -> Any:
        from pymilvus import MilvusClient  # type: ignore[import-untyped]

        return MilvusClient(
            uri=(
                f"http://{storage_config.MILVUS_HOST}:" f"{storage_config.MILVUS_PORT}"
            ),
            db_name=storage_config.MILVUS_DATABASE_NAME,
        )

    storage_schema_service = overrides.storage_schema_service or StorageSchemaService(
        fetch_mysql_migrations=fetch_all,
        neo4j_client=neo4j_client,
        milvus_client_factory=_create_milvus_diagnostic_client,
        document_collection_name=storage_config.MILVUS_COLLECTION_NAME,
        graph_collection_name=storage_config.KG_MILVUS_COLLECTION_NAME,
        document_dense_dim=DOCUMENT_EMBEDDING_DENSE_DIM,
    )

    admin_sessions_service = overrides.admin_sessions_service or AdminSessionsService(
        await_call=_await_call,
        get_session_memory_service=a2a_runtime_service.get_session_memory_service,
    )

    admin_observability_service = (
        overrides.admin_observability_service
        or AdminObservabilityService(log_api_error=_log_api_error)
    )

    admin_documents_service = (
        overrides.admin_documents_service
        or AdminDocumentsService(
            await_call=_await_call,
            get_shared_vector_store=_get_shared_vector_store_lazy,
            log_api_error=_log_api_error,
        )
    )

    graph_admin_service = overrides.graph_admin_service or GraphAdminService(
        await_call=_await_call,
        neo4j_client=neo4j_client,
        get_shared_kg_milvus_store=_get_shared_kg_milvus_store_lazy,
        validate_cypher_identifier=a2a_runtime_service.validate_cypher_identifier,
        sanitize_graph_properties=a2a_runtime_service.sanitize_graph_properties,
        log_api_error=_log_api_error,
    )

    graph_public_service = overrides.graph_public_service or GraphPublicService(
        await_call=_await_call,
        neo4j_client=neo4j_client,
        log_api_error=_log_api_error,
    )

    @asynccontextmanager
    async def app_lifespan(_: FastAPI):
        await startup_runtime_service.startup()
        try:
            yield
        finally:
            await startup_runtime_service.shutdown()

    app = FastAPI(lifespan=app_lifespan)
    app.state.runtime = runtime_container
    app.state.test_overrides = overrides

    @app.exception_handler(UnauthorizedError)
    async def _handle_unauthorized_error(
        _: Request, exc: UnauthorizedError
    ) -> JSONResponse:
        return unauthorized_response(str(exc) or "未登录")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=backend_config.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
    )

    app.middleware("http")(request_timing_middleware)

    upload_dir = backend_config.upload_dir
    upload_dir.mkdir(exist_ok=True)
    app.mount("/uploads", StaticFiles(directory=str(upload_dir)), name="uploads")

    app.include_router(
        create_system_mcp_router(
            await_call=_await_call,
            build_readiness_report=readiness_service.build_readiness_report,
            get_mcp=a2a_runtime_service.get_mcp,
            log_api_error=_log_api_error,
            get_trace_id=_resolve_trace_id_from_request,
            enforce_rate_limit=enforce_heavy_api_rate_limit,
            rate_limiter=rate_limiter,
            endpoint_limits=heavy_endpoint_limits,
            window_seconds=rate_limit_window_seconds,
        )
    )
    app.include_router(
        create_a2a_router(
            get_trace_id=_resolve_trace_id_from_request,
            get_a2a=a2a_runtime_service.get_a2a,
            dispatch_service=a2a_dispatch_service,
        )
    )
    app.include_router(create_graph_public_router(service=graph_public_service))
    app.include_router(
        create_query_chat_router(
            agent_run_service=agent_run_service,
            session_turn_service=session_turn_service,
            session_stream_service=session_stream_service,
            event_stream_service=a2a_event_stream_service,
            readiness_service=readiness_service,
            get_trace_id=_resolve_trace_id_from_request,
            enforce_rate_limit=enforce_heavy_api_rate_limit,
            rate_limiter=rate_limiter,
            endpoint_limits=heavy_endpoint_limits,
            window_seconds=rate_limit_window_seconds,
        )
    )
    app.include_router(create_sessions_router(service=sessions_history_service))
    app.include_router(create_admin_auth_router(service=admin_auth_service))
    app.include_router(create_admin_runtime_router(service=admin_runtime_service))
    app.include_router(create_admin_storage_router(service=storage_schema_service))
    app.include_router(create_admin_sessions_router(service=admin_sessions_service))
    app.include_router(
        create_admin_observability_router(service=admin_observability_service)
    )
    app.include_router(create_admin_documents_router(service=admin_documents_service))
    app.include_router(create_admin_graph_router(service=graph_admin_service))

    return app
