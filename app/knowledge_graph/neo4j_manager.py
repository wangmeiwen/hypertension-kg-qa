"""neo4j_manager 模块。

提供 neo4j_manager 相关能力与对外接口。
"""


from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Dict, List, Optional

from neo4j import AsyncGraphDatabase

from app.common.perf_logger import log_duration

logger = logging.getLogger(__name__)

current_file_path = Path(__file__).resolve()
project_root = current_file_path.parent.parent.parent
env_path = project_root / ".env"
if env_path.exists():
    from dotenv import load_dotenv

    load_dotenv(dotenv_path=env_path, override=True)


class Neo4jClient:
    """封装 Neo4j 异步连接与 Cypher 执行能力。"""

    def __init__(self, uri: str, user: str, password: str):
        """初始化 Neo4j 异步客户端。

        Args:
            uri: Neo4j 连接地址，如 bolt://127.0.0.1:7687。
            user: 数据库用户名。
            password: 数据库密码。

        Returns:
            None: 无返回值。
        """
        self._driver = AsyncGraphDatabase.driver(uri, auth=(user, password))

    async def close(self) -> None:
        """关闭底层 Neo4j 驱动连接池。

        Returns:
            None: 无返回值。
        """
        if self._driver is not None:
            await self._driver.close()

    async def run_cypher(self, query: str, parameters: Optional[Dict] = None) -> List[Dict]:
        """执行单条 Cypher 查询并返回结果列表。

        Args:
            query: Cypher 查询语句。
            parameters: 查询参数字典。

        Returns:
            List[Dict]: 每行记录对应的字典列表。
        """
        with log_duration("neo4j.run_cypher"):
            async with self._driver.session() as session:
                result = await session.run(query, parameters or {})
                rows: List[Dict] = []
                async for record in result:
                    rows.append(record.data())
                logger.info("[PERF] neo4j.run_cypher rows=%s", len(rows))
                return rows

    async def run_multiple_cypher(self, queries_with_params: List[tuple]) -> None:
        """在写事务中批量执行多条 Cypher 语句。

        Args:
            queries_with_params: (query, params) 元组列表。

        Returns:
            None: 无返回值。
        """
        with log_duration("neo4j.run_multiple_cypher", queries=len(queries_with_params)):
            async with self._driver.session() as session:
                async def _tx(tx):
                    """事务回调：顺序执行批量语句。

                    Args:
                        tx: Neo4j 异步事务对象。

                    Returns:
                        None: 无返回值。
                    """
                    for query, params in queries_with_params:
                        await tx.run(query, params or {})

                await session.execute_write(_tx)

    async def run_multiple_cypher_read(self, queries_with_params: List[tuple]) -> List[List[Dict]]:
        """顺序执行多条读查询并分别返回结果集。

        Args:
            queries_with_params: (query, params) 元组列表。

        Returns:
            List[List[Dict]]: 与输入查询顺序对应的结果集列表。
        """
        rows: List[List[Dict]] = []
        with log_duration("neo4j.run_multiple_cypher_read", queries=len(queries_with_params)):
            async with self._driver.session() as session:
                for query, params in queries_with_params:
                    result = await session.run(query, params or {})
                    current: List[Dict] = []
                    async for record in result:
                        current.append(record.data())
                    rows.append(current)
        return rows

    async def get_all_node_names(self, label: Optional[str] = None) -> List[str]:
        """获取图中节点名称列表。

        Args:
            label: 可选节点标签；为空时查询全图节点。

        Returns:
            List[str]: 去重后的节点名称列表。
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
        rows = await self.run_cypher(query)
        return [str(row.get("name")) for row in rows if row.get("name")]

    async def validate_cypher(self, query: str) -> bool:
        """校验 Cypher 语句语法是否合法。

        Args:
            query: 待校验的 Cypher 语句。

        Returns:
            bool: 语法合法返回 True，否则返回 False。
        """
        try:
            async with self._driver.session() as session:
                await session.run(f"EXPLAIN {query}")
            return True
        except Exception as exc:
            logger.error("Cypher 语法错误: %s", exc)
            return False


neo4j_client = Neo4jClient(
    os.getenv("NEO4J_URI"),
    os.getenv("NEO4J_USER"),
    os.getenv("NEO4J_PASSWORD"),
)
