"""公开图谱查询服务。"""

from __future__ import annotations

from typing import Any, Awaitable, Callable


class GraphPublicService:
    """封装公开图谱查询接口。"""

    _RELATIONSHIP_BASE_CAP = 10
    _RELATIONSHIP_FETCH_MULTIPLIER = 6
    _RELATIONSHIP_FETCH_MAX = 3000

    def __init__(
        self,
        await_call: Callable[..., Awaitable[Any]],
        neo4j_client: Any,
        log_api_error: Callable[..., None],
    ) -> None:
        """初始化实例并注入运行依赖。
        
        Args:
            await_call: 函数输入参数。
            neo4j_client: 函数输入参数。
            log_api_error: 函数输入参数。
        
        Returns:
            None: 无返回值。
        """
        self._await_call = await_call
        self._neo4j_client = neo4j_client
        self._log_api_error = log_api_error

    @staticmethod
    def _normalize_graph_row(row: dict[str, Any]) -> dict[str, Any]:
        """标准化图谱关系行，便于后续做均衡筛选。"""
        sid = str(row.get("source_id"))
        tid = str(row.get("target_id"))
        return {
            "source_id": sid,
            "target_id": tid,
            "source_name": row.get("source_name") or sid,
            "target_name": row.get("target_name") or tid,
            "source_labels": row.get("source_labels") or ["Entity"],
            "target_labels": row.get("target_labels") or ["Entity"],
            "source_props": row.get("source_props") or {},
            "target_props": row.get("target_props") or {},
            "rel_type": row.get("rel_type") or "RELATED_TO",
        }

    @classmethod
    def _select_balanced_relationship_rows(cls, rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
        """按每节点关系上限逐轮扩容，均衡不同节点的展示关系数。"""
        safe_limit = max(0, int(limit or 0))
        normalized_rows = [cls._normalize_graph_row(row) for row in rows if isinstance(row, dict)]
        if safe_limit <= 0:
            return []
        if len(normalized_rows) <= safe_limit:
            return normalized_rows

        per_node_total: dict[str, int] = {}
        for row in normalized_rows:
            source_id = str(row.get("source_id") or "")
            target_id = str(row.get("target_id") or "")
            per_node_total[source_id] = per_node_total.get(source_id, 0) + 1
            per_node_total[target_id] = per_node_total.get(target_id, 0) + 1

        selected: list[dict[str, Any]] = []
        selected_keys: set[tuple[str, str, str]] = set()
        node_counts: dict[str, int] = {}
        cap = int(cls._RELATIONSHIP_BASE_CAP)
        max_cap = max(cap, max(per_node_total.values(), default=cap))

        while len(selected) < safe_limit and cap <= max_cap:
            added_in_round = False
            for row in normalized_rows:
                if len(selected) >= safe_limit:
                    break
                source_id = str(row.get("source_id") or "")
                target_id = str(row.get("target_id") or "")
                rel_type = str(row.get("rel_type") or "RELATED_TO")
                row_key = (source_id, target_id, rel_type)
                if row_key in selected_keys:
                    continue
                if node_counts.get(source_id, 0) >= cap or node_counts.get(target_id, 0) >= cap:
                    continue
                selected.append(row)
                selected_keys.add(row_key)
                node_counts[source_id] = node_counts.get(source_id, 0) + 1
                node_counts[target_id] = node_counts.get(target_id, 0) + 1
                added_in_round = True
            if len(selected) >= safe_limit:
                break
            if not added_in_round and cap >= max_cap:
                break
            cap += 1

        if len(selected) < safe_limit:
            for row in normalized_rows:
                if len(selected) >= safe_limit:
                    break
                source_id = str(row.get("source_id") or "")
                target_id = str(row.get("target_id") or "")
                rel_type = str(row.get("rel_type") or "RELATED_TO")
                row_key = (source_id, target_id, rel_type)
                if row_key in selected_keys:
                    continue
                selected.append(row)
                selected_keys.add(row_key)

        return selected[:safe_limit]

    async def graph_visualization(self, keyword: str = "", limit: int = 200) -> dict[str, Any]:
        """查询图谱可视化节点与边数据。"""
        safe_limit = max(10, min(limit, 500))
        query_limit = min(
            max(safe_limit, safe_limit * self._RELATIONSHIP_FETCH_MULTIPLIER),
            self._RELATIONSHIP_FETCH_MAX,
        )
        q = (keyword or "").strip()
        try:
            node_rows: list[dict[str, Any]] = []
            if q:
                rows = await self._await_call(
                    self._neo4j_client.run_cypher,
                    """
                    MATCH (a)-[r]->(b)
                    WHERE toString(coalesce(a.name, "")) CONTAINS $q
                       OR toString(coalesce(b.name, "")) CONTAINS $q
                       OR toString(coalesce(a.test_run_id, "")) CONTAINS $q
                       OR toString(coalesce(b.test_run_id, "")) CONTAINS $q
                       OR toString(coalesce(r.test_run_id, "")) CONTAINS $q
                    RETURN id(a) AS source_id, id(b) AS target_id,
                           labels(a) AS source_labels, labels(b) AS target_labels,
                           a.name AS source_name, b.name AS target_name,
                           properties(a) AS source_props, properties(b) AS target_props,
                           type(r) AS rel_type
                     LIMIT $limit
                    """,
                    {"q": q, "limit": query_limit},
                )
                node_rows = await self._await_call(
                    self._neo4j_client.run_cypher,
                    """
                    MATCH (n)
                    WHERE toString(coalesce(n.name, "")) CONTAINS $q
                       OR toString(coalesce(n.test_run_id, "")) CONTAINS $q
                    RETURN id(n) AS id,
                           labels(n) AS labels,
                           n.name AS name,
                           properties(n) AS props
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
                           properties(a) AS source_props, properties(b) AS target_props,
                           type(r) AS rel_type
                     LIMIT $limit
                     """,
                    {"limit": query_limit},
                )
        except RuntimeError as exc:
            self._log_api_error("/graph/visualization", "neo4j_query", exc, keyword=q, limit=safe_limit)
            return {"ok": False, "error": f"graph_query_error:{str(exc)}", "nodes": [], "links": []}
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            self._log_api_error("/graph/visualization", "unknown", exc, keyword=q, limit=safe_limit)
            return {"ok": False, "error": f"graph_unknown_error:{str(exc)}", "nodes": [], "links": []}

        selected_rows = self._select_balanced_relationship_rows(rows if isinstance(rows, list) else [], safe_limit)
        node_map: dict[str, dict[str, Any]] = {}
        links: list[dict[str, Any]] = []
        for row in selected_rows:
            sid = str(row.get("source_id"))
            tid = str(row.get("target_id"))
            source_props = dict(row.get("source_props") or {})
            target_props = dict(row.get("target_props") or {})
            sname = row.get("source_name") or source_props.get("name") or sid
            tname = row.get("target_name") or target_props.get("name") or tid
            source_labels = row.get("source_labels") or ["Entity"]
            target_labels = row.get("target_labels") or ["Entity"]
            slabel = source_labels[0]
            tlabel = target_labels[0]
            if sid not in node_map:
                node_map[sid] = {
                    "id": sid,
                    "name": sname,
                    "label": slabel,
                    "labels": source_labels,
                    "properties": source_props,
                }
            if tid not in node_map:
                node_map[tid] = {
                    "id": tid,
                    "name": tname,
                    "label": tlabel,
                    "labels": target_labels,
                    "properties": target_props,
                }
            links.append(
                {
                    "source": sid,
                    "target": tid,
                    "label": row.get("rel_type") or "RELATED_TO",
                    "rel_type": row.get("rel_type") or "RELATED_TO",
                }
            )

        for row in node_rows if isinstance(node_rows, list) else []:
            node_id = str(row.get("id"))
            if node_id in node_map:
                continue
            labels = row.get("labels") or ["Entity"]
            props = dict(row.get("props") or {})
            node_map[node_id] = {
                "id": node_id,
                "name": row.get("name") or props.get("name") or node_id,
                "label": labels[0],
                "labels": labels,
                "properties": props,
            }

        return {"ok": True, "nodes": list(node_map.values()), "links": links, "count": len(links)}

    async def get_entity_detail(self, entity_id: str) -> dict[str, Any]:
        """获取实体详细信息。"""
        try:
            if not str(entity_id or "").strip().isdigit():
                raise ValueError(f"invalid literal for int() with base 10: {entity_id!r}")
            entity_result = await self._await_call(
                self._neo4j_client.run_cypher,
                """
                MATCH (n)
                WHERE toString(id(n)) = $entity_id
                RETURN id(n) AS id, labels(n) AS labels, n.name AS name, properties(n) AS props
                """,
                {"entity_id": str(entity_id)},
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
                WHERE toString(id(b)) = $entity_id
                RETURN id(a) AS source_id, a.name AS source_name, labels(a) AS source_labels,
                       type(r) AS rel_type, properties(r) AS rel_props
                LIMIT 50
                """,
                {"entity_id": str(entity_id)},
            )

            outgoing = await self._await_call(
                self._neo4j_client.run_cypher,
                """
                MATCH (a)-[r]->(b)
                WHERE toString(id(a)) = $entity_id
                RETURN id(b) AS target_id, b.name AS target_name, labels(b) AS target_labels,
                       type(r) AS rel_type, properties(r) AS rel_props
                LIMIT 50
                """,
                {"entity_id": str(entity_id)},
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
