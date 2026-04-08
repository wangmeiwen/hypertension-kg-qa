"""后端启动与生命周期服务。"""

from __future__ import annotations

import logging
from typing import Any, Callable

from app.backend.infrastructure.runtime_container import BackendRuntimeContainer
from app.backend.repositories.database import initialize_database
from app.common.db_manager import check_databases
from app.common.feature_flags import get_runtime_flag_snapshot
from app.common.perf_logger import log_duration

from app.backend.services.a2a_runtime_service import A2ARuntimeService

logger = logging.getLogger(__name__)


class StartupRuntimeService:
    """封装应用 startup/shutdown 生命周期行为。"""

    def __init__(
        self,
        runtime_container: BackendRuntimeContainer,
        a2a_runtime: A2ARuntimeService,
        log_api_error: Callable[..., None],
    ) -> None:
        """初始化实例并注入运行依赖。
        
        Args:
            runtime_container: 函数输入参数。
            a2a_runtime: 函数输入参数。
            log_api_error: 函数输入参数。
        
        Returns:
            None: 无返回值。
        """
        self._runtime = runtime_container
        self._a2a_runtime = a2a_runtime
        self._log_api_error = log_api_error

    async def startup(self) -> None:
        """执行应用启动流程。"""
        logger.info("%s", "=" * 50)
        logger.info("正在初始化 MySQL 数据库...")
        logger.info("%s", "=" * 50)
        self._runtime.mysql_initialized = bool(await initialize_database())
        if not self._runtime.mysql_initialized:
            logger.warning("MySQL 数据库初始化失败，部分功能可能不可用")

        logger.info("%s", "=" * 50)
        logger.info("正在检查数据库连接...")
        logger.info("%s", "=" * 50)
        status = await check_databases()
        if bool(status.get("all_running")):
            logger.info("Neo4j 运行正常")
            logger.info("Milvus 运行正常")
        else:
            logger.warning("检测到部分数据库未启动，请手动启动后再使用相关功能")
        logger.info("%s", "=" * 50)

        logger.info("正在预热共享检索资源...")
        try:
            from app.rag_core.runtime_resources import get_shared_kg_milvus_store, get_shared_vector_store

            with log_duration("startup.prewarm_vector_store"):
                get_shared_vector_store()
            kg_store = get_shared_kg_milvus_store()
            if self._runtime.config.preload_kg_entity_relation_embedder:
                with log_duration("startup.prewarm_kg_embedder"):
                    kg_store.ensure_embedder_loaded()
            logger.info("共享检索资源预热完成")
        except Exception as exc:
            self._log_api_error("startup", "retrieval_prewarm", exc)
            logger.warning(
                "共享检索资源预热失败: error_type=%s error=%s",
                type(exc).__name__,
                exc,
            )

        logger.info("正在初始化 MCP 服务...")
        try:
            mcp = self._a2a_runtime.get_mcp()
            tools = mcp.list_tools()
            logger.info("MCP 服务初始化成功，已注册 %s 个工具:", len(tools))
            for tool in tools:
                logger.info("  - %s: %s", tool["name"], tool["description"])
        except Exception as exc:
            self._log_api_error("startup", "mcp_init", exc)
            logger.warning(
                "MCP 服务初始化失败: error_type=%s error=%s",
                type(exc).__name__,
                exc,
            )

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
        except Exception as exc:
            self._log_api_error("startup", "a2a_init", exc)
            logger.warning(
                "A2A 服务初始化失败: error_type=%s error=%s",
                type(exc).__name__,
                exc,
            )

        flags = get_runtime_flag_snapshot()
        logger.info("当前运行时开关:")
        for flag_name, flag_value in flags.items():
            logger.info("  - %s=%s", flag_name, flag_value)
        logger.info("%s", "=" * 50)

    async def shutdown(self) -> None:
        """执行应用关闭流程。"""
        await self._a2a_runtime.shutdown_all()
        try:
            from app.knowledge_graph.neo4j_manager import neo4j_client

            await neo4j_client.close()
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            self._log_api_error("shutdown", "neo4j_close", exc)
