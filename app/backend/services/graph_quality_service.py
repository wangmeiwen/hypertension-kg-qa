"""管理员全图质量报告服务。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.knowledge_graph.graph_quality_snapshot import (
    build_graph_quality_snapshot,
)


class GraphQualityService:
    """读取受限 Neo4j 投影并计算全图质量指标。"""

    def __init__(
        self,
        *,
        await_call: Callable[..., Awaitable[Any]],
        neo4j_client: Any,
        log_api_error: Callable[..., None],
    ) -> None:
        self._await_call = await_call
        self._neo4j_client = neo4j_client
        self._log_api_error = log_api_error

    async def get_quality_report(
        self, *, max_nodes: int, max_relationships: int
    ) -> dict[str, Any]:
        """生成规模受限的全图质量报告，超限时拒绝返回部分指标。"""
        try:
            node_count_rows = await self._await_call(
                self._neo4j_client.run_cypher,
                "MATCH (n) RETURN count(n) AS total",
            )
            relationship_count_rows = await self._await_call(
                self._neo4j_client.run_cypher,
                "MATCH ()-[r]->() RETURN count(r) AS total",
            )
            if not node_count_rows or not relationship_count_rows:
                raise RuntimeError("Neo4j 未返回图谱规模统计")
            total_nodes = int(node_count_rows[0]["total"])
            total_relationships = int(relationship_count_rows[0]["total"])
            if total_nodes > max_nodes or total_relationships > max_relationships:
                return {
                    "ok": False,
                    "code": "graph_quality_snapshot_too_large",
                    "error": "图谱规模超过本次质量快照限制，未计算部分指标",
                    "data": {
                        "total_nodes": total_nodes,
                        "total_relationships": total_relationships,
                        "max_nodes": max_nodes,
                        "max_relationships": max_relationships,
                    },
                }

            nodes = await self._await_call(
                self._neo4j_client.run_cypher,
                """
                MATCH (n)
                RETURN id(n) AS id,
                       coalesce(n.entity_type, head(labels(n)), '') AS entity_type,
                       coalesce(n.canonical_name, n.name, '') AS canonical_name
                """,
            )
            relationships = await self._await_call(
                self._neo4j_client.run_cypher,
                """
                MATCH (source)-[r]->(target)
                RETURN id(source) AS source_node_id,
                       id(target) AS target_node_id,
                       r.source_id AS source_id,
                       r.source_chunk_index AS source_chunk_index,
                       r.confidence AS confidence,
                       r.quality_policy_version AS quality_policy_version
                """,
            )
            report = build_graph_quality_snapshot(
                list(nodes),
                list(relationships),
                captured_at=datetime.now(timezone.utc).isoformat(),
            )
            return {"ok": True, "data": report}
        except (
            RuntimeError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            OSError,
            ImportError,
        ) as exc:
            self._log_api_error(
                "/admin/graph/quality-report",
                "graph_quality_snapshot",
                exc,
            )
            return {"ok": False, "error": str(exc)}
