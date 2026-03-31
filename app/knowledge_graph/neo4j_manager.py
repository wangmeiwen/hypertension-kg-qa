"""Neo4j 异步客户端。"""

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
    def __init__(self, uri: str, user: str, password: str):
        self._driver = AsyncGraphDatabase.driver(uri, auth=(user, password))

    async def close(self) -> None:
        if self._driver is not None:
            await self._driver.close()

    async def run_cypher(self, query: str, parameters: Optional[Dict] = None) -> List[Dict]:
        with log_duration("neo4j.run_cypher"):
            async with self._driver.session() as session:
                result = await session.run(query, parameters or {})
                rows: List[Dict] = []
                async for record in result:
                    rows.append(record.data())
                logger.info("[PERF] neo4j.run_cypher rows=%s", len(rows))
                return rows

    async def run_multiple_cypher(self, queries_with_params: List[tuple]) -> None:
        with log_duration("neo4j.run_multiple_cypher", queries=len(queries_with_params)):
            async with self._driver.session() as session:
                async def _tx(tx):
                    for query, params in queries_with_params:
                        await tx.run(query, params or {})

                await session.execute_write(_tx)

    async def run_multiple_cypher_read(self, queries_with_params: List[tuple]) -> List[List[Dict]]:
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
