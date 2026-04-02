"""管理员图谱服务。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from fastapi import UploadFile


class GraphAdminService:
    """封装管理员图谱管理与导入嵌入业务。"""

    def __init__(
        self,
        await_call: Callable[..., Awaitable[Any]],
        neo4j_client: Any,
        get_shared_kg_milvus_store: Callable[[], Any],
        validate_cypher_identifier: Callable[[Any], Optional[str]],
        sanitize_graph_properties: Callable[[Any], dict[str, Any]],
        log_api_error: Callable[..., None],
    ) -> None:
        self._await_call = await_call
        self._neo4j_client = neo4j_client
        self._get_shared_kg_milvus_store = get_shared_kg_milvus_store
        self._validate_cypher_identifier = validate_cypher_identifier
        self._sanitize_graph_properties = sanitize_graph_properties
        self._log_api_error = log_api_error

    async def get_graph_labels(self) -> dict[str, Any]:
        """获取所有节点标签。"""
        try:
            query = """
            MATCH (n)
            UNWIND labels(n) AS label
            RETURN DISTINCT label
            ORDER BY label
            """
            result = await self._await_call(self._neo4j_client.run_cypher, query)
            labels = [record["label"] for record in result]
            return {"ok": True, "data": labels}
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            self._log_api_error("/admin/graph/labels", "neo4j_query", exc)
            return {"ok": False, "error": str(exc)}

    async def get_graph_relationship_types(self) -> dict[str, Any]:
        """获取所有关系类型。"""
        try:
            query = """
            MATCH ()-[r]->()
            RETURN DISTINCT type(r) AS rel_type
            ORDER BY rel_type
            """
            result = await self._await_call(self._neo4j_client.run_cypher, query)
            rel_types = [record["rel_type"] for record in result]
            return {"ok": True, "data": rel_types}
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            self._log_api_error("/admin/graph/relationship-types", "neo4j_query", exc)
            return {"ok": False, "error": str(exc)}

    async def get_graph_nodes(self, label: str = "", keyword: str = "", limit: int = 100, offset: int = 0) -> dict[str, Any]:
        """获取节点列表。"""
        try:
            if label:
                if keyword:
                    query = f"""
                    MATCH (n:{label})
                    WHERE n.name CONTAINS $keyword OR n.title CONTAINS $keyword
                    RETURN id(n) AS id, labels(n) AS labels, properties(n) AS properties
                    ORDER BY n.name
                    SKIP $offset LIMIT $limit
                    """
                    count_query = f"""
                    MATCH (n:{label})
                    WHERE n.name CONTAINS $keyword OR n.title CONTAINS $keyword
                    RETURN count(n) AS total
                    """
                else:
                    query = f"""
                    MATCH (n:{label})
                    RETURN id(n) AS id, labels(n) AS labels, properties(n) AS properties
                    ORDER BY n.name
                    SKIP $offset LIMIT $limit
                    """
                    count_query = f"""
                    MATCH (n:{label})
                    RETURN count(n) AS total
                    """
            else:
                if keyword:
                    query = """
                    MATCH (n)
                    WHERE n.name CONTAINS $keyword OR n.title CONTAINS $keyword
                    RETURN id(n) AS id, labels(n) AS labels, properties(n) AS properties
                    ORDER BY n.name
                    SKIP $offset LIMIT $limit
                    """
                    count_query = """
                    MATCH (n)
                    WHERE n.name CONTAINS $keyword OR n.title CONTAINS $keyword
                    RETURN count(n) AS total
                    """
                else:
                    query = """
                    MATCH (n)
                    RETURN id(n) AS id, labels(n) AS labels, properties(n) AS properties
                    ORDER BY n.name
                    SKIP $offset LIMIT $limit
                    """
                    count_query = """
                    MATCH (n)
                    RETURN count(n) AS total
                    """

            params = {"keyword": keyword, "offset": offset, "limit": limit}
            result = await self._await_call(self._neo4j_client.run_cypher, query, params)
            count_result = await self._await_call(self._neo4j_client.run_cypher, count_query, {"keyword": keyword} if keyword else {})
            total = count_result[0]["total"] if count_result else 0
            return {"ok": True, "data": result, "total": total}
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            self._log_api_error("/admin/graph/nodes", "neo4j_query", exc, label=label, keyword=keyword)
            return {"ok": False, "error": str(exc)}

    async def get_graph_node(self, node_id: int) -> dict[str, Any]:
        """获取单个节点详情。"""
        try:
            node_query = """
            MATCH (n)
            WHERE id(n) = $node_id
            RETURN id(n) AS id, labels(n) AS labels, properties(n) AS properties
            """
            node_result = await self._await_call(self._neo4j_client.run_cypher, node_query, {"node_id": node_id})
            if not node_result:
                return {"ok": False, "error": "节点不存在"}

            incoming_query = """
            MATCH (n)<-[r]-(m)
            WHERE id(n) = $node_id
            RETURN id(r) AS rel_id, type(r) AS rel_type, properties(r) AS rel_props,
                   id(m) AS source_id, labels(m) AS source_labels, m.name AS source_name
            """
            incoming = await self._await_call(self._neo4j_client.run_cypher, incoming_query, {"node_id": node_id})

            outgoing_query = """
            MATCH (n)-[r]->(m)
            WHERE id(n) = $node_id
            RETURN id(r) AS rel_id, type(r) AS rel_type, properties(r) AS rel_props,
                   id(m) AS target_id, labels(m) AS target_labels, m.name AS target_name
            """
            outgoing = await self._await_call(self._neo4j_client.run_cypher, outgoing_query, {"node_id": node_id})

            return {
                "ok": True,
                "data": {
                    "node": node_result[0],
                    "relationships": {
                        "incoming": incoming,
                        "outgoing": outgoing,
                    },
                },
            }
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            self._log_api_error("/admin/graph/nodes/{node_id}", "neo4j_query", exc, node_id=node_id)
            return {"ok": False, "error": str(exc)}

    async def create_graph_node(self, data: dict[str, Any]) -> dict[str, Any]:
        """创建新节点。"""
        try:
            raw_label = str(data.get("label", "")).strip()
            label = self._validate_cypher_identifier(raw_label)
            properties = self._sanitize_graph_properties(data.get("properties", {}))

            if not raw_label:
                return {"ok": False, "error": "节点标签不能为空"}
            if not label:
                return {"ok": False, "error": "节点标签非法，仅允许字母数字下划线且不能以数字开头"}

            if properties:
                props_str = ", ".join([f"`{k}`: ${k}" for k in properties.keys()])
                query = f"CREATE (n:`{label}` {{{props_str}}}) RETURN id(n) AS id"
            else:
                query = f"CREATE (n:`{label}`) RETURN id(n) AS id"

            result = await self._await_call(self._neo4j_client.run_cypher, query, properties)
            if result:
                return {"ok": True, "data": {"id": result[0]["id"], "message": "节点创建成功"}}
            return {"ok": False, "error": "创建失败"}
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            self._log_api_error("/admin/graph/nodes", "create_node", exc)
            return {"ok": False, "error": str(exc)}

    async def update_graph_node(self, node_id: int, data: dict[str, Any]) -> dict[str, Any]:
        """更新节点。"""
        try:
            properties = self._sanitize_graph_properties(data.get("properties", {}))
            if not properties:
                return {"ok": False, "error": "属性不能为空"}

            set_clauses = [f"n.`{k}` = ${k}" for k in properties.keys()]
            set_str = ", ".join(set_clauses)
            query = f"""
            MATCH (n)
            WHERE id(n) = $node_id
            SET {set_str}
            RETURN id(n) AS id, properties(n) AS properties
            """
            params = {"node_id": node_id, **properties}
            result = await self._await_call(self._neo4j_client.run_cypher, query, params)
            if result:
                return {"ok": True, "data": {"id": result[0]["id"], "message": "节点更新成功"}}
            return {"ok": False, "error": "节点不存在或更新失败"}
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            self._log_api_error("/admin/graph/nodes/{node_id}", "update_node", exc, node_id=node_id)
            return {"ok": False, "error": str(exc)}

    async def delete_graph_node(self, node_id: int) -> dict[str, Any]:
        """删除节点及关系。"""
        try:
            query = """
            MATCH (n)
            WHERE id(n) = $node_id
            DETACH DELETE n
            RETURN count(n) AS deleted_count
            """
            result = await self._await_call(self._neo4j_client.run_cypher, query, {"node_id": node_id})
            if result and result[0]["deleted_count"] > 0:
                return {"ok": True, "message": "节点删除成功"}
            return {"ok": False, "error": "节点不存在"}
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            self._log_api_error("/admin/graph/nodes/{node_id}", "delete_node", exc, node_id=node_id)
            return {"ok": False, "error": str(exc)}

    async def create_graph_relationship(self, data: dict[str, Any]) -> dict[str, Any]:
        """创建关系。"""
        try:
            source_id = data.get("source_id")
            target_id = data.get("target_id")
            raw_rel_type = str(data.get("rel_type", "")).strip()
            rel_type = self._validate_cypher_identifier(raw_rel_type)
            properties = self._sanitize_graph_properties(data.get("properties", {}))

            if source_id is None or target_id is None or not raw_rel_type:
                return {"ok": False, "error": "源节点、目标节点和关系类型不能为空"}
            if not rel_type:
                return {"ok": False, "error": "关系类型非法，仅允许字母数字下划线且不能以数字开头"}

            try:
                source_id_int = int(source_id)
                target_id_int = int(target_id)
            except (TypeError, ValueError):
                return {"ok": False, "error": "源节点和目标节点ID必须是整数"}

            if properties:
                props_str = "{ " + ", ".join([f"`{k}`: ${k}" for k in properties.keys()]) + " }"
                query = f"""
                MATCH (source), (target)
                WHERE id(source) = $source_id AND id(target) = $target_id
                CREATE (source)-[r:`{rel_type}` {props_str}]->(target)
                RETURN id(r) AS rel_id
                """
                params = {"source_id": source_id_int, "target_id": target_id_int, **properties}
            else:
                query = f"""
                MATCH (source), (target)
                WHERE id(source) = $source_id AND id(target) = $target_id
                CREATE (source)-[r:`{rel_type}`]->(target)
                RETURN id(r) AS rel_id
                """
                params = {"source_id": source_id_int, "target_id": target_id_int}

            result = await self._await_call(self._neo4j_client.run_cypher, query, params)
            if result:
                return {"ok": True, "data": {"rel_id": result[0]["rel_id"], "message": "关系创建成功"}}
            return {"ok": False, "error": "创建失败，请检查节点是否存在"}
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            self._log_api_error("/admin/graph/relationships", "create_relationship", exc)
            return {"ok": False, "error": str(exc)}

    async def delete_graph_relationship(self, rel_id: int) -> dict[str, Any]:
        """删除关系。"""
        try:
            query = """
            MATCH ()-[r]->()
            WHERE id(r) = $rel_id
            DELETE r
            RETURN count(r) AS deleted_count
            """
            result = await self._await_call(self._neo4j_client.run_cypher, query, {"rel_id": rel_id})
            if result and result[0]["deleted_count"] > 0:
                return {"ok": True, "message": "关系删除成功"}
            return {"ok": False, "error": "关系不存在"}
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            self._log_api_error("/admin/graph/relationships/{rel_id}", "delete_relationship", exc, rel_id=rel_id)
            return {"ok": False, "error": str(exc)}

    async def execute_cypher(self, data: dict[str, Any]) -> dict[str, Any]:
        """执行只读 Cypher。"""
        try:
            query = str(data.get("query") or "").strip()
            if not query:
                return {"ok": False, "error": "查询语句不能为空"}

            forbidden_keywords = ["create", "merge", "delete", "remove", "set", "drop", "alter"]
            query_lower = query.lower()
            for keyword in forbidden_keywords:
                if keyword in query_lower:
                    return {"ok": False, "error": f"出于安全考虑，不允许执行包含 '{keyword}' 的操作"}

            result = await self._await_call(self._neo4j_client.run_cypher, query)
            return {"ok": True, "data": result}
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            self._log_api_error("/admin/graph/execute-cypher", "execute_cypher", exc)
            return {"ok": False, "error": str(exc)}

    async def extract_and_import_graph(self, file: UploadFile, max_chars: int = 12000) -> dict[str, Any]:
        """从上传文件抽取并导入图谱。"""
        _ = max_chars
        filename = file.filename or "uploaded_file"
        suffix = Path(filename).suffix.lower()
        supported_suffixes = {".txt", ".md", ".pdf", ".docx", ".doc", ".pptx", ".ppt", ".html", ".htm"}
        if suffix not in supported_suffixes:
            return {"ok": False, "error": f"不支持的文件格式: {suffix}，仅支持 {', '.join(sorted(supported_suffixes))}"}
        chunk_limit = 20000

        def split_text_by_sentence(text: str, limit: int = 20000) -> list[str]:
            chunks: list[str] = []
            remaining = (text or "").strip()
            sentence_ends = ["。", ".", "!", "！", "?", "？"]
            while remaining:
                if len(remaining) <= limit:
                    chunks.append(remaining)
                    break
                window = remaining[:limit]
                split_at = max(window.rfind(mark) for mark in sentence_ends)
                if split_at < 0:
                    split_at = limit
                else:
                    split_at += 1
                chunk = remaining[:split_at].strip()
                if not chunk:
                    chunk = remaining[:limit]
                    split_at = limit
                chunks.append(chunk)
                remaining = remaining[split_at:].strip()
            return chunks

        def extract_text_fallback(file_bytes: bytes, raw_filename: str, raw_suffix: str) -> str:
            _ = raw_filename
            if raw_suffix == ".pdf":
                import fitz

                doc = fitz.open(stream=file_bytes, filetype="pdf")
                try:
                    return "\n".join(page.get_text("text") for page in doc)
                finally:
                    doc.close()
            if raw_suffix == ".docx":
                from io import BytesIO
                from docx import Document

                doc = Document(BytesIO(file_bytes))
                return "\n\n".join(p.text.strip() for p in doc.paragraphs if p.text and p.text.strip())
            if raw_suffix in {".html", ".htm"}:
                from bs4 import BeautifulSoup

                html = file_bytes.decode("utf-8", errors="replace")
                return BeautifulSoup(html, "html.parser").get_text(separator="\n", strip=True)
            raise RuntimeError(f"当前环境无法使用 Docling，且该格式无内置回退解析器: {raw_suffix}")

        try:
            file_bytes = await file.read()
            if not file_bytes:
                return {"ok": False, "error": "上传文件为空"}

            parser_used = "plain_text"
            parse_warning = ""
            parsed = None

            if suffix in {".txt", ".md"}:
                text = file_bytes.decode("utf-8", errors="replace")
            else:
                try:
                    from app.document_parser.docling_parser import DoclingParser

                    parser = DoclingParser(enable_image_ocr=True)
                    parsed = await self._await_call(parser.parse_bytes, file_bytes, filename)
                    text = parsed.text
                    parser_used = "docling"
                except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as docling_error:
                    self._log_api_error("/admin/graph/extract-and-import", "docling_parse_fallback", docling_error, filename=filename)
                    text = await self._await_call(extract_text_fallback, file_bytes, filename, suffix)
                    parser_used = "fallback"
                    parse_warning = f"Docling 不可用，已自动回退解析: {docling_error}"

            if not text or not text.strip():
                return {"ok": False, "error": "文件解析后文本为空，无法抽取图谱"}

            from app.knowledge_graph.extract_graph_data_utils import extract_text_to_graph_queries

            chunks = split_text_by_sentence(text, chunk_limit)
            total_entities = 0
            total_relations = 0
            total_node_batches = 0
            total_relation_batches = 0

            for chunk in chunks:
                graph_data = await self._await_call(extract_text_to_graph_queries, chunk, len(chunk))
                node_queries = graph_data["node_queries"]
                relation_queries = graph_data["relation_queries"]
                if node_queries:
                    await self._await_call(self._neo4j_client.run_multiple_cypher, node_queries)
                if relation_queries:
                    await self._await_call(self._neo4j_client.run_multiple_cypher, relation_queries)

                stats = graph_data["stats"]
                total_entities += stats["entities"]
                total_relations += stats["relations"]
                total_node_batches += len(node_queries)
                total_relation_batches += len(relation_queries)

            response_data: dict[str, Any] = {
                "file_name": filename,
                "text_length": len(text),
                "parser_used": parser_used,
                "parse_warning": parse_warning,
                "chunk_limit": chunk_limit,
                "chunks": len(chunks),
                "entity_count": total_entities,
                "relation_count": total_relations,
                "node_batches": total_node_batches,
                "relation_batches": total_relation_batches,
                "message": f"抽取并导入完成，共分 {len(chunks)} 段，实体 {total_entities} 个，关系 {total_relations} 条",
            }
            if parsed and parser_used == "docling":
                response_data["parse_details"] = {
                    "format": parsed.format,
                    "pages": parsed.pages,
                    "tables_count": len(parsed.tables),
                    "images_count": len(parsed.images),
                    "metadata": parsed.metadata,
                    "text_preview": parsed.text[:2000] if parsed.text else "",
                }
            return {"ok": True, "data": response_data}
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            import traceback

            self._log_api_error("/admin/graph/extract-and-import", "extract_import", exc, filename=filename)
            return {"ok": False, "error": f"抽取写入失败: {str(exc)}\n{traceback.format_exc()}"}

    async def embed_graph_to_milvus(self, data: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """将知识图谱嵌入到 Milvus。"""
        try:
            labels = data.get("labels") if data else None
            batch_size = data.get("batch_size", 100) if data else 100
            kg_store = await self._await_call(self._get_shared_kg_milvus_store)
            stats = await self._await_call(kg_store.rebuild_from_neo4j, self._neo4j_client, labels, batch_size)
            return {
                "ok": True,
                "data": {
                    "message": f"知识图谱嵌入成功，共 {stats['total_documents']} 条记录",
                    "stats": stats,
                    "timestamp": stats.get("timestamp"),
                },
            }
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            import traceback

            self._log_api_error("/admin/graph/embed-to-milvus", "embed_graph", exc)
            return {"ok": False, "error": f"嵌入失败: {str(exc)}\n{traceback.format_exc()}"}

    async def get_graph_embed_status(self) -> dict[str, Any]:
        """获取知识图谱嵌入状态。"""
        try:
            kg_store = await self._await_call(self._get_shared_kg_milvus_store)
            stats = await self._await_call(kg_store.get_stats)
            return {"ok": True, "data": stats}
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            self._log_api_error("/admin/graph/embed-status", "embed_status", exc)
            return {"ok": False, "error": str(exc)}
