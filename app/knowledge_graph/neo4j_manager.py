"""
Neo4j 图数据库客户端模块。

本模块提供与 Neo4j 图数据库的交互能力，支持：
- Cypher 查询执行（单条/批量）
- 节点名称检索
- Cypher 语法验证

主要类：
- Neo4jClient: Neo4j 数据库客户端

"""

import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional

try:
    from neo4j import AsyncGraphDatabase, GraphDatabase
except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError):
    from neo4j import GraphDatabase  # type: ignore
    AsyncGraphDatabase = None  # type: ignore
from tqdm import tqdm

from app.common.perf_logger import log_duration
from app.common.storage_executors import run_in_neo4j_executor

logger = logging.getLogger(__name__)

current_file_path = Path(__file__).resolve()
project_root = current_file_path.parent.parent.parent
env_path = project_root / ".env"
if env_path.exists():
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=env_path, override=True)
    logger.info(f"已加载环境变量文件: {env_path}")
else:
    logger.warning(f"未找到环境变量文件: {env_path}")


class Neo4jClient:
    def __init__(self, uri: str, user: str, password: str):
        """
        初始化 Neo4j 连接。

        Args:
            uri: Neo4j 数据库 URI，如 "bolt://localhost:7687"
            user: 用户名
            password: 密码
        """
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        self._async_driver = None
        async_flag = str(os.getenv("ENABLE_NEO4J_ASYNC_DRIVER", "false")).strip().lower()
        self._async_enabled = async_flag in {"1", "true", "yes", "on"}
        if self._async_enabled and AsyncGraphDatabase is not None:
            try:
                self._async_driver = AsyncGraphDatabase.driver(uri, auth=(user, password))
                logger.info("Neo4j async driver 已启用")
            except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
                logger.warning("Neo4j async driver 初始化失败，回退同步模式: %s", exc)
                self._async_driver = None
        elif self._async_enabled and AsyncGraphDatabase is None:
            logger.warning("当前 neo4j 驱动不支持 AsyncGraphDatabase，已回退同步模式")

    def __del__(self):
        """
        关闭 Neo4j 连接。
        """
        if self.driver is not None:
            self.driver.close()

    def run_cypher(self, query: str, parameters: Optional[Dict] = None) -> List[Dict]:
        """
        执行单条 Cypher 查询语句并返回结果。

        Args:
            query: Cypher 查询语句
            parameters: 可选的参数字典

        Returns:
            查询结果列表，每一行是一个字典
        """
        with log_duration("neo4j.run_cypher"):
            with self.driver.session() as session:
                result = session.run(query, parameters or {})
                data = [record.data() for record in result]
                logger.info(f"[PERF] neo4j.run_cypher rows={len(data)}")
                return data

    async def run_cypher_async(self, query: str, parameters: Optional[Dict] = None) -> List[Dict]:
        """
        异步执行 Cypher 查询。

        优先使用官方 async driver；未启用时统一走 Neo4j 专用执行器。
        """
        if self._async_driver is not None:
            with log_duration("neo4j.run_cypher_async"):
                async with self._async_driver.session() as session:
                    result = await session.run(query, parameters or {})
                    rows: List[Dict] = []
                    async for record in result:
                        rows.append(record.data())
                    logger.info(f"[PERF] neo4j.run_cypher_async rows={len(rows)}")
                    return rows
        return await run_in_neo4j_executor(self.run_cypher, query, parameters)

    def run_multiple_cypher(self, queries_with_params: List[tuple]) -> None:
        """
        批量执行 Cypher 写入语句，使用事务并显示进度条。

        Args:
            queries_with_params: Cypher 语句和参数元组列表，
                格式如 [("CREATE (n:Test {name: $name})", {"name": "Alice"}), ...]
        """
        with log_duration("neo4j.run_multiple_cypher", queries=len(queries_with_params)):
            with self.driver.session() as session:
                def transaction_logic(tx):
                    for query, params in tqdm(queries_with_params, desc="执行 Cypher 语句"):
                        tx.run(query, params or {})

                session.execute_write(transaction_logic)

    def run_multiple_cypher_read(self, queries_with_params: List[tuple]) -> List[List[Dict]]:
        """
        批量执行 Cypher 查询语句并显示进度条。

        Args:
            queries_with_params: Cypher 语句和参数元组列表

        Returns:
            查询结果列表的列表
        """
        all_results = []

        def transaction_logic(tx, q, p):
            res = tx.run(q, p)
            return [record.data() for record in res]

        with log_duration("neo4j.run_multiple_cypher_read", queries=len(queries_with_params)):
            for query, params in tqdm(queries_with_params, desc="执行 Cypher 语句"):
                with self.driver.session() as session:
                    records = session.execute_read(transaction_logic, query, params or {})
                    all_results.append(records)

        return all_results

    def get_all_node_names(self, label: Optional[str] = None) -> List[str]:
        """
        获取指定标签的所有节点名称。

        Args:
            label: 节点标签，如 'Effect', 'Symptom', 'Disease'；为 None 时返回所有节点

        Returns:
            节点名称列表
        """
        if label is None:
            query = """
            MATCH (n)
            RETURN DISTINCT n.name AS name
            ORDER BY name
            """
        else:
            query = f"""
            MATCH (n:{label})
            RETURN DISTINCT n.name AS name
            ORDER BY name
            """
        with self.driver.session() as session:
            result = session.run(query)
            return [record["name"] for record in result if record["name"]]

    def validate_cypher(self, query: str) -> bool:
        """
        验证 Cypher 查询语句语法是否合法。

        Args:
            query: 待验证的 Cypher 语句

        Returns:
            True 表示语法合法，False 表示不合法
        """
        try:
            with self.driver.session() as session:
                # 使用 EXPLAIN 只做解析，不执行
                session.run(f"EXPLAIN {query}")
            return True
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as e:
            logger.error(f"Cypher 语法错误: {e}")
            return False


neo4j_client = Neo4jClient(
    os.getenv("NEO4J_URI"),
    os.getenv("NEO4J_USER"),
    os.getenv("NEO4J_PASSWORD"),
)




