"""后端启动与生命周期服务。"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, Callable

from app.backend.infrastructure.runtime_container import BackendRuntimeContainer
from app.backend.migrations.neo4j import apply_neo4j_migrations
from app.backend.repositories.database import close_database, initialize_database
from app.backend.infrastructure.database_health import check_databases
from app.core.feature_flags import get_runtime_flag_snapshot
from app.core.perf_logger import log_duration

from app.backend.services.a2a_runtime_service import A2ARuntimeService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StartupRuntimeBehavior:
    """控制 startup/shutdown 在测试场景下的执行策略。"""

    initialize_database: bool = True
    check_databases: bool = True
    migrate_neo4j_schema: bool = True
    prewarm_retrieval_resources: bool = True
    bootstrap_mcp: bool = True
    bootstrap_a2a: bool = True
    close_neo4j_on_shutdown: bool = True


class StartupRuntimeService:
    """封装应用 startup/shutdown 生命周期行为。"""

    def __init__(
        self,
        runtime_container: BackendRuntimeContainer,
        a2a_runtime: A2ARuntimeService,
        log_api_error: Callable[..., None],
        *,
        behavior: StartupRuntimeBehavior | None = None,
        database_initializer: Callable[..., Any] = initialize_database,
        database_closer: Callable[..., Any] = close_database,
        database_checker: Callable[..., Any] = check_databases,
        neo4j_schema_initializer: Callable[..., Any] = apply_neo4j_migrations,
        get_shared_vector_store: Callable[[], Any] | None = None,
        get_shared_kg_milvus_store: Callable[[], Any] | None = None,
        neo4j_client: Any = None,
    ) -> None:
        """初始化生命周期服务并注入可替换的启动依赖。

        Args:
            runtime_container: 后端运行时容器，用于保存初始化状态和共享资源。
            a2a_runtime: A2A 运行时服务。
            log_api_error: 统一记录生命周期错误的回调。
            behavior: 可选启动/关闭行为开关，主要用于测试隔离。
            database_initializer: MySQL 初始化函数。
            database_closer: MySQL 异步连接池释放函数。
            database_checker: 数据库健康检查函数。
            neo4j_schema_initializer: Neo4j schema 迁移函数。
            get_shared_vector_store: 可选文本向量库预热工厂。
            get_shared_kg_milvus_store: 可选图谱向量库预热工厂。
            neo4j_client: 可选 Neo4j 客户端，便于测试替换。

        Returns:
            None: 无返回值。
        """
        self._runtime = runtime_container
        self._a2a_runtime = a2a_runtime
        self._log_api_error = log_api_error
        self._behavior = behavior or StartupRuntimeBehavior()
        self._database_initializer = database_initializer
        self._database_closer = database_closer
        self._database_checker = database_checker
        self._neo4j_schema_initializer = neo4j_schema_initializer
        self._get_shared_vector_store = get_shared_vector_store
        self._get_shared_kg_milvus_store = get_shared_kg_milvus_store
        self._neo4j_client = neo4j_client

    async def startup(self) -> None:
        """执行应用启动流程，包含数据库、检索资源、MCP 和 A2A 初始化。"""
        logger.info("%s", "=" * 50)
        if self._behavior.initialize_database:
            logger.info("正在初始化 MySQL 数据库...")
            logger.info("%s", "=" * 50)
            self._runtime.mysql_initialized = bool(await self._database_initializer())
            if not self._runtime.mysql_initialized:
                logger.warning("MySQL 数据库初始化失败，部分功能可能不可用")
        else:
            logger.info("跳过 MySQL 初始化")
            if self._runtime.mysql_initialized is None:
                self._runtime.mysql_initialized = True

        database_status: dict[str, Any] = {}
        if self._behavior.check_databases:
            logger.info("%s", "=" * 50)
            logger.info("正在检查数据库连接...")
            logger.info("%s", "=" * 50)
            database_status = await self._database_checker()
            if bool(database_status.get("all_running")):
                logger.info("Neo4j 运行正常")
                logger.info("Milvus 运行正常")
            else:
                logger.warning("检测到部分数据库未启动，请手动启动后再使用相关功能")
            logger.info("%s", "=" * 50)
        else:
            logger.info("跳过数据库健康检查")

        neo4j_status = database_status.get("neo4j")
        neo4j_running = isinstance(neo4j_status, dict) and bool(
            neo4j_status.get("is_running")
        )
        if self._behavior.migrate_neo4j_schema and neo4j_running:
            try:
                neo4j = self._neo4j_client
                if neo4j is None:
                    from app.knowledge_graph.neo4j_manager import neo4j_client

                    neo4j = neo4j_client
                applied_versions = await self._neo4j_schema_initializer(neo4j)
                logger.info(
                    "Neo4j schema migration 完成: applied_versions=%s",
                    list(applied_versions),
                )
            except Exception as exc:
                self._log_api_error("startup", "neo4j_schema_migration", exc)
                logger.warning(
                    "Neo4j schema migration 失败: error_type=%s error=%s",
                    type(exc).__name__,
                    exc,
                )
        elif self._behavior.migrate_neo4j_schema:
            logger.info("Neo4j 不可用，跳过 schema migration")

        if self._behavior.prewarm_retrieval_resources:
            logger.info("正在预热共享检索资源...")
            try:
                vector_store_factory = self._get_shared_vector_store
                kg_store_factory = self._get_shared_kg_milvus_store
                if vector_store_factory is None or kg_store_factory is None:
                    from app.rag_core.runtime_resources import (
                        get_shared_kg_milvus_store,
                        get_shared_vector_store,
                    )

                    vector_store_factory = get_shared_vector_store
                    kg_store_factory = get_shared_kg_milvus_store

                with log_duration("startup.prewarm_vector_store"):
                    vector_store_factory()
                kg_store = kg_store_factory()
                if (
                    self._runtime.config.preload_kg_entity_relation_embedder
                    and hasattr(kg_store, "ensure_embedder_loaded")
                ):
                    with log_duration("startup.prewarm_kg_embedder"):
                        kg_store.ensure_embedder_loaded()
                logger.info("共享检索资源预热完成")
            except Exception as exc:  # pragma: no cover - depends on external runtimes
                self._log_api_error("startup", "retrieval_prewarm", exc)
                logger.warning(
                    "共享检索资源预热失败: error_type=%s error=%s",
                    type(exc).__name__,
                    exc,
                )
        else:
            logger.info("跳过共享检索资源预热")

        if self._behavior.bootstrap_mcp:
            logger.info("正在初始化 MCP 服务...")
            try:
                mcp = self._a2a_runtime.get_mcp()
                tools = mcp.list_tools()
                logger.info("MCP 服务初始化成功，已注册 %s 个工具", len(tools))
                for tool in tools:
                    logger.info("  - %s: %s", tool["name"], tool["description"])
            except Exception as exc:  # pragma: no cover - depends on external runtimes
                self._log_api_error("startup", "mcp_init", exc)
                logger.warning(
                    "MCP 服务初始化失败: error_type=%s error=%s",
                    type(exc).__name__,
                    exc,
                )
        else:
            logger.info("跳过 MCP 初始化")

        if self._behavior.bootstrap_a2a:
            logger.info("正在初始化 A2A 服务...")
            try:
                a2a_service = self._a2a_runtime.get_a2a()
                card = a2a_service.get_agent_card()
                logger.info(
                    "A2A 服务初始化成功: name=%s, protocol=%s",
                    card.get("name", ""),
                    card.get("protocolVersion", ""),
                )
                graph_card = self._a2a_runtime.get_a2a_graph().get_agent_card()
                retrieval_card = self._a2a_runtime.get_a2a_retrieval().get_agent_card()
                logger.info(
                    "A2A 子代理已初始化: %s, %s",
                    graph_card.get("name", ""),
                    retrieval_card.get("name", ""),
                )
            except Exception as exc:  # pragma: no cover - depends on external runtimes
                self._log_api_error("startup", "a2a_init", exc)
                logger.warning(
                    "A2A 服务初始化失败: error_type=%s error=%s",
                    type(exc).__name__,
                    exc,
                )
        else:
            logger.info("跳过 A2A 初始化")

        flags = get_runtime_flag_snapshot()
        logger.info("当前运行时开关:")
        for flag_name, flag_value in flags.items():
            logger.info("  - %s=%s", flag_name, flag_value)
        logger.info("%s", "=" * 50)

    async def shutdown(self) -> None:
        """执行应用关闭流程，释放 A2A、LLM、MCP、MySQL 和 Neo4j 资源。"""
        await self._a2a_runtime.shutdown_all()
        await self._close_llm_runtime_resources()
        try:
            await self._database_closer()
        except Exception as exc:  # pragma: no cover - defensive shutdown path
            self._log_api_error("shutdown", "mysql_close", exc)
        if not self._behavior.close_neo4j_on_shutdown:
            return
        try:
            if self._neo4j_client is not None:
                await self._neo4j_client.close()
                return
            from app.knowledge_graph.neo4j_manager import neo4j_client

            await neo4j_client.close()
        except (
            RuntimeError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            OSError,
            ImportError,
        ) as exc:
            self._log_api_error("shutdown", "neo4j_close", exc)

    async def _close_llm_runtime_resources(self) -> None:
        """关闭 Agent 与 MCP 持有的 LLM HTTP 连接池。"""
        try:
            from app.a2a.agent import close_iterative_agent

            await close_iterative_agent()
        except Exception as exc:  # pragma: no cover - defensive shutdown path
            self._log_api_error("shutdown", "a2a_llm_close", exc)

        try:
            server = self._runtime.mcp_server
            self._runtime.mcp_server = None
            if server is not None and hasattr(server, "aclose"):
                await server.aclose()

            from app.mcp.tools import close_mcp_server

            await close_mcp_server()
        except Exception as exc:  # pragma: no cover - defensive shutdown path
            self._log_api_error("shutdown", "mcp_close", exc)
