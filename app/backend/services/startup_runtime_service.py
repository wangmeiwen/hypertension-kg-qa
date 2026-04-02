"""后端启动与生命周期服务。"""

from __future__ import annotations

from typing import Any, Callable

from app.backend.infrastructure.runtime_container import BackendRuntimeContainer
from app.backend.repositories.database import initialize_database
from app.common.db_manager import check_databases
from app.common.feature_flags import get_runtime_flag_snapshot
from app.common.perf_logger import log_duration

from app.backend.services.a2a_runtime_service import A2ARuntimeService


class StartupRuntimeService:
    """封装应用 startup/shutdown 生命周期行为。"""

    def __init__(
        self,
        runtime_container: BackendRuntimeContainer,
        a2a_runtime: A2ARuntimeService,
        log_api_error: Callable[..., None],
    ) -> None:
        self._runtime = runtime_container
        self._a2a_runtime = a2a_runtime
        self._log_api_error = log_api_error

    async def startup(self) -> None:
        """执行应用启动流程。"""
        print("\n" + "=" * 50)
        print("正在初始化 MySQL 数据库...")
        print("=" * 50)
        self._runtime.mysql_initialized = bool(await initialize_database())
        if not self._runtime.mysql_initialized:
            print("⚠ MySQL 数据库初始化失败，部分功能可能不可用")

        print("\n" + "=" * 50)
        print("正在检查数据库连接...")
        print("=" * 50)
        status = await check_databases()
        if bool(status.get("all_running")):
            print("✓ Neo4j 运行正常")
            print("✓ Milvus 运行正常")
        else:
            print("⚠ 检测到部分数据库未启动，请手动启动后再使用相关功能")
        print("=" * 50 + "\n")

        print("正在预热共享检索资源...")
        try:
            from app.rag_core.runtime_resources import get_shared_kg_milvus_store, get_shared_vector_store

            with log_duration("startup.prewarm_vector_store"):
                get_shared_vector_store()
            kg_store = get_shared_kg_milvus_store()
            if self._runtime.config.preload_kg_entity_relation_embedder:
                with log_duration("startup.prewarm_kg_embedder"):
                    kg_store.ensure_embedder_loaded()
            print("✓ 共享检索资源预热完成")
        except Exception as exc:
            self._log_api_error("startup", "retrieval_prewarm", exc)
            print(f"⚠ 共享检索资源预热失败: {exc}")

        print("正在初始化 MCP 服务...")
        try:
            mcp = self._a2a_runtime.get_mcp()
            tools = mcp.list_tools()
            print(f"✓ MCP 服务初始化成功，已注册 {len(tools)} 个工具:")
            for tool in tools:
                print(f"  - {tool['name']}: {tool['description']}")
        except Exception as exc:
            self._log_api_error("startup", "mcp_init", exc)
            print(f"⚠ MCP 服务初始化失败: {exc}")

        print("正在初始化 A2A 服务...")
        try:
            a2a_service = self._a2a_runtime.get_a2a()
            card = a2a_service.get_agent_card()
            print(
                "✓ A2A 服务初始化成功: "
                f"name={card.get('name', '')}, protocol={card.get('protocolVersion', '')}"
            )
            graph_card = self._a2a_runtime.get_a2a_graph().get_agent_card()
            retrieval_card = self._a2a_runtime.get_a2a_retrieval().get_agent_card()
            evaluator_card = self._a2a_runtime.get_a2a_evaluator().get_agent_card()
            print(
                "✓ A2A 子代理已初始化: "
                f"{graph_card.get('name', '')}, {retrieval_card.get('name', '')}, {evaluator_card.get('name', '')}"
            )
        except Exception as exc:
            self._log_api_error("startup", "a2a_init", exc)
            print(f"⚠ A2A 服务初始化失败: {exc}")

        flags = get_runtime_flag_snapshot()
        print("当前运行时开关:")
        for flag_name, flag_value in flags.items():
            print(f"  - {flag_name}={flag_value}")
        print("=" * 50 + "\n")

    async def shutdown(self) -> None:
        """执行应用关闭流程。"""
        await self._a2a_runtime.shutdown_all()
        try:
            from app.knowledge_graph.neo4j_manager import neo4j_client

            await neo4j_client.close()
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            self._log_api_error("shutdown", "neo4j_close", exc)
