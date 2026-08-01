"""图谱管理服务的 Neo4j CRUD 组件。"""

from __future__ import annotations

import re
from typing import Any, Awaitable, Callable, Optional

from fastapi import UploadFile

_WRITE_CYPHER_PATTERN = re.compile(
    r"\b(?:ALTER|CALL|CREATE|DELETE|DENY|DETACH|DROP|FOREACH|GRANT|LOAD\s+CSV|MERGE|REMOVE|RENAME|REVOKE|SET|START|STOP|TERMINATE|USE)\b",
    flags=re.IGNORECASE,
)


class GraphCrudMixin:
    """提供图谱查询、编辑和兼容入口，由 GraphAdminService 组合使用。"""

    _await_call: Callable[..., Awaitable[Any]]
    _neo4j_client: Any
    _get_shared_kg_milvus_store: Callable[[], Any]
    _validate_cypher_identifier: Callable[[Any], Optional[str]]
    _sanitize_graph_properties: Callable[[Any], dict[str, Any]]
    _log_api_error: Callable[..., None]
    submit_graph_extract_import_job: Callable[..., Awaitable[dict[str, Any]]]
    submit_graph_embed_milvus_job: Callable[..., Awaitable[dict[str, Any]]]

    async def get_graph_labels(self) -> dict[str, Any]:
        """获取 Neo4j 中所有节点标签。"""
        try:
            query = """
            MATCH (n)
            UNWIND labels(n) AS label
            RETURN DISTINCT label
            ORDER BY label
            """
            result = await self._await_call(self._neo4j_client.run_cypher, query)
            return {"ok": True, "data": [record["label"] for record in result]}
        except (
            RuntimeError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            OSError,
            ImportError,
        ) as exc:
            self._log_api_error("/admin/graph/labels", "neo4j_query", exc)
            return {"ok": False, "error": str(exc)}

    async def get_graph_relationship_types(self) -> dict[str, Any]:
        """获取 Neo4j 中所有关系类型。"""
        try:
            query = """
            MATCH ()-[r]->()
            RETURN DISTINCT type(r) AS rel_type
            ORDER BY rel_type
            """
            result = await self._await_call(self._neo4j_client.run_cypher, query)
            return {"ok": True, "data": [record["rel_type"] for record in result]}
        except (
            RuntimeError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            OSError,
            ImportError,
        ) as exc:
            self._log_api_error("/admin/graph/relationship-types", "neo4j_query", exc)
            return {"ok": False, "error": str(exc)}

    async def get_graph_nodes(
        self,
        label: str = "",
        keyword: str = "",
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """按标签、关键词和分页参数获取图谱节点列表。"""
        try:
            safe_label = self._validate_cypher_identifier(label) if label else ""
            if label and not safe_label:
                return {
                    "ok": False,
                    "error": "节点标签非法，仅允许字母数字下划线且不能以数字开头",
                }

            label_clause = f":`{safe_label}`" if safe_label else ""
            keyword_clause = (
                "WHERE n.name CONTAINS $keyword OR n.title CONTAINS $keyword"
                if keyword
                else ""
            )
            query = f"""
            MATCH (n{label_clause})
            {keyword_clause}
            RETURN id(n) AS id, labels(n) AS labels, properties(n) AS properties
            ORDER BY n.name
            SKIP $offset LIMIT $limit
            """
            count_query = f"""
            MATCH (n{label_clause})
            {keyword_clause}
            RETURN count(n) AS total
            """
            params = {"keyword": keyword, "offset": offset, "limit": limit}
            count_params = {"keyword": keyword} if keyword else {}
            result = await self._await_call(
                self._neo4j_client.run_cypher, query, params
            )
            count_result = await self._await_call(
                self._neo4j_client.run_cypher, count_query, count_params
            )
            total = count_result[0]["total"] if count_result else 0
            return {"ok": True, "data": result, "total": total}
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
                "/admin/graph/nodes", "neo4j_query", exc, label=label, keyword=keyword
            )
            return {"ok": False, "error": str(exc)}

    async def get_graph_node(self, node_id: int) -> dict[str, Any]:
        """获取单个节点详情及其入边、出边。"""
        try:
            node_query = """
            MATCH (n)
            WHERE id(n) = $node_id
            RETURN id(n) AS id, labels(n) AS labels, properties(n) AS properties
            """
            node_result = await self._await_call(
                self._neo4j_client.run_cypher, node_query, {"node_id": node_id}
            )
            if not node_result:
                return {"ok": False, "error": "节点不存在"}

            incoming_query = """
            MATCH (n)<-[r]-(m)
            WHERE id(n) = $node_id
            RETURN id(r) AS rel_id, type(r) AS rel_type, properties(r) AS rel_props,
                   id(m) AS source_id, labels(m) AS source_labels, m.name AS source_name
            """
            outgoing_query = """
            MATCH (n)-[r]->(m)
            WHERE id(n) = $node_id
            RETURN id(r) AS rel_id, type(r) AS rel_type, properties(r) AS rel_props,
                   id(m) AS target_id, labels(m) AS target_labels, m.name AS target_name
            """
            params = {"node_id": node_id}
            incoming = await self._await_call(
                self._neo4j_client.run_cypher, incoming_query, params
            )
            outgoing = await self._await_call(
                self._neo4j_client.run_cypher, outgoing_query, params
            )
            return {
                "ok": True,
                "data": {
                    "node": node_result[0],
                    "relationships": {"incoming": incoming, "outgoing": outgoing},
                },
            }
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
                "/admin/graph/nodes/{node_id}", "neo4j_query", exc, node_id=node_id
            )
            return {"ok": False, "error": str(exc)}

    async def create_graph_node(self, data: dict[str, Any]) -> dict[str, Any]:
        """创建新的图谱节点。"""
        try:
            raw_label = str(data.get("label", "")).strip()
            label = self._validate_cypher_identifier(raw_label)
            properties = self._sanitize_graph_properties(data.get("properties", {}))
            if not raw_label:
                return {"ok": False, "error": "节点标签不能为空"}
            if not label:
                return {
                    "ok": False,
                    "error": "节点标签非法，仅允许字母数字下划线且不能以数字开头",
                }
            properties["entity_type"] = label

            if properties:
                props_text = ", ".join(f"`{key}`: ${key}" for key in properties)
                query = (
                    f"CREATE (n:KnowledgeEntity:`{label}` {{{props_text}}}) "
                    "RETURN id(n) AS id"
                )
            else:
                query = f"CREATE (n:KnowledgeEntity:`{label}`) RETURN id(n) AS id"
            result = await self._await_call(
                self._neo4j_client.run_cypher, query, properties
            )
            if result:
                return {
                    "ok": True,
                    "data": {"id": result[0]["id"], "message": "节点创建成功"},
                }
            return {"ok": False, "error": "创建失败"}
        except (
            RuntimeError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            OSError,
            ImportError,
        ) as exc:
            self._log_api_error("/admin/graph/nodes", "create_node", exc)
            return {"ok": False, "error": str(exc)}

    async def update_graph_node(
        self, node_id: int, data: dict[str, Any]
    ) -> dict[str, Any]:
        """更新指定图谱节点属性。"""
        try:
            properties = self._sanitize_graph_properties(data.get("properties", {}))
            if not properties:
                return {"ok": False, "error": "属性不能为空"}
            set_text = ", ".join(f"n.`{key}` = ${key}" for key in properties)
            query = f"""
            MATCH (n)
            WHERE id(n) = $node_id
            SET {set_text}
            RETURN id(n) AS id, properties(n) AS properties
            """
            result = await self._await_call(
                self._neo4j_client.run_cypher,
                query,
                {"node_id": node_id, **properties},
            )
            if result:
                return {
                    "ok": True,
                    "data": {"id": result[0]["id"], "message": "节点更新成功"},
                }
            return {"ok": False, "error": "节点不存在或更新失败"}
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
                "/admin/graph/nodes/{node_id}", "update_node", exc, node_id=node_id
            )
            return {"ok": False, "error": str(exc)}

    async def delete_graph_node(self, node_id: int) -> dict[str, Any]:
        """删除指定图谱节点及其关联关系。"""
        try:
            query = """
            MATCH (n)
            WHERE id(n) = $node_id
            DETACH DELETE n
            RETURN count(n) AS deleted_count
            """
            result = await self._await_call(
                self._neo4j_client.run_cypher, query, {"node_id": node_id}
            )
            if result and result[0]["deleted_count"] > 0:
                return {"ok": True, "message": "节点删除成功"}
            return {"ok": False, "error": "节点不存在"}
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
                "/admin/graph/nodes/{node_id}", "delete_node", exc, node_id=node_id
            )
            return {"ok": False, "error": str(exc)}

    async def create_graph_relationship(self, data: dict[str, Any]) -> dict[str, Any]:
        """创建两个节点之间的图谱关系。"""
        try:
            source_id = data.get("source_id")
            target_id = data.get("target_id")
            raw_rel_type = str(data.get("rel_type", "")).strip()
            rel_type = self._validate_cypher_identifier(raw_rel_type)
            properties = self._sanitize_graph_properties(data.get("properties", {}))
            if source_id is None or target_id is None or not raw_rel_type:
                return {"ok": False, "error": "源节点、目标节点和关系类型不能为空"}
            if not rel_type:
                return {
                    "ok": False,
                    "error": "关系类型非法，仅允许字母数字下划线且不能以数字开头",
                }

            try:
                source_id_int = int(source_id)
                target_id_int = int(target_id)
            except (TypeError, ValueError) as exc:
                self._log_api_error(
                    "/admin/graph/relationships",
                    "create_relationship_id_parse",
                    exc,
                    source_id=source_id,
                    target_id=target_id,
                )
                return {"ok": False, "error": "源节点和目标节点ID必须是整数"}

            params = {
                "source_id": source_id_int,
                "target_id": target_id_int,
                **properties,
            }
            if properties:
                props_text = (
                    "{ " + ", ".join(f"`{key}`: ${key}" for key in properties) + " }"
                )
                query = f"""
                MATCH (source), (target)
                WHERE id(source) = $source_id AND id(target) = $target_id
                CREATE (source)-[r:`{rel_type}` {props_text}]->(target)
                RETURN id(r) AS rel_id
                """
            else:
                query = f"""
                MATCH (source), (target)
                WHERE id(source) = $source_id AND id(target) = $target_id
                CREATE (source)-[r:`{rel_type}`]->(target)
                RETURN id(r) AS rel_id
                """
            result = await self._await_call(
                self._neo4j_client.run_cypher, query, params
            )
            if result:
                return {
                    "ok": True,
                    "data": {"rel_id": result[0]["rel_id"], "message": "关系创建成功"},
                }
            return {"ok": False, "error": "创建失败，请检查节点是否存在"}
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
                "/admin/graph/relationships", "create_relationship", exc
            )
            return {"ok": False, "error": str(exc)}

    async def delete_graph_relationship(self, rel_id: int) -> dict[str, Any]:
        """删除指定图谱关系。"""
        try:
            query = """
            MATCH ()-[r]->()
            WHERE id(r) = $rel_id
            DELETE r
            RETURN count(r) AS deleted_count
            """
            result = await self._await_call(
                self._neo4j_client.run_cypher, query, {"rel_id": rel_id}
            )
            if result and result[0]["deleted_count"] > 0:
                return {"ok": True, "message": "关系删除成功"}
            return {"ok": False, "error": "关系不存在"}
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
                "/admin/graph/relationships/{rel_id}",
                "delete_relationship",
                exc,
                rel_id=rel_id,
            )
            return {"ok": False, "error": str(exc)}

    async def execute_cypher(self, data: dict[str, Any]) -> dict[str, Any]:
        """执行管理员输入的只读 Cypher 查询。"""
        try:
            query = str(data.get("query") or "").strip()
            if not query:
                return {"ok": False, "error": "查询语句不能为空"}
            if ";" in query.rstrip(";"):
                return {"ok": False, "error": "只允许执行单条只读查询"}
            forbidden_match = _WRITE_CYPHER_PATTERN.search(query)
            if forbidden_match:
                return {
                    "ok": False,
                    "error": f"出于安全考虑，不允许执行包含 '{forbidden_match.group(0)}' 的操作",
                }
            result = await self._await_call(self._neo4j_client.run_cypher, query)
            return {"ok": True, "data": result}
        except (
            RuntimeError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            OSError,
            ImportError,
        ) as exc:
            self._log_api_error("/admin/graph/execute-cypher", "execute_cypher", exc)
            return {"ok": False, "error": str(exc)}

    async def extract_and_import_graph(self, file: UploadFile) -> dict[str, Any]:
        """兼容旧接口，提交文件抽取导入后台任务。"""
        return await self.submit_graph_extract_import_job(file=file)

    async def embed_graph_to_milvus(
        self, data: Optional[dict[str, Any]] = None
    ) -> dict[str, Any]:
        """兼容旧接口，提交图谱嵌入 Milvus 后台任务。"""
        return await self.submit_graph_embed_milvus_job(data=data)

    async def get_graph_embed_status(self) -> dict[str, Any]:
        """获取知识图谱向量库嵌入状态。"""
        try:
            kg_store = await self._await_call(self._get_shared_kg_milvus_store)
            stats = await self._await_call(kg_store.get_stats)
            return {"ok": True, "data": stats}
        except (
            RuntimeError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            OSError,
            ImportError,
        ) as exc:
            self._log_api_error("/admin/graph/embed-status", "embed_status", exc)
            return {"ok": False, "error": str(exc)}
