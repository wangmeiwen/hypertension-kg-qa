"""公开图谱查询服务。"""

from __future__ import annotations

from typing import Any, Awaitable, Callable


class GraphPublicService:
    """封装公开图谱查询接口。"""

    def __init__(
        self,
        await_call: Callable[..., Awaitable[Any]],
        neo4j_client: Any,
        log_api_error: Callable[..., None],
    ) -> None:
        self._await_call = await_call
        self._neo4j_client = neo4j_client
        self._log_api_error = log_api_error

    async def graph_visualization(self, keyword: str = "", limit: int = 200) -> dict[str, Any]:
        """查询图谱可视化节点与边数据。"""
        safe_limit = max(10, min(limit, 500))
        q = (keyword or "").strip()
        try:
            if q:
                rows = await self._await_call(
                    self._neo4j_client.run_cypher,
                    """
                    MATCH (a)-[r]->(b)
                    WHERE toString(a.name) CONTAINS $q OR toString(b.name) CONTAINS $q
                    RETURN id(a) AS source_id, id(b) AS target_id,
                           labels(a) AS source_labels, labels(b) AS target_labels,
                           a.name AS source_name, b.name AS target_name,
                           type(r) AS rel_type
                    LIMIT $limit
                    """,
                    {"q": q, "limit": safe_limit},
                )
            else:
                rows = await self._await_call(
                    self._neo4j_client.run_cypher,
                    """
                    MATCH (a)-[r]->(b)
                    RETURN id(a) AS source_id, id(b) AS target_id,
                           labels(a) AS source_labels, labels(b) AS target_labels,
                           a.name AS source_name, b.name AS target_name,
                           type(r) AS rel_type
                    LIMIT $limit
                    """,
                    {"limit": safe_limit},
                )
        except RuntimeError as exc:
            self._log_api_error("/graph/visualization", "neo4j_query", exc, keyword=q, limit=safe_limit)
            return {"ok": False, "error": f"graph_query_error:{str(exc)}", "nodes": [], "links": []}
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            self._log_api_error("/graph/visualization", "unknown", exc, keyword=q, limit=safe_limit)
            return {"ok": False, "error": f"graph_unknown_error:{str(exc)}", "nodes": [], "links": []}

        node_map: dict[str, dict[str, Any]] = {}
        links: list[dict[str, Any]] = []
        for row in rows:
            sid = str(row.get("source_id"))
            tid = str(row.get("target_id"))
            sname = row.get("source_name") or sid
            tname = row.get("target_name") or tid
            slabel = (row.get("source_labels") or ["Entity"])[0]
            tlabel = (row.get("target_labels") or ["Entity"])[0]
            if sid not in node_map:
                node_map[sid] = {"id": sid, "name": sname, "label": slabel}
            if tid not in node_map:
                node_map[tid] = {"id": tid, "name": tname, "label": tlabel}
            links.append(
                {
                    "source": sid,
                    "target": tid,
                    "label": row.get("rel_type") or "RELATED_TO",
                }
            )

        return {"ok": True, "nodes": list(node_map.values()), "links": links, "count": len(links)}

    async def get_entity_detail(self, entity_id: str) -> dict[str, Any]:
        """获取实体详细信息。"""
        try:
            entity_result = await self._await_call(
                self._neo4j_client.run_cypher,
                """
                MATCH (n)
                WHERE id(n) = $entity_id
                RETURN id(n) AS id, labels(n) AS labels, n.name AS name, properties(n) AS props
                """,
                {"entity_id": int(entity_id)},
            )

            if not entity_result:
                return {"ok": False, "error": "实体不存在"}

            entity = entity_result[0]
            props = entity.get("props") or {}
            node_properties = {k: v for k, v in props.items() if k != "name"}

            entity_data = {
                "id": str(entity["id"]),
                "labels": entity["labels"],
                "name": entity["name"],
                "properties": node_properties,
            }

            incoming = await self._await_call(
                self._neo4j_client.run_cypher,
                """
                MATCH (a)-[r]->(b)
                WHERE id(b) = $entity_id
                RETURN id(a) AS source_id, a.name AS source_name, labels(a) AS source_labels,
                       type(r) AS rel_type, properties(r) AS rel_props
                LIMIT 50
                """,
                {"entity_id": int(entity_id)},
            )

            outgoing = await self._await_call(
                self._neo4j_client.run_cypher,
                """
                MATCH (a)-[r]->(b)
                WHERE id(a) = $entity_id
                RETURN id(b) AS target_id, b.name AS target_name, labels(b) AS target_labels,
                       type(r) AS rel_type, properties(r) AS rel_props
                LIMIT 50
                """,
                {"entity_id": int(entity_id)},
            )

            relationships = {
                "incoming": [
                    {
                        "source_id": str(row["source_id"]),
                        "source_name": row["source_name"],
                        "source_labels": row["source_labels"],
                        "rel_type": row["rel_type"],
                        "rel_props": row.get("rel_props") or {},
                    }
                    for row in incoming
                ],
                "outgoing": [
                    {
                        "target_id": str(row["target_id"]),
                        "target_name": row["target_name"],
                        "target_labels": row["target_labels"],
                        "rel_type": row["rel_type"],
                        "rel_props": row.get("rel_props") or {},
                    }
                    for row in outgoing
                ],
            }

            return {
                "ok": True,
                "entity": entity_data,
                "relationships": relationships,
            }

        except ValueError as exc:
            self._log_api_error("/graph/entity", "id_parse", exc, entity_id=entity_id)
            return {"ok": False, "error": f"invalid_entity_id:{str(exc)}"}
        except RuntimeError as exc:
            self._log_api_error("/graph/entity", "neo4j_query", exc, entity_id=entity_id)
            return {"ok": False, "error": f"graph_query_error:{str(exc)}"}
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            self._log_api_error("/graph/entity", "unknown", exc, entity_id=entity_id)
            return {"ok": False, "error": f"graph_unknown_error:{str(exc)}"}
