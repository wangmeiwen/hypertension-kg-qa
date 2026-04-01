"""
知识图谱向量存储模块。

本模块提供基于 Milvus 的知识图谱向量存储与检索能力，支持：
- 实体和关系的向量化存储与检索
- 混合检索（向量相似度 + 关键词匹配）
- 实体消歧（基于 Neo4j 别名映射 + LLM 回退）
- 社区发现（Leiden/Louvain 算法）
- 从 Neo4j 图谱重建向量库

主要功能：
1. rebuild_from_neo4j: 从 Neo4j 图谱重建 Milvus 向量库
2. search_entity_relations: 执行图谱混合检索、实体消歧与社区发现
3. _disambiguate_entities: 基于别名映射的实体消歧
4. _detect_communities_leiden: 对实体关系图进行社区发现

依赖：
- Milvus: 向量数据库
- Neo4j: 图谱数据源
- sentence-transformers: 文本嵌入
- networkx: 图算法
- igraph + leidenalg: Leiden 社区发现算法（可选）

"""

import hashlib
import os
import re
from collections import defaultdict
from datetime import datetime
from threading import Lock
from typing import Any, Callable, Dict, List, Optional

import networkx as nx
import numpy as np
from pymilvus import DataType, MilvusClient
from sentence_transformers import SentenceTransformer

from app.common.config import Config
from app.common.perf_logger import log_duration
from app.knowledge_graph.neo4j_manager import neo4j_client

try:
    import igraph as ig  # type: ignore
    import leidenalg  # type: ignore
    _LEIDEN_AVAILABLE = True
except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError):
    ig = None
    leidenalg = None
    _LEIDEN_AVAILABLE = False


class KgMilvusStore:
    _CLIENT_LOCK = Lock()
    _EMBEDDER_LOCK = Lock()
    _CLIENT_CACHE: Dict[str, MilvusClient] = {}
    _EMBEDDER_CACHE: Dict[str, SentenceTransformer] = {}

    @classmethod
    def _get_cached_client(cls, host: str, port: str, database: str) -> MilvusClient:
        """获取（或创建）共享 Milvus 客户端。

        Args:
            host: Milvus 主机地址。
            port: Milvus 端口。
            database: Milvus 数据库名。

        Returns:
            MilvusClient: 缓存复用的客户端实例。
        """
        key = f"http://{host}:{port}|{database}"
        with cls._CLIENT_LOCK:
            client = cls._CLIENT_CACHE.get(key)
            if client is None:
                client = MilvusClient(
                    uri=f"http://{host}:{port}",
                    db_name=database,
                )
                cls._CLIENT_CACHE[key] = client
        return client

    @classmethod
    def _get_cached_embedder(cls, model_path: str) -> SentenceTransformer:
        """获取（或加载）共享句向量模型。

        Args:
            model_path: 句向量模型本地路径。

        Returns:
            SentenceTransformer: 可复用的 embedding 模型实例。
        """
        with cls._EMBEDDER_LOCK:
            embedder = cls._EMBEDDER_CACHE.get(model_path)
            if embedder is None:
                with log_duration("kg_milvus.load_embedding_model", model_path=model_path):
                    embedder = SentenceTransformer(model_path)
                cls._EMBEDDER_CACHE[model_path] = embedder
        return embedder

    def __init__(self, conf: Optional[Config] = None, collection_name: Optional[str] = None):
        """
        初始化知识图谱 Milvus 存储。

        Args:
            conf: 配置对象，默认为 None（使用默认配置）
            collection_name: 集合名称，默认为 None（从环境变量读取或使用默认值）

        Returns:
            None
        """
        self.conf = conf or Config()
        self.collection_name = collection_name or os.getenv("KG_MILVUS_COLLECTION_NAME", "hypertension_kg_graph_collection")
        self.client = self._get_cached_client(
            str(self.conf.MILVUS_HOST),
            str(self.conf.MILVUS_PORT),
            str(self.conf.MILVUS_DATABASE_NAME),
        )

        self.model_path = self.conf.EMBEDDING_MODEL_PATH
        if not self.model_path or not os.path.exists(self.model_path):
            self.model_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "model", "bge-m3")
        self.embedder: Optional[SentenceTransformer] = None
        self.anns_field = "embedding"
        self.metric_type = self._normalize_metric_type(os.getenv("KG_MILVUS_METRIC_TYPE", "COSINE"))
        self.search_nprobe = int(self.conf.MILVUS_SEARCH_NPROBE)
        self.top_k_default = int(self.conf.KG_RETRIEVAL_TOP_K_DEFAULT)
        self.top_k_max = int(self.conf.KG_RETRIEVAL_TOP_K_MAX)
        self.lexical_weight_default = float(self.conf.KG_RETRIEVAL_LEXICAL_WEIGHT_DEFAULT)
        self.expansion_limit_default = int(self.conf.KG_RETRIEVAL_EXPANSION_LIMIT_DEFAULT)
        self.expansion_limit_max = int(self.conf.KG_RETRIEVAL_EXPANSION_LIMIT_MAX)
        self.primary_limit_min = int(self.conf.KG_RETRIEVAL_PRIMARY_LIMIT_MIN)
        self.primary_limit_multiplier = int(self.conf.KG_RETRIEVAL_PRIMARY_LIMIT_MULTIPLIER)
        self.keyword_limit_min = int(self.conf.KG_RETRIEVAL_KEYWORD_LIMIT_MIN)
        self.keyword_limit_multiplier = int(self.conf.KG_RETRIEVAL_KEYWORD_LIMIT_MULTIPLIER)
        self.vector_limit_max = int(self.conf.KG_RETRIEVAL_VECTOR_LIMIT_MAX)
        self.seed_entity_top_n = int(self.conf.KG_RETRIEVAL_SEED_ENTITY_TOP_N)
        self.neighbor_limit_min = int(self.conf.KG_RETRIEVAL_NEIGHBOR_LIMIT_MIN)
        self.neighbor_limit_max = int(self.conf.KG_RETRIEVAL_NEIGHBOR_LIMIT_MAX)
        self.community_top_k_default = int(self.conf.KG_COMMUNITY_TOP_K_DEFAULT)
        self.community_top_k_max = int(self.conf.KG_COMMUNITY_TOP_K_MAX)
        self.disambiguation_margin_default = float(self.conf.KG_DISAMBIGUATION_MARGIN_DEFAULT)
        self.disambiguation_llm_confidence_min = float(self.conf.KG_DISAMBIGUATION_LLM_CONFIDENCE_MIN)

        self._ensure_collection()
        self._refresh_vector_field()

    @staticmethod
    def _empty_token_usage() -> Dict[str, int]:
        """返回零值 token 统计结构。"""
        return {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }

    @staticmethod
    def _coerce_token_usage(metric_obj: Any) -> Dict[str, int]:
        """将任意 token 统计对象归一化为整型三元组。"""
        metric = metric_obj if isinstance(metric_obj, dict) else {}
        prompt_tokens = int(metric.get("prompt_tokens") or 0)
        completion_tokens = int(metric.get("completion_tokens") or 0)
        total_tokens = int(metric.get("total_tokens") or 0)
        if total_tokens <= 0:
            total_tokens = prompt_tokens + completion_tokens
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }

    @staticmethod
    def _merge_token_usage(target: Dict[str, int], metric_obj: Any) -> None:
        """把 token 统计累加到目标字典。"""
        metric = KgMilvusStore._coerce_token_usage(metric_obj)
        target["prompt_tokens"] += metric["prompt_tokens"]
        target["completion_tokens"] += metric["completion_tokens"]
        target["total_tokens"] += metric["total_tokens"]

    def _generate_community_report_with_llm(
        self,
        query: str,
        community: Dict[str, Any],
        relationships: List[Dict[str, Any]],
    ) -> tuple[str, Dict[str, int]]:
        """使用本地规则生成社区摘要，避免额外 LLM 开销。"""

        node_set = set(community.get("nodes") or [])
        related_rels = []
        for rel in relationships:
            src = str(rel.get("source_name") or "").strip()
            dst = str(rel.get("target_name") or "").strip()
            if src in node_set and dst in node_set:
                related_rels.append(
                    {
                        "source": src,
                        "relation": str(rel.get("relation_type") or "").strip(),
                        "target": dst,
                        "score": self._to_score(rel.get("score")),
                    }
                )
        related_rels = sorted(related_rels, key=lambda x: x["score"], reverse=True)[:8]

        relation_parts = [
            f"{item['source']} -[{item['relation']}]-> {item['target']}"
            for item in related_rels[:3]
            if str(item.get("source") or "").strip()
            and str(item.get("relation") or "").strip()
            and str(item.get("target") or "").strip()
        ]

        node_count = int(community.get("size") or len(community.get("nodes") or []))
        community_id = int(community.get("community_id") or 0)
        community_score = round(self._to_score(community.get("score")), 3)
        density = round(self._to_score(community.get("density")), 3)
        evidence_coverage = round(self._to_score(community.get("evidence_coverage")), 3)
        query_alignment = round(self._to_score(community.get("query_alignment")), 3)

        summary_lines = [
            f"社区{community_id}包含 {node_count} 个核心实体，综合相关度 {community_score}。",
            (
                f"结构密度 {density}、证据覆盖 {evidence_coverage}、"
                f"与查询“{str(query or '').strip()}”对齐度 {query_alignment}。"
            ),
        ]

        if relation_parts:
            summary_lines.append("主要关系：" + "；".join(relation_parts))

        return "\n".join(summary_lines), self._empty_token_usage()

    def _get_embedder(self) -> SentenceTransformer:
        """
        懒加载嵌入模型并复用单例实例。

        Args:
            None

        Returns:
            SentenceTransformer: 文本嵌入模型实例
        """
        if self.embedder is None:
            self.embedder = self._get_cached_embedder(self.model_path)
        return self.embedder

    def ensure_embedder_loaded(self) -> None:
        """
        显式预热嵌入模型，避免首轮检索触发冷启动超时。

        Returns:
            None
        """
        self._get_embedder()

    def _ensure_collection(self) -> None:
        """
        确保目标集合存在并已加载到内存。

        如果集合不存在，则创建新的集合并定义 schema 和索引。

        """
        with log_duration("kg_milvus.ensure_collection", collection=self.collection_name):
            if self.client.has_collection(self.collection_name):
                self.client.load_collection(self.collection_name)
                return

            schema = self.client.create_schema(auto_id=True, enable_dynamic_field=False)
            schema.add_field(field_name="pk_id", datatype=DataType.INT64, is_primary=True, auto_id=True)
            schema.add_field(field_name="doc_id", datatype=DataType.VARCHAR, max_length=64)
            schema.add_field(field_name="item_type", datatype=DataType.VARCHAR, max_length=32)
            schema.add_field(field_name="name", datatype=DataType.VARCHAR, max_length=512)
            schema.add_field(field_name="label", datatype=DataType.VARCHAR, max_length=128)
            schema.add_field(field_name="source_name", datatype=DataType.VARCHAR, max_length=512)
            schema.add_field(field_name="source_label", datatype=DataType.VARCHAR, max_length=128)
            schema.add_field(field_name="relation_type", datatype=DataType.VARCHAR, max_length=128)
            schema.add_field(field_name="target_name", datatype=DataType.VARCHAR, max_length=512)
            schema.add_field(field_name="target_label", datatype=DataType.VARCHAR, max_length=128)
            schema.add_field(field_name="text", datatype=DataType.VARCHAR, max_length=65535)
            schema.add_field(field_name="timestamp", datatype=DataType.VARCHAR, max_length=64)
            schema.add_field(field_name="embedding", datatype=DataType.FLOAT_VECTOR, dim=1024)

            index_params = self.client.prepare_index_params()
            index_params.add_index(
                field_name="embedding",
                index_type="IVF_FLAT",
                metric_type=self.metric_type,
                params={"nlist": 128},
            )

            self.client.create_collection(
                collection_name=self.collection_name,
                schema=schema,
                index_params=index_params,
            )
            self.client.load_collection(self.collection_name)

    def _refresh_vector_field(self) -> None:
        """
        刷新向量字段与度量类型。

        Args:
            None。

        Returns:
            None。

        """
        self.anns_field = "embedding"
        self.metric_type = self._infer_metric_type_from_index(default_metric=self.metric_type)

    @staticmethod
    def _normalize_metric_type(metric_type: Optional[str], default: str = "COSINE") -> str:
        """
        规范化 metric_type，未知值回退为默认值。

        Args:
            metric_type: 度量类型字符串
            default: 回退默认值

        Returns:
            str: 规范化后的度量类型
        """
        candidate = str(metric_type or "").strip().upper()
        if candidate in {"COSINE", "IP", "L2"}:
            return candidate
        return str(default or "COSINE").strip().upper() or "COSINE"

    def _infer_metric_type_from_index(self, default_metric: Optional[str] = None) -> str:
        """
        从集合索引元数据推断向量检索的 metric_type。

        Args:
            default_metric: 未能推断时使用的默认 metric

        Returns:
            str: 可用于检索的 metric_type
        """
        fallback_metric = self._normalize_metric_type(default_metric, default=self.metric_type)
        index_names: List[str] = []
        try:
            index_names = self.client.list_indexes(collection_name=self.collection_name, field_name=self.anns_field)
        except TypeError:
            # 兼容旧版 API（不支持 field_name 参数）
            index_names = self.client.list_indexes(collection_name=self.collection_name)
        except Exception:
            return fallback_metric

        if not isinstance(index_names, list):
            return fallback_metric

        for index_name in index_names:
            if not index_name:
                continue
            try:
                index_desc = self.client.describe_index(collection_name=self.collection_name, index_name=str(index_name))
            except Exception:
                continue
            if not isinstance(index_desc, dict):
                continue

            field_name = str(index_desc.get("field_name") or "").strip()
            if field_name and field_name != self.anns_field:
                continue

            metric_value = str(index_desc.get("metric_type") or "").strip()
            if metric_value:
                return self._normalize_metric_type(metric_value, default=fallback_metric)

        return fallback_metric

    @staticmethod
    def _extract_expected_metric_type(error_text: str) -> Optional[str]:
        """
        从 Milvus metric mismatch 报错中提取 expected metric。

        Args:
            error_text: 异常文本

        Returns:
            Optional[str]: 解析出的 expected metric，无法解析时返回 None
        """
        text = str(error_text or "")
        match = re.search(r"expected=([A-Za-z0-9_]+)\]\[actual=", text, flags=re.IGNORECASE)
        if not match:
            return None
        expected_metric = KgMilvusStore._normalize_metric_type(match.group(1), default="")
        return expected_metric or None

    def _search_with_params(self, search_kwargs: Dict[str, Any], search_params: Dict[str, Any]) -> List[List[dict]]:
        """单版本检索参数：仅使用 search_params。"""
        return self.client.search(**search_kwargs, search_params=search_params)

    def clear_collection(self) -> None:
        """
        清空并重建集合结构。

        删除现有集合并重新创建。

        Args:
            None

        Returns:
            None
        """
        with log_duration("kg_milvus.clear_collection", collection=self.collection_name):
            if self.client.has_collection(self.collection_name):
                self.client.drop_collection(self.collection_name)
            self._ensure_collection()

    async def _build_entity_rows(self, neo4j_client, labels: Optional[List[str]], timestamp: str) -> List[Dict[str, Any]]:
        """
        从 Neo4j 节点生成实体入库行数据。

        Args:
            neo4j_client: Neo4j 客户端实例
            labels: 实体标签列表，默认为 None（查询所有标签）
            timestamp: 时间戳字符串

        Returns:
            List[Dict[str, Any]]: 实体行数据列表
        """
        rows: List[Dict[str, Any]] = []
        target_labels = labels
        if not target_labels:
            result = await neo4j_client.run_cypher(
                """
                MATCH (n)
                UNWIND labels(n) AS label
                RETURN DISTINCT label
                """
            )
            target_labels = [record["label"] for record in result]

        for label in target_labels:
            nodes = await neo4j_client.run_cypher(
                f"""
                MATCH (n:{label})
                RETURN n, id(n) AS node_id
                """
            )
            for node_data in nodes:
                node = node_data["n"]
                node_id = node_data["node_id"]
                name = str(node.get("name") or f"{label}_{node_id}")
                text = f"实体 {name}，类型 {label}。属性：{str(dict(node))}"
                doc_id = hashlib.md5(f"entity_{label}_{node_id}_{name}".encode("utf-8")).hexdigest()
                rows.append(
                    {
                        "doc_id": doc_id,
                        "item_type": "entity",
                        "name": name,
                        "label": label,
                        "source_name": "",
                        "source_label": "",
                        "relation_type": "",
                        "target_name": "",
                        "target_label": "",
                        "text": text,
                        "timestamp": timestamp,
                    }
                )
        return rows

    async def _build_relationship_rows(self, neo4j_client, timestamp: str) -> List[Dict[str, Any]]:
        """
        从 Neo4j 边生成关系入库行数据。

        Args:
            neo4j_client: Neo4j 客户端实例
            timestamp: 时间戳字符串

        Returns:
            List[Dict[str, Any]]: 关系行数据列表
        """
        rows: List[Dict[str, Any]] = []
        rels = await neo4j_client.run_cypher(
            """
            MATCH (a)-[r]->(b)
            RETURN id(a) AS from_id, labels(a) AS from_labels, a.name AS from_name,
                   type(r) AS rel_type, properties(r) AS rel_props,
                   id(b) AS to_id, labels(b) AS to_labels, b.name AS to_name
            """
        )

        for rel in rels:
            from_name = str(rel.get("from_name") or f"node_{rel.get('from_id')}")
            to_name = str(rel.get("to_name") or f"node_{rel.get('to_id')}")
            from_label = (rel.get("from_labels") or ["Unknown"])[0]
            to_label = (rel.get("to_labels") or ["Unknown"])[0]
            rel_type = str(rel.get("rel_type") or "RELATED_TO")
            rel_props = rel.get("rel_props") or {}

            text = f"关系 {from_name}({from_label}) -[{rel_type}]-> {to_name}({to_label})。属性：{str(rel_props)}"
            doc_id = hashlib.md5(
                f"rel_{rel.get('from_id')}_{rel_type}_{rel.get('to_id')}".encode("utf-8")
            ).hexdigest()
            rows.append(
                {
                    "doc_id": doc_id,
                    "item_type": "relationship",
                    "name": rel_type,
                    "label": "",
                    "source_name": from_name,
                    "source_label": from_label,
                    "relation_type": rel_type,
                    "target_name": to_name,
                    "target_label": to_label,
                    "text": text,
                    "timestamp": timestamp,
                }
            )
        return rows

    def _upsert_rows(self, rows: List[Dict[str, Any]], batch_size: int) -> int:
        """
        分批生成向量并写入 Milvus。

        Args:
            rows: 待插入的行数据列表
            batch_size: 每批处理的数据量

        Returns:
            int: 成功插入的记录数
        """
        if not rows:
            return 0

        inserted = 0
        for start in range(0, len(rows), batch_size):
            batch = rows[start : start + batch_size]
            texts = [item["text"] for item in batch]
            embeddings = self._get_embedder().encode(texts, normalize_embeddings=True)

            data = []
            for item, vec in zip(batch, embeddings):
                data.append(
                    {
                        "doc_id": item["doc_id"],
                        "item_type": item["item_type"],
                        "name": item["name"],
                        "label": item["label"],
                        "source_name": item["source_name"],
                        "source_label": item["source_label"],
                        "relation_type": item["relation_type"],
                        "target_name": item["target_name"],
                        "target_label": item["target_label"],
                        "text": item["text"],
                        "timestamp": item["timestamp"],
                        "embedding": vec.tolist() if hasattr(vec, "tolist") else list(vec),
                    }
                )

            self.client.insert(collection_name=self.collection_name, data=data)
            inserted += len(data)

        return inserted

    async def rebuild_from_neo4j(self, neo4j_client, labels: Optional[List[str]] = None, batch_size: int = 100) -> Dict[str, Any]:
        """
        从 Neo4j 重建图谱向量库。

        清空现有集合，从 Neo4j 读取实体和关系数据，生成向量并写入 Milvus。

        Args:
            neo4j_client: Neo4j 客户端实例
            labels: 实体标签列表，默认为 None（所有标签）
            batch_size: 批量插入大小，默认为 100

        Returns:
            Dict[str, Any]: 重建统计信息，包含实体数、关系数、总文档数和时间戳
        """
        with log_duration("kg_milvus.rebuild_from_neo4j", collection=self.collection_name, batch_size=batch_size):
            self.clear_collection()
            timestamp = datetime.now().isoformat()

            with log_duration("kg_milvus.build_entities"):
                entity_rows = await self._build_entity_rows(neo4j_client, labels, timestamp)
            with log_duration("kg_milvus.build_relationships"):
                rel_rows = await self._build_relationship_rows(neo4j_client, timestamp)

            with log_duration("kg_milvus.insert_entities", count=len(entity_rows)):
                entity_inserted = self._upsert_rows(entity_rows, batch_size=batch_size)
            with log_duration("kg_milvus.insert_relationships", count=len(rel_rows)):
                rel_inserted = self._upsert_rows(rel_rows, batch_size=batch_size)

            return {
                "collection_name": self.collection_name,
                "entities_count": entity_inserted,
                "relationships_count": rel_inserted,
                "total_documents": entity_inserted + rel_inserted,
                "timestamp": timestamp,
            }

    def _split_query_terms(self, query: str) -> List[str]:
        """
        将查询按标点与空白切词并去重。

        Args:
            query: 查询字符串

        Returns:
            List[str]: 切分后的关键词列表
        """
        text = re.sub(r"[，。！？；：、,!?;:/\\|()（）\[\]{}]+", " ", str(query or ""))
        terms = [item.strip() for item in text.split(" ") if item.strip()]
        seen: set[str] = set()
        deduped: List[str] = []
        for item in terms:
            lowered = item.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            deduped.append(item)
        return deduped

    def _to_score(self, raw: Any) -> float:
        """
        将原始分值安全归一为非负浮点数。

        Args:
            raw: 原始分值（任意类型）

        Returns:
            float: 归一化后的非负浮点数
        """
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return 0.0
        if value < 0:
            return 0.0
        return value

    def _lexical_overlap_score(self, text: str, terms: List[str]) -> float:
        """
        计算关键词在文本中的覆盖比例。

        Args:
            text: 待匹配的文本
            terms: 关键词列表

        Returns:
            float: 关键词覆盖比例（0.0 ~ 1.0）
        """
        if not terms:
            return 0.0
        haystack = str(text or "").lower()
        if not haystack:
            return 0.0
        matched = sum(1 for term in terms if str(term).lower() in haystack)
        return matched / max(1, len(terms))

    async def _search_vector_rows(self, query_text: str, limit: int) -> List[Dict[str, Any]]:
        """
        执行向量检索并转为统一行结构。

        Args:
            query_text: 查询文本
            limit: 返回结果数量限制

        Returns:
            List[Dict[str, Any]]: 检索结果列表，包含向量相似度分数
        """
        query_value = str(query_text or "").strip()
        if not query_value:
            return []

        vec_np = np.asarray(self._get_embedder().encode([query_value], normalize_embeddings=True)[0], dtype=np.float32)
        vec = vec_np.reshape(-1).tolist()
        search_kwargs = {
            "collection_name": self.collection_name,
            "data": [vec],
            "anns_field": self.anns_field,
            "limit": max(1, min(limit, int(self.vector_limit_max))),
            "output_fields": [
                "doc_id",
                "item_type",
                "name",
                "label",
                "source_name",
                "source_label",
                "relation_type",
                "target_name",
                "target_label",
                "text",
                "timestamp",
            ],
        }
        search_params = {
            "metric_type": self.metric_type,
            "params": {"nprobe": max(1, int(self.search_nprobe))},
        }

        retry_count = 0
        while True:
            try:
                results = self._search_with_params(search_kwargs=search_kwargs, search_params=search_params)
                break
            except Exception as exc:
                error_text = str(exc)
                if retry_count >= 2:
                    raise

                if "fieldName(" in error_text and "not found" in error_text:
                    self._refresh_vector_field()
                    search_kwargs["anns_field"] = self.anns_field
                    search_params["metric_type"] = self.metric_type
                    retry_count += 1
                    continue

                if "metric type not match" in error_text:
                    expected_metric = self._extract_expected_metric_type(error_text)
                    if not expected_metric:
                        expected_metric = self._infer_metric_type_from_index(default_metric=self.metric_type)
                    if expected_metric and expected_metric != self.metric_type:
                        self.metric_type = expected_metric
                        search_params["metric_type"] = self.metric_type
                        retry_count += 1
                        continue
                raise

        rows: List[Dict[str, Any]] = []
        for hit in results[0]:
            row = hit.get("entity", {}) if isinstance(hit, dict) else {}
            if not isinstance(row, dict):
                continue
            rows.append(
                {
                    "doc_id": row.get("doc_id"),
                    "item_type": row.get("item_type"),
                    "name": row.get("name"),
                    "label": row.get("label"),
                    "source_name": row.get("source_name"),
                    "source_label": row.get("source_label"),
                    "relation_type": row.get("relation_type"),
                    "target_name": row.get("target_name"),
                    "target_label": row.get("target_label"),
                    "text": row.get("text"),
                    "timestamp": row.get("timestamp"),
                    "vector_score": self._to_score(hit.get("distance") if isinstance(hit, dict) else 0.0),
                }
            )
        return rows

    def _escape_filter_value(self, value: str) -> str:
        """
        转义 Milvus 过滤表达式中的特殊字符。

        Args:
            value: 待转义的字符串值

        Returns:
            str: 转义后的字符串
        """
        return str(value or "").replace("\\", "\\\\").replace("\"", "\\\"")

    async def _query_relation_neighbors(self, entity_names: List[str], limit: int) -> List[Dict[str, Any]]:
        """
        按实体名扩展查询其相邻关系行。

        Args:
            entity_names: 实体名称列表
            limit: 返回结果数量限制

        Returns:
            List[Dict[str, Any]]: 关系行数据列表
        """
        seeds = [str(item).strip() for item in entity_names if str(item).strip()]
        if not seeds:
            return []

        terms = []
        for name in seeds[:8]:
            escaped = self._escape_filter_value(name)
            terms.append(f'source_name == "{escaped}"')
            terms.append(f'target_name == "{escaped}"')
        expr = "item_type == 'relationship' and (" + " or ".join(terms) + ")"

        rows = self.client.query(
            collection_name=self.collection_name,
            filter=expr,
            output_fields=[
                "doc_id",
                "item_type",
                "name",
                "label",
                "source_name",
                "source_label",
                "relation_type",
                "target_name",
                "target_label",
                "text",
                "timestamp",
            ],
            limit=max(1, min(limit, 200)),
        )
        return rows if isinstance(rows, list) else []

    def _normalize_entity_name(self, name: str) -> str:
        """
        规范化实体名用于别名匹配。

        去除空格、标点符号，转换为小写。

        Args:
            name: 实体名称

        Returns:
            str: 规范化后的实体名
        """
        text = str(name or "").strip().lower()
        text = re.sub(r"[\s\-_/()（）\[\]{}，,。！？!?；;：:]", "", text)
        return text

    def _parse_aliases(self, raw_alias: Any) -> List[str]:
        """
        解析别名字段为标准字符串列表。

        支持列表和字符串格式，字符串可按逗号、分号、竖线分隔。

        Args:
            raw_alias: 原始别名数据（列表或字符串）

        Returns:
            List[str]: 解析后的别名列表
        """
        if isinstance(raw_alias, list):
            return [str(item).strip() for item in raw_alias if str(item).strip()]
        if isinstance(raw_alias, str):
            text = raw_alias.strip()
            if not text:
                return []
            if text.startswith("[") and text.endswith("]"):
                text = text[1:-1]
            parts = [item.strip().strip("'\"") for item in re.split(r"[,，;；|]", text) if item.strip()]
            return [item for item in parts if item]
        return []

    async def _fetch_alias_map_from_neo4j(self, names: List[str]) -> Dict[str, str]:
        """
        从 Neo4j 读取 name/canonical_name/alias 映射关系。

        Args:
            names: 实体名称列表

        Returns:
            Dict[str, str]: 别名到规范名称的映射字典
        """
        seeds = [str(item).strip() for item in names if str(item).strip()]
        if not seeds:
            return {}

        # 仅依赖 Neo4j 属性(name/canonical_name/alias) 做实体消歧。
        rows = await neo4j_client.run_cypher(
            """
            MATCH (n)
            WHERE coalesce(n.name, '') IN $names OR coalesce(n.canonical_name, '') IN $names
            RETURN n.name AS name, n.canonical_name AS canonical_name, n.alias AS alias
            """,
            {"names": seeds},
        )

        alias_map: Dict[str, str] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "").strip()
            canonical_name = str(row.get("canonical_name") or "").strip() or name
            if not canonical_name:
                continue

            base_keys = [name, canonical_name]
            for key in base_keys:
                if not key:
                    continue
                normalized_key = self._normalize_entity_name(key)
                if normalized_key:
                    alias_map[normalized_key] = canonical_name

            aliases = self._parse_aliases(row.get("alias"))
            for alias in aliases:
                normalized_alias = self._normalize_entity_name(alias)
                if normalized_alias:
                    alias_map[normalized_alias] = canonical_name

        return alias_map

    async def _disambiguate_entities(
        self,
        query: str,
        entities: List[Dict[str, Any]],
        relationships: List[Dict[str, Any]],
        margin: float,
        resolver: Optional[Callable[[str, List[Dict[str, Any]], float], Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        基于别名映射与可选 LLM 回退执行实体消歧。

        通过 Neo4j 别名映射识别同一实体的不同表述，对歧义实体使用 LLM 进行消歧。

        Args:
            query: 原始查询字符串
            entities: 实体列表
            relationships: 关系列表
            margin: 消歧阈值，分数差小于此值视为歧义
            resolver: 可选的 LLM 消歧函数

        Returns:
            Dict[str, Any]: 消歧结果，包含消歧后的实体、关系、歧义实体列表等
        """
        all_names: List[str] = []
        for item in entities:
            all_names.append(str(item.get("name") or "").strip())
        for rel in relationships:
            all_names.append(str(rel.get("source_name") or "").strip())
            all_names.append(str(rel.get("target_name") or "").strip())

        alias_map = await self._fetch_alias_map_from_neo4j(all_names)
        grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        canonical_mapping: Dict[str, str] = {}
        alias_groups: Dict[str, List[str]] = defaultdict(list)

        def resolve_canonical(name: str) -> str:
            """
            将实体名解析为规范名称。

            Args:
                name: 实体名称

            Returns:
                str: 规范名称
            """
            raw_name = str(name or "").strip()
            if not raw_name:
                return raw_name
            normalized = self._normalize_entity_name(raw_name)
            if not normalized:
                return raw_name
            mapped = str(alias_map.get(normalized) or raw_name).strip()
            return mapped or raw_name

        for item in entities:
            raw_name = str(item.get("name") or "").strip()
            if not raw_name:
                continue
            canonical = resolve_canonical(raw_name)
            canonical_mapping[raw_name] = canonical
            grouped[canonical].append(item)
            if raw_name not in alias_groups[canonical]:
                alias_groups[canonical].append(raw_name)

        disambiguated_entities: List[Dict[str, Any]] = []
        ambiguous_entities: List[Dict[str, Any]] = []
        llm_resolved_count = 0
        llm_attempts = 0
        token_usage = self._empty_token_usage()
        llm_calls = 0

        for canonical, candidates in grouped.items():
            ordered = sorted(candidates, key=lambda x: self._to_score(x.get("score")), reverse=True)
            leader = dict(ordered[0])
            top1 = self._to_score(ordered[0].get("score"))
            top2 = self._to_score(ordered[1].get("score")) if len(ordered) > 1 else 0.0
            ambiguous = len(ordered) > 1 and (top1 - top2) < margin

            if ambiguous and resolver is not None:
                llm_attempts += 1
                candidate_rows = [
                    {
                        "name": str(row.get("name") or "").strip(),
                        "label": str(row.get("label") or "").strip(),
                        "score": self._to_score(row.get("score")),
                    }
                    for row in ordered[:5]
                ]
                try:
                    resolve_result = resolver(str(query or ""), candidate_rows, margin)
                except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError):
                    resolve_result = {}

                if isinstance(resolve_result, dict):
                    self._merge_token_usage(token_usage, resolve_result.get("token_usage"))
                    llm_calls += int(resolve_result.get("llm_calls") or 0)

                resolved_name = str(resolve_result.get("canonical_name") or "").strip() if isinstance(resolve_result, dict) else ""
                resolved_conf = self._to_score(resolve_result.get("confidence")) if isinstance(resolve_result, dict) else 0.0
                if resolved_name and resolved_conf >= float(self.disambiguation_llm_confidence_min):
                    for row in ordered:
                        if str(row.get("name") or "").strip() == resolved_name:
                            leader = dict(row)
                            break
                    canonical = resolved_name
                    ambiguous = False
                    llm_resolved_count += 1

            leader["canonical_name"] = canonical
            leader["aliases"] = alias_groups.get(canonical, [])
            leader["candidate_count"] = len(ordered)
            leader["ambiguous"] = ambiguous
            disambiguated_entities.append(leader)

            if ambiguous:
                ambiguous_entities.append(
                    {
                        "canonical_name": canonical,
                        "candidates": [
                            {
                                "name": str(row.get("name") or ""),
                                "label": str(row.get("label") or ""),
                                "score": self._to_score(row.get("score")),
                            }
                            for row in ordered[:3]
                        ],
                        "score_gap": round(top1 - top2, 6),
                    }
                )

        updated_relationships: List[Dict[str, Any]] = []
        for rel in relationships:
            row = dict(rel)
            src = str(row.get("source_name") or "").strip()
            dst = str(row.get("target_name") or "").strip()
            if src:
                row["source_name"] = resolve_canonical(src)
            if dst:
                row["target_name"] = resolve_canonical(dst)
            updated_relationships.append(row)

        forced_query = ""
        if ambiguous_entities:
            seeds = [str(item.get("canonical_name") or "").strip() for item in ambiguous_entities[:3] if str(item.get("canonical_name") or "").strip()]
            if seeds:
                forced_query = "；".join(seeds) + " 的差异诊断与关键监测指标"

        return {
            "entities": disambiguated_entities,
            "relationships": updated_relationships,
            "ambiguous_entities": ambiguous_entities,
            "canonical_mapping": canonical_mapping,
            "alias_groups": dict(alias_groups),
            "force_supplement_required": bool(ambiguous_entities),
            "forced_supplement_query": forced_query,
            "token_usage": token_usage,
            "llm_calls": llm_calls,
            "disambiguation_meta": {
                "total_candidates": len(entities),
                "resolved_entities": len(disambiguated_entities),
                "ambiguous_count": len(ambiguous_entities),
                "margin": margin,
                "source": "neo4j_properties_primary_llm_fallback",
                "llm_fallback_attempts": llm_attempts,
                "llm_fallback_resolved": llm_resolved_count,
            },
        }

    def _detect_communities_leiden(
        self,
        entities: List[Dict[str, Any]],
        relationships: List[Dict[str, Any]],
        query_terms: List[str],
        query: str,
        top_k: int,
    ) -> Dict[str, Any]:
        """
        对实体关系图做社区发现并输出社区摘要。

        使用 Leiden 算法检测实体社区，计算社区得分并生成报告。

        Args:
            entities: 实体列表
            relationships: 关系列表
            query_terms: 查询关键词列表
            query: 原始查询字符串
            top_k: 返回的顶级社区数量

        Returns:
            Dict[str, Any]: 社区检测结果，包含社区列表、社区报告和检测元数据
        """
        graph = nx.Graph()
        for entity in entities:
            name = str(entity.get("canonical_name") or entity.get("name") or "").strip()
            if not name:
                continue
            graph.add_node(name)

        for rel in relationships:
            src = str(rel.get("source_name") or "").strip()
            dst = str(rel.get("target_name") or "").strip()
            if not src or not dst:
                continue
            weight = self._to_score(rel.get("score"))
            if graph.has_edge(src, dst):
                graph[src][dst]["weight"] += weight
            else:
                graph.add_edge(src, dst, weight=max(weight, 0.01))

        if graph.number_of_nodes() == 0:
            return {
                "communities": [],
                "community_reports": [],
                "community_detection": {"algorithm": "leiden", "community_count": 0, "top_community_score": 0.0},
                "token_usage": self._empty_token_usage(),
                "llm_calls": 0,
            }

        if not _LEIDEN_AVAILABLE or ig is None or leidenalg is None:
            raise RuntimeError("Leiden 依赖未安装，社区检测仅支持 Leiden，请安装 igraph 和 leidenalg")

        node_names = sorted([str(item) for item in graph.nodes()])
        index = {name: idx for idx, name in enumerate(node_names)}
        edge_tuples = []
        edge_weights = []
        for src, dst, attrs in graph.edges(data=True):
            edge_tuples.append((index[str(src)], index[str(dst)]))
            edge_weights.append(max(self._to_score(attrs.get("weight")), 0.01))

        ig_graph = ig.Graph(n=len(node_names), edges=edge_tuples, directed=False)
        partition = leidenalg.find_partition(
            ig_graph,
            leidenalg.RBConfigurationVertexPartition,
            weights=edge_weights if edge_weights else None,
            seed=42,
        )
        raw_communities = [set(node_names[idx] for idx in community) for community in partition]
        communities: List[Dict[str, Any]] = []

        terms = [str(item).lower() for item in query_terms if str(item).strip()]
        for idx, members in enumerate(raw_communities, start=1):
            nodes = sorted([str(item) for item in members if str(item).strip()])
            subgraph = graph.subgraph(nodes)
            density = float(nx.density(subgraph)) if subgraph.number_of_nodes() > 1 else 0.0
            weights = [self._to_score(subgraph[u][v].get("weight")) for u, v in subgraph.edges()]
            avg_weight = (sum(weights) / len(weights)) if weights else 0.0
            evidence_coverage = max(0.0, min(avg_weight, 1.0))
            joined = " ".join(nodes).lower()
            query_alignment = 0.0
            if terms:
                query_alignment = sum(1 for token in terms if token in joined) / max(1, len(terms))
            score = 0.5 * density + 0.3 * evidence_coverage + 0.2 * query_alignment

            communities.append(
                {
                    "community_id": idx,
                    "nodes": nodes,
                    "size": len(nodes),
                    "density": round(density, 6),
                    "evidence_coverage": round(evidence_coverage, 6),
                    "query_alignment": round(query_alignment, 6),
                    "score": round(score, 6),
                }
            )

        communities.sort(key=lambda item: self._to_score(item.get("score")), reverse=True)
        top_communities = communities[: max(1, min(top_k, int(self.community_top_k_max)))]

        reports: List[Dict[str, Any]] = []
        token_usage = self._empty_token_usage()
        llm_calls = 0
        for row in top_communities:
            summary, summary_token_usage = self._generate_community_report_with_llm(query, row, relationships)
            self._merge_token_usage(token_usage, summary_token_usage)
            reports.append(
                {
                    "community_id": row["community_id"],
                    "summary": summary,
                    "nodes": row["nodes"],
                    "score": row["score"],
                }
            )

        top_score = self._to_score(top_communities[0].get("score")) if top_communities else 0.0
        return {
            "communities": communities,
            "community_reports": reports,
            "community_detection": {
                "algorithm": "leiden",
                "community_count": len(communities),
                "top_community_score": round(top_score, 6),
            },
            "token_usage": token_usage,
            "llm_calls": llm_calls,
        }

    async def search_entity_relations(
        self,
        query: str,
        top_k: Optional[int] = None,
        lexical_weight: Optional[float] = None,
        expansion_limit: Optional[int] = None,
        enable_disambiguation: bool = True,
        enable_community_detection: bool = True,
        community_top_k: Optional[int] = None,
        disambiguation_margin: Optional[float] = None,
        disambiguation_resolver: Optional[Callable[[str, List[Dict[str, Any]], float], Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        执行图谱混合检索、实体消歧与社区发现主流程。

        整合向量检索、关键词匹配、关系扩展、实体消歧和社区发现，返回完整的图谱检索结果。

        Args:
            query: 查询字符串
            top_k: 返回结果数量，默认为 10
            lexical_weight: 关键词匹配权重（0.0 ~ 1.0），默认为 0.35
            expansion_limit: 关系扩展数量限制，默认为 30
            enable_disambiguation: 是否启用实体消歧，默认为 True
            enable_community_detection: 是否启用社区发现，默认为 True
            community_top_k: 返回的顶级社区数量，默认为 3
            disambiguation_margin: 消歧阈值，默认为 0.12
            disambiguation_resolver: 可选的 LLM 消歧函数

        Returns:
            Dict[str, Any]: 检索结果，包含实体、关系、消歧信息、社区信息等
        """
        if not query.strip():
            return {"entities": [], "relationships": []}

        effective_top_k = int(top_k) if top_k is not None else int(self.top_k_default)
        effective_lexical_weight = float(lexical_weight) if lexical_weight is not None else float(self.lexical_weight_default)
        effective_expansion_limit = int(expansion_limit) if expansion_limit is not None else int(self.expansion_limit_default)
        effective_community_top_k = int(community_top_k) if community_top_k is not None else int(self.community_top_k_default)
        effective_disambiguation_margin = float(disambiguation_margin) if disambiguation_margin is not None else float(self.disambiguation_margin_default)

        safe_top_k = max(1, min(effective_top_k, int(self.top_k_max)))
        safe_lexical_weight = max(0.0, min(float(effective_lexical_weight), 1.0))
        terms = self._split_query_terms(query)

        primary_rows = await self._search_vector_rows(
            query,
            limit=max(int(self.primary_limit_min), safe_top_k * int(self.primary_limit_multiplier)),
        )
        keyword_rows: List[Dict[str, Any]] = []
        if terms:
            keyword_query = " ".join(terms[:6])
            if keyword_query and keyword_query != query:
                keyword_rows = await self._search_vector_rows(
                    keyword_query,
                    limit=max(int(self.keyword_limit_min), safe_top_k * int(self.keyword_limit_multiplier)),
                )

        merged: Dict[str, Dict[str, Any]] = {}
        for row in primary_rows + keyword_rows:
            doc_id = str(row.get("doc_id") or "").strip()
            if not doc_id:
                continue
            existing = merged.get(doc_id)
            if existing is None:
                merged[doc_id] = dict(row)
                continue
            existing["vector_score"] = max(self._to_score(existing.get("vector_score")), self._to_score(row.get("vector_score")))
            if not existing.get("text") and row.get("text"):
                existing["text"] = row.get("text")

        # 基于高分实体扩展关系，补齐图结构证据（参考RAGFlow的n-hop补边思路）。
        seed_entities = [
            str(item.get("name") or "").strip()
            for item in sorted(merged.values(), key=lambda x: self._to_score(x.get("vector_score")), reverse=True)
            if str(item.get("item_type") or "") == "entity"
        ]
        seed_entities = [name for name in seed_entities if name][: int(self.seed_entity_top_n)]
        neighbor_rows: List[Dict[str, Any]] = []
        if seed_entities:
            neighbor_rows = await self._query_relation_neighbors(
                seed_entities,
                limit=max(
                    int(self.neighbor_limit_min),
                    min(int(effective_expansion_limit), int(self.neighbor_limit_max)),
                ),
            )

        for row in neighbor_rows:
            doc_id = str(row.get("doc_id") or "").strip()
            if not doc_id:
                continue
            if doc_id not in merged:
                merged[doc_id] = {
                    **row,
                    "vector_score": 0.0,
                }

        ranked_rows: List[Dict[str, Any]] = []
        for row in merged.values():
            lexical_score = self._lexical_overlap_score(str(row.get("text") or ""), terms)
            vector_score = self._to_score(row.get("vector_score"))
            hybrid_score = (1.0 - safe_lexical_weight) * vector_score + safe_lexical_weight * lexical_score
            row["lexical_score"] = lexical_score
            row["hybrid_score"] = hybrid_score
            ranked_rows.append(row)

        ranked_rows.sort(key=lambda item: self._to_score(item.get("hybrid_score")), reverse=True)

        entities = []
        relationships = []
        for row in ranked_rows:
            item_type = str(row.get("item_type") or "").strip()
            base = {
                "doc_id": row.get("doc_id"),
                "score": round(self._to_score(row.get("hybrid_score")), 6),
                "vector_score": round(self._to_score(row.get("vector_score")), 6),
                "lexical_score": round(self._to_score(row.get("lexical_score")), 6),
                "text": row.get("text"),
                "timestamp": row.get("timestamp"),
            }

            if item_type == "entity" and len(entities) < safe_top_k:
                entities.append({**base, "name": row.get("name"), "label": row.get("label")})
            elif item_type == "relationship" and len(relationships) < safe_top_k:
                relationships.append(
                    {
                        **base,
                        "source_name": row.get("source_name"),
                        "source_label": row.get("source_label"),
                        "relation_type": row.get("relation_type"),
                        "target_name": row.get("target_name"),
                        "target_label": row.get("target_label"),
                    }
                )

            if len(entities) >= safe_top_k and len(relationships) >= safe_top_k:
                break

        disambiguation_result = {
            "entities": entities,
            "relationships": relationships,
            "ambiguous_entities": [],
            "canonical_mapping": {},
            "alias_groups": {},
            "force_supplement_required": False,
            "forced_supplement_query": "",
            "token_usage": self._empty_token_usage(),
            "llm_calls": 0,
            "disambiguation_meta": {
                "total_candidates": len(entities),
                "resolved_entities": len(entities),
                "ambiguous_count": 0,
                "margin": effective_disambiguation_margin,
                "source": "disabled",
            },
        }
        if enable_disambiguation:
            disambiguation_result = await self._disambiguate_entities(
                query=query,
                entities=entities,
                relationships=relationships,
                margin=max(0.0, float(effective_disambiguation_margin)),
                resolver=disambiguation_resolver,
            )

        community_result = {
            "communities": [],
            "community_reports": [],
            "community_detection": {
                "algorithm": "disabled",
                "community_count": 0,
                "top_community_score": 0.0,
            },
            "token_usage": self._empty_token_usage(),
            "llm_calls": 0,
        }
        if enable_community_detection:
            community_result = self._detect_communities_leiden(
                entities=disambiguation_result["entities"],
                relationships=disambiguation_result["relationships"],
                query_terms=terms,
                query=query,
                top_k=effective_community_top_k,
            )

        token_usage = self._empty_token_usage()
        self._merge_token_usage(token_usage, disambiguation_result.get("token_usage"))
        self._merge_token_usage(token_usage, community_result.get("token_usage"))
        llm_calls = int(disambiguation_result.get("llm_calls") or 0) + int(
            community_result.get("llm_calls") or 0
        )

        return {
            "entities": disambiguation_result["entities"],
            "relationships": disambiguation_result["relationships"],
            "disambiguated_entities": disambiguation_result["entities"],
            "ambiguous_entities": disambiguation_result["ambiguous_entities"],
            "canonical_mapping": disambiguation_result["canonical_mapping"],
            "alias_groups": disambiguation_result["alias_groups"],
            "communities": community_result["communities"],
            "community_reports": community_result["community_reports"],
            "collection_name": self.collection_name,
            "token_usage": token_usage,
            "llm_calls": llm_calls,
            "retrieval_meta": {
                "query_terms": terms,
                "vector_rows": len(primary_rows) + len(keyword_rows),
                "expanded_rows": len(neighbor_rows),
                "merged_rows": len(merged),
                "lexical_weight": safe_lexical_weight,
                "force_supplement_required": bool(disambiguation_result.get("force_supplement_required")),
                "forced_supplement_query": str(disambiguation_result.get("forced_supplement_query") or ""),
                "disambiguation": disambiguation_result.get("disambiguation_meta", {}),
                "community_detection": community_result.get("community_detection", {}),
                "llm_calls": llm_calls,
            },
        }

    async def get_stats(self) -> Dict[str, Any]:
        """
        返回当前图谱向量库统计信息。

        Args:
            None

        Returns:
            Dict[str, Any]: 统计信息，包含集合名称、实体数、关系数、标签分布等
        """
        # Milvus query 的 offset + limit 窗口默认上限为 16384
        max_query_window = 16384
        result = self.client.query(
            collection_name=self.collection_name,
            filter="doc_id != ''",
            output_fields=["item_type", "label"],
            limit=max_query_window,
        )

        entity_count = sum(1 for row in result if row.get("item_type") == "entity")
        rel_count = sum(1 for row in result if row.get("item_type") == "relationship")

        label_distribution: Dict[str, int] = {}
        for row in result:
            if row.get("item_type") == "entity" and row.get("label"):
                key = row["label"]
                label_distribution[key] = label_distribution.get(key, 0) + 1

        return {
            "collection_name": self.collection_name,
            "total_kg_documents": len(result),
            "entities_count": entity_count,
            "relationships_count": rel_count,
            "label_distribution": label_distribution,
            "query_window_limit": max_query_window,
            "stats_may_be_truncated": len(result) >= max_query_window,
        }


