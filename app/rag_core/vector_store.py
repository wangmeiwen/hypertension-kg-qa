# -*- coding:utf-8 -*-
"""
Milvus 向量存储与检索模块

本模块封装了基于 Milvus 的向量数据库操作，支持混合检索与重排序功能。

核心功能：
- 混合检索：结合稠密向量（BGE-M3）和稀疏向量进行语义检索
- RRF 融合：使用 Reciprocal Rank Fusion 算法融合多路检索结果
- 重排序：使用 BGE-Reranker 模型对检索结果进行二次排序
- 父子文档：支持父子文档结构，保持文档完整性

主要类：
- VectorStore: 向量存储类，负责集合管理、文档添加、混合检索

检索流程：
1. 用户查询 -> BGE-M3 嵌入 -> 稠密向量 + 稀疏向量
2. 分别执行稠密检索和稀疏检索
3. 使用 RRF 融合结果
4. 使用 BGE-Reranker 重排序，返回 top-k 结果
"""

# 导入 BGE-M3 嵌入函数，用于生成文档和查询的向量表示
import logging
import os
import re
import sys
import uuid
from threading import Lock

import numpy as np
import torch.cuda
from milvus_model.hybrid import BGEM3EmbeddingFunction  # type: ignore[import-untyped]
# 导入 Milvus 相关类，用于操作向量数据库
from pymilvus import DataType, MilvusClient  # type: ignore[import-untyped]
# 导入 Document 类，用于创建文档对象
from langchain_core.documents import Document
# 导入 CrossEncoder，用于重排序和 NLI 判断
from sentence_transformers import CrossEncoder

# 获取当前文件所在目录的绝对路径
current_dir = os.path.dirname(os.path.abspath(__file__))

# 获取core文件所在的目录的绝对路径
rag_qa_path = os.path.dirname(current_dir)
sys.path.insert(0, rag_qa_path)
# 获取根目录文件所在的绝对位置
project_root = os.path.dirname(rag_qa_path)
sys.path.insert(0, project_root)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from app.core.config import Config  # noqa: E402
from app.core.perf_logger import log_duration  # noqa: E402

conf = Config()


def _to_float32_vector(vec, expected_dim):
    """
    将任意向量输入规范化为 Milvus 所需的一维 float32 向量。

    Args:
        vec: 原始向量对象，支持 tensor、ndarray 或 list。
        expected_dim: 期望向量维度。

    Returns:
        numpy.ndarray: 规范化后的 float32 一维向量。
    """
    if hasattr(vec, "detach"):
        vec = vec.detach().cpu().numpy()
    arr = np.asarray(vec, dtype=np.float32)
    if arr.ndim != 1:
        arr = arr.reshape(-1)
    if expected_dim and arr.shape[0] != int(expected_dim):
        raise ValueError(f"dense vector dim mismatch: got {arr.shape[0]}, expected {expected_dim}")
    return arr


def _generate_uuid_int64_id() -> int:
    """
    生成满足 Milvus INT64 主键约束的随机 ID。

    Args:
        None。

    Returns:
        int: 正 INT64 随机 ID。
    """
    return uuid.uuid4().int & ((1 << 63) - 1)


def _safe_int(value, default=None):
    """
    安全转换整型，失败时返回默认值。
    Args:
        value: 原始值。
        default: 默认值。
    Returns:
        int | None: 转换后的整型值。
    """
    try:
        text = str(value).strip()
        if not text:
            return default
        return int(text)
    except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError):
        return default


# 定义 VectorStore 类，封装向量存储和检索功能
class VectorStore:
    """封装通用文档向量存储、混合检索与重排序流程。"""

    _MODEL_LOCK = Lock()
    _CLIENT_LOCK = Lock()
    _EMBEDDING_FUNCTION_CACHE: dict[str, BGEM3EmbeddingFunction] = {}
    _RERANKER_CACHE: dict[str, CrossEncoder] = {}
    _CLIENT_CACHE: dict[str, MilvusClient] = {}

    @classmethod
    def _get_cached_reranker(cls, model_path: str, device: str) -> CrossEncoder:
        """获取（或加载）共享 Reranker 模型实例。

        Args:
            model_path: Reranker 模型本地路径。
            device: 推理设备标识（如 cpu/cuda）。

        Returns:
            CrossEncoder: 复用的重排序模型实例。
        """
        key = f"{model_path}|{device}"
        with cls._MODEL_LOCK:
            model = cls._RERANKER_CACHE.get(key)
            if model is None:
                with log_duration("milvus.load_reranker", path=model_path):
                    model = CrossEncoder(model_path, device=device, local_files_only=True)
                cls._RERANKER_CACHE[key] = model
        return model

    @classmethod
    def _get_cached_embedding_function(
        cls, model_path: str, device: str, use_fp16: bool
    ) -> BGEM3EmbeddingFunction:
        """获取（或加载）共享 embedding 函数实例。

        Args:
            model_path: Embedding 模型路径。
            device: 推理设备标识。
            use_fp16: 是否启用 FP16 推理。

        Returns:
            BGEM3EmbeddingFunction: 可复用 embedding 函数对象。
        """
        key = f"{model_path}|{device}|{int(use_fp16)}"
        with cls._MODEL_LOCK:
            model = cls._EMBEDDING_FUNCTION_CACHE.get(key)
            if model is None:
                with log_duration("milvus.load_embedding_model", path=model_path):
                    model = BGEM3EmbeddingFunction(
                        model_name_or_path=model_path,
                        use_fp16=use_fp16,
                        device=device,
                    )
                cls._EMBEDDING_FUNCTION_CACHE[key] = model
        return model

    @classmethod
    def _get_cached_client(cls, host: str, port: str, database: str) -> MilvusClient:
        """获取（或创建）共享 MilvusClient 实例。

        Args:
            host: Milvus 主机地址。
            port: Milvus 端口。
            database: Milvus 数据库名。

        Returns:
            MilvusClient: 复用的客户端连接实例。
        """
        key = f"http://{host}:{port}|{database}"
        with cls._CLIENT_LOCK:
            client = cls._CLIENT_CACHE.get(key)
            if client is None:
                with log_duration("milvus.connect", host=host, port=port, database=database):
                    client = MilvusClient(uri=f"http://{host}:{port}", db_name=database)
                cls._CLIENT_CACHE[key] = client
        return client

    def _search_with_params(self, search_kwargs: dict, search_params: dict):
        """用当前 pymilvus 客户端参数格式执行向量检索。

        Args:
            search_kwargs: Milvus search 的基础关键字参数。
            search_params: 向量检索参数，如 metric_type、nprobe 等。

        Returns:
            list: Milvus 原始命中结果。
        """
        return self.client.search(**search_kwargs, search_params=search_params)

    def __init__(self,
                 collection_name=conf.MILVUS_COLLECTION_NAME,
                 host=conf.MILVUS_HOST,
                 port=conf.MILVUS_PORT,
                 database=conf.MILVUS_DATABASE_NAME):
        """
        初始化向量存储。

        Args:
            collection_name: Milvus 集合名称。
            host: Milvus 主机地址。
            port: Milvus 端口。
            database: Milvus 数据库名称。

        Returns:
            None。
        """
        with log_duration("milvus.vector_store_init", collection=collection_name):
            logger.info('===='*20)
        # 设置 Milvus 集合名称
        self.collection_name = collection_name
        # 设置 Milvus 主机地址
        self.host = host
        # 设置 Milvus 端口号
        self.port = port
        # 设置 Milvus 数据库名称
        self.database = database
        # 设置日志记录器
        self.logger = logger
        # 检查CUDA是否可用
        self.device ='cuda' if torch.cuda.is_available() else 'cpu'
        # 日志提醒使用的是什么设备
        self.logger.info(f"使用设置：{self.device}")
        # 初始化 BGE-Reranker 模型，用于重排序检索结果
        reranker_path = conf.RERANKER_MODEL_PATH
        # 将 Windows 反斜杠转换为正斜杠，避免 Hugging Face 模型加载错误
        reranker_path = reranker_path.replace('\\', '/')
        # 如果是相对路径，基于项目根目录解析
        if not os.path.isabs(reranker_path):
            reranker_path = os.path.join(project_root, reranker_path)
            reranker_path = reranker_path.replace('\\', '/')
        # 检查 reranker 模型是否存在，不存在则警告
        if not os.path.exists(reranker_path):
            self.logger.warning("未找到 Reranker 模型: %s", reranker_path)
        self.reranker = self._get_cached_reranker(reranker_path, self.device)
        # 初始化 BGE-M3 嵌入函数，使用 CPU 设备，不启用 FP16
        m3_path = conf.EMBEDDING_MODEL_PATH
        # 将 Windows 反斜杠转换为正斜杠，避免 Hugging Face 模型加载错误
        m3_path = m3_path.replace('\\', '/')
        # 如果是相对路径，基于项目根目录解析
        if not os.path.isabs(m3_path):
            m3_path = os.path.join(project_root, m3_path)
            m3_path = m3_path.replace('\\', '/')
        if not os.path.exists(m3_path):
            self.logger.warning("Embedding model not found at %s.", m3_path)
        self.embedding_function = self._get_cached_embedding_function(
            m3_path,
            self.device,
            use_fp16=(self.device == "cuda"),
        )
        # 获取稠密向量的维度# 1024
        self.dense_dim = self.embedding_function.dim["dense"]
        # 初始化 Milvus 客户端，连接到指定主机和数据库
        self.client = self._get_cached_client(
            str(self.host),
            str(self.port),
            str(self.database),
        )
        self.collection_fields = set()
        # 调用方法创建或加载 Milvus 集合
        with log_duration("milvus.create_or_load_collection", collection=self.collection_name):
            self._create_or_load_collection()

    def _refresh_collection_fields(self):
        """
        刷新当前集合字段缓存。

        Args:
            None。

        Returns:
            None。
        """
        self.collection_fields = set()
        try:
            desc = self.client.describe_collection(collection_name=self.collection_name)
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError):
            try:
                desc = self.client.describe_collection(self.collection_name)
            except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as e2:
                logger.warning(
                    "获取集合字段失败: error_type=%s error=%s",
                    type(e2).__name__,
                    e2,
                )
                desc = {}

        # 确保在 try/except 后仍然提取字段信息
        try:
            fields = desc.get("fields", []) if isinstance(desc, dict) else []
            for field in fields:
                if isinstance(field, dict):
                    name = field.get("name") or field.get("field_name")
                else:
                    name = getattr(field, "name", None) or getattr(field, "field_name", None)
                if name:
                    self.collection_fields.add(name)
        except Exception as e:
            logger.warning(
                "提取集合字段信息失败: error_type=%s error=%s",
                type(e).__name__,
                e,
            )

    def _create_or_load_collection(self):
        """
        创建集合或加载已有集合并确保可查询。

        Args:
            None。

        Returns:
            None。
        """
        # 检查指定集合是否已经存在
        if not self.client.has_collection(self.collection_name):
            # 创建集合 Schema，禁用自动 ID，启用动态字段
            schema = self.client.create_schema(auto_id=False, enable_dynamic_field=True)
            # 添加 ID 字段，作为主键，INT64 类型（与代码中生成的整数ID一致）
            schema.add_field(field_name="id", datatype=DataType.INT64, is_primary=True)
            # 添加文本字段，VARCHAR 类型，最大长度 65535
            schema.add_field(field_name="text", datatype=DataType.VARCHAR, max_length=65535)
            # 添加稠密向量字段，FLOAT_VECTOR 类型，维度由嵌入函数指定
            schema.add_field(field_name="dense_vector", datatype=DataType.FLOAT_VECTOR, dim=self.dense_dim)
            # 添加稀疏向量字段，SPARSE_FLOAT_VECTOR 类型
            schema.add_field(field_name="sparse_vector", datatype=DataType.SPARSE_FLOAT_VECTOR)
            # 添加父块 ID 字段，VARCHAR 类型，最大长度 100
            schema.add_field(field_name="parent_id", datatype=DataType.VARCHAR, max_length=100)
            # 添加父块内容字段，VARCHAR 类型，最大长度 65535
            schema.add_field(field_name="parent_content", datatype=DataType.VARCHAR, max_length=65535)
            # 添加学科类别字段，VARCHAR 类型，最大长度 50
            schema.add_field(field_name="source", datatype=DataType.VARCHAR, max_length=50)
            # 添加时间戳字段，VARCHAR 类型，最大长度 50
            schema.add_field(field_name="timestamp", datatype=DataType.VARCHAR, max_length=50)
            # 添加页码字段，INT64 类型，可为空
            schema.add_field(field_name="page_num", datatype=DataType.INT64)
            # 添加起始页码字段，INT64 类型，可为空
            schema.add_field(field_name="start_page", datatype=DataType.INT64)
            # 添加结束页码字段，INT64 类型，可为空
            schema.add_field(field_name="end_page", datatype=DataType.INT64)

            # 创建索引参数对象
            index_params = self.client.prepare_index_params()
            # 为稠密向量字段添加 IVF_FLAT 索引，度量类型为内积 (IP)
            index_params.add_index(
                field_name="dense_vector",
                index_name="dense_index",
                index_type="IVF_FLAT",
                metric_type="IP",
                params={"nlist": 128} # 簇
            )

            # 为稀疏向量字段添加 SPARSE_INVERTED_INDEX 索引，度量类型为内积 (IP)
            index_params.add_index(
                field_name="sparse_vector",
                index_name="sparse_index",
                index_type="SPARSE_INVERTED_INDEX", # 稀疏
                metric_type="IP",
                params={"drop_ratio_build": 0.2} # 搜索忽略
            )

            # 创建 Milvus 集合，应用定义的 Schema 和索引参数
            self.client.create_collection(collection_name=self.collection_name, schema=schema,
                                          index_params=index_params)
            # 记录创建集合的日志
            logger.info(f"已创建集合 {self.collection_name}")
            self.collection_fields = {
                "id", "text", "dense_vector", "sparse_vector",
                "parent_id", "parent_content", "source", "timestamp",
                "page_num", "start_page", "end_page"
            }
        # 如果集合已存在
        else:
            # 记录加载集合的日志
            logger.info(f"已加载集合 {self.collection_name}")
            self._refresh_collection_fields()
        # 将集合加载到内存，确保可立即查询
        self.client.load_collection(self.collection_name)

    def add_documents(self, documents):
        """
        批量嵌入并写入文档到 Milvus。

        Args:
            documents: LangChain Document 列表。

        Returns:
            None。
        """
        with log_duration("milvus.add_documents", count=len(documents), collection=self.collection_name):
            texts = [doc.page_content for doc in documents]

            with log_duration("milvus.embed_documents", count=len(texts)):
                embeddings = self.embedding_function(texts)

            data = []
            generated_ids = set()
            for i, doc in enumerate(documents):
                user_id = doc.metadata.get("id")
                if user_id is not None and str(user_id).strip() != "":
                    try:
                        doc_id = int(user_id)
                    except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError):
                        try:
                            doc_id = uuid.UUID(str(user_id)).int & ((1 << 63) - 1)
                        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError):
                            self.logger.warning("无效 metadata.id=%s，改用 UUID 生成 ID", user_id)
                            doc_id = _generate_uuid_int64_id()
                else:
                    doc_id = _generate_uuid_int64_id()

                # 同批次如果发生冲突，则继续生成新的 UUID ID。
                if doc_id in generated_ids:
                    while doc_id in generated_ids:
                        doc_id = _generate_uuid_int64_id()
                generated_ids.add(doc_id)

                sparse_vector = {}
                sparse_emb = embeddings["sparse"]
                if hasattr(sparse_emb, 'getrow'):
                    row = sparse_emb.getrow(i)
                else:
                    row = sparse_emb[i]

                if hasattr(row, 'toarray'):
                    row = row.toarray().flatten()

                if hasattr(row, 'indices'):
                    indices = row.indices
                    values = row.data
                else:
                    indices = row.nonzero()[0] if hasattr(row, 'nonzero') else [j for j, v in enumerate(row) if v != 0]
                    values = [row[j] for j in indices] if not hasattr(row, 'nonzero') else row[row.nonzero()[0]]

                for idx, value in zip(indices, values):
                    sparse_vector[idx] = value

                item = {
                    "id": doc_id,
                    "text": doc.page_content,
                    "parent_id": doc.metadata.get("parent_id", ""),
                    "parent_content": doc.metadata.get("parent_content", doc.page_content),
                    "source": doc.metadata.get("source", "unknown"),
                    "timestamp": doc.metadata.get("timestamp", "unknown")
                }

                dense_vec = _to_float32_vector(embeddings["dense"][i], self.dense_dim)

                if "dense_vector" in self.collection_fields:
                    item["dense_vector"] = dense_vec
                if "embedding" in self.collection_fields:
                    item["embedding"] = dense_vec
                if "sparse_vector" in self.collection_fields:
                    item["sparse_vector"] = sparse_vector

                if "page_num" in self.collection_fields:
                    page_num = doc.metadata.get("page_num")
                    item["page_num"] = int(page_num) if page_num is not None else 0
                if "start_page" in self.collection_fields:
                    start_page = doc.metadata.get("start_page")
                    item["start_page"] = int(start_page) if start_page is not None else 0
                if "end_page" in self.collection_fields:
                    end_page = doc.metadata.get("end_page")
                    item["end_page"] = int(end_page) if end_page is not None else 0

                data.append(item)

            if data:
                with log_duration("milvus.upsert", count=len(data), collection=self.collection_name):
                    self.client.upsert(collection_name=self.collection_name, data=data)
                logger.info(f"已插入或更新 {len(data)} 个文档")

    def _reciprocal_rank_fusion(self, dense_results, sparse_results, k=conf.RRF_K):
        """
        使用 RRF 融合稠密检索与稀疏检索结果。

        Args:
            dense_results: 稠密向量检索结果。
            sparse_results: 稀疏向量检索结果。
            k: RRF 融合参数。

        Returns:
            list: 按融合分数排序后的实体列表。
        """
        from collections import defaultdict

        # 存储每个文档的 RRF 分数
        rrf_scores = defaultdict(float)
        # 存储文档 ID 到文档对象的映射
        doc_map = {}

        # 处理稠密向量检索结果
        for rank, hit in enumerate(dense_results):
            doc_id = hit["entity"].get("parent_id") or hit["entity"].get("text")
            rrf_scores[doc_id] += 1.0 / (k + rank + 1)  # rank 从 0 开始，所以 +1
            if doc_id not in doc_map:
                doc_map[doc_id] = hit["entity"]

        # 处理稀疏向量检索结果
        for rank, hit in enumerate(sparse_results):
            doc_id = hit["entity"].get("parent_id") or hit["entity"].get("text")
            rrf_scores[doc_id] += 1.0 / (k + rank + 1)
            if doc_id not in doc_map:
                doc_map[doc_id] = hit["entity"]

        # 按 RRF 分数降序排序
        sorted_docs = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)

        # 返回排序后的文档对象列表
        fused_entities = []
        for doc_id, score in sorted_docs:
            entity = dict(doc_map[doc_id]) if isinstance(doc_map[doc_id], dict) else {"text": str(doc_map[doc_id])}
            entity["_rrf_score"] = float(score)
            fused_entities.append(entity)
        return fused_entities

    def _build_search_output_fields(self):
        """
        构建检索输出字段，确保页码与子块元数据贯穿返回。
        Args:
            None。
        Returns:
            list[str]: 输出字段列表。
        """
        return [
            "text",
            "parent_id",
            "parent_content",
            "source",
            "timestamp",
            "page_num",
            "start_page",
            "end_page",
            "chunk_id",
            "parent_index",
            "sub_index",
        ]

    def _resolve_internal_recall_limit(self, k, configured_limit=0):
        """
        计算内部召回候选数，避免过早截断正确证据。
        Args:
            k: 最终返回上限。
            configured_limit: 分路召回上限配置。
        Returns:
            int: 内部召回候选数。
        """
        base_limit = max(int(k or 1) * 3, int(conf.RAG_INTERNAL_RECALL_TOP_K or 12))
        if int(configured_limit or 0) > 0:
            base_limit = max(base_limit, int(configured_limit))
        return max(1, base_limit)

    def _is_statistical_query(self, query):
        """
        判断查询是否属于数值统计口径题。
        Args:
            query: 查询文本。
        Returns:
            bool: 是否命中统计题特征。
        """
        text = str(query or "").strip()
        if not text:
            return False
        statistical_terms = ("患病率", "加权", "成人", "18岁", "18 岁", "比例", "调查", "统计")
        has_year = any(ch.isdigit() for ch in text)
        return has_year and any(token in text for token in statistical_terms)

    def _page_bias_bonus(self, query, metadata):
        """
        对统计题的前言/流行病学页施加轻量排序偏置。
        Args:
            query: 查询文本。
            metadata: 文档元数据。
        Returns:
            float: 页码偏置分。
        """
        if not self._is_statistical_query(query):
            return 0.0
        meta = metadata if isinstance(metadata, dict) else {}
        start_page = _safe_int(meta.get("start_page"), None)
        end_page = _safe_int(meta.get("end_page"), None)
        page_num = _safe_int(meta.get("page_num"), None)
        candidate_page = start_page if start_page is not None else page_num
        if candidate_page is None:
            return 0.0
        if candidate_page <= 3:
            return 0.2
        if candidate_page <= 6:
            return 0.08
        if end_page is not None and end_page <= 6:
            return 0.04
        return 0.0

    def _normalize_match_text(self, value):
        """
        归一化文本，便于做数字与关键词匹配。
        Args:
            value: 原始文本。
        Returns:
            str: 去空白后的文本。
        """
        return re.sub(r"\s+", "", str(value or "")).strip()

    def _extract_query_terms(self, query):
        """
        提取对事实型问答更敏感的年份、年龄和关键口径词。
        Args:
            query: 查询文本。
        Returns:
            list[str]: 去重后的查询关键项。
        """
        normalized = self._normalize_match_text(query)
        if not normalized:
            return []
        terms = []
        seen = set()
        for matched in re.findall(r"\d{4}年|\d{4}", normalized):
            if matched and matched not in seen:
                terms.append(matched)
                seen.add(matched)
        for matched in re.findall(r"\d+岁(?:及以上)?", normalized):
            if matched and matched not in seen:
                terms.append(matched)
                seen.add(matched)
        for keyword in (
            "高血压",
            "患病率",
            "加权",
            "成人",
            "成年人",
            "年龄≥18岁",
            "18岁及以上",
            "知晓率",
            "治疗率",
            "控制率",
        ):
            if keyword in normalized and keyword not in seen:
                terms.append(keyword)
                seen.add(keyword)
        return terms

    def _content_match_bonus(self, query, text):
        """
        依据查询关键词在证据子块中的显式命中情况增加轻量加分。
        Args:
            query: 查询文本。
            text: 证据子块文本。
        Returns:
            float: 匹配加分。
        """
        normalized_query = self._normalize_match_text(query)
        normalized_text = self._normalize_match_text(text)
        if not normalized_query or not normalized_text:
            return 0.0
        terms = self._extract_query_terms(normalized_query)
        if not terms:
            return 0.0
        matched = [term for term in terms if term in normalized_text]
        if not matched:
            return 0.0
        bonus = (len(matched) / len(terms)) * 0.6
        if self._is_statistical_query(query) and any(term.endswith("年") or term.isdigit() for term in matched):
            bonus += 0.1
        if self._is_statistical_query(query) and "患病率" in matched and "高血压" in matched:
            bonus += 0.12
        return float(bonus)

    def _dedupe_sub_chunks(self, docs):
        """
        对子块结果去重，避免父块吞掉更精确证据。
        Args:
            docs: Document 列表。
        Returns:
            list[Document]: 去重后的子块列表。
        """
        unique_docs = []
        seen = set()
        for doc in docs:
            metadata = doc.metadata if isinstance(doc.metadata, dict) else {}
            dedupe_key = (
                str(metadata.get("chunk_id") or "").strip()
                or "|".join(
                    [
                        str(metadata.get("parent_id") or "").strip(),
                        str(metadata.get("sub_index") or "").strip(),
                        str(metadata.get("start_page") or metadata.get("page_num") or "").strip(),
                    ]
                )
                or str(doc.page_content or "")[:240]
            )
            if not dedupe_key or dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            unique_docs.append(doc)
        return unique_docs

    def _rerank_sub_chunks(self, query, sub_chunks, limit):
        """
        对命中的子块进行重排并应用轻量页级偏置。
        Args:
            query: 查询文本。
            sub_chunks: 子块文档列表。
            limit: 最终返回数量上限。
        Returns:
            list[Document]: 重排后的子块列表。
        """
        if not sub_chunks:
            return []
        rerank_cap = max(limit, int(conf.CANDIDATE_M or limit), 2)
        rerank_docs = self._dedupe_sub_chunks(sub_chunks)[:rerank_cap]
        if len(rerank_docs) < 2:
            return rerank_docs[:limit]
        pairs = [[query, str(doc.page_content or "")] for doc in rerank_docs]
        with log_duration("milvus.rerank", pairs=len(pairs)):
            try:
                raw_scores = self.reranker.predict(pairs)
            except Exception as e:
                logger.warning("重排序失败: %s", e)
                return rerank_docs[:limit]
        ranked_rows = []
        for doc, raw_score in zip(rerank_docs, raw_scores):
            metadata = doc.metadata if isinstance(doc.metadata, dict) else {}
            page_bonus = self._page_bias_bonus(query, metadata)
            content_bonus = self._content_match_bonus(query, doc.page_content)
            search_score = float(metadata.get("search_score") or 0.0)
            if self._is_statistical_query(query):
                final_score = (search_score * 8.0) + (float(page_bonus) * 1.5) + (float(content_bonus) * 1.2) + (float(raw_score) * 0.25)
            else:
                final_score = float(raw_score) + (search_score * 0.2) + float(page_bonus) + float(content_bonus)
            metadata["rerank_score"] = float(raw_score)
            metadata["page_bias_bonus"] = float(page_bonus)
            metadata["content_match_bonus"] = float(content_bonus)
            metadata["final_rank_score"] = float(final_score)
            doc.metadata = metadata
            ranked_rows.append((final_score, doc))
        ranked_rows.sort(key=lambda item: item[0], reverse=True)
        return [doc for _, doc in ranked_rows[:limit]]

    def _log_candidate_diagnostics(self, stage, query, rows, max_items=5):
        """
        输出候选诊断日志，便于定位召回与重排问题。
        Args:
            stage: 阶段名称。
            query: 查询文本。
            rows: 命中列表。
            max_items: 最多输出条数。
        Returns:
            None。
        """
        if not rows:
            logger.info(
                "rag_candidate_diagnostics stage=%s query=%s hits=0",
                stage,
                str(query or "").strip()[:120],
            )
            return
        for index, row in enumerate(rows[:max_items], start=1):
            score = None
            metadata = {}
            snippet = ""
            if isinstance(row, Document):
                metadata = row.metadata if isinstance(row.metadata, dict) else {}
                score = metadata.get("final_rank_score", metadata.get("rerank_score"))
                snippet = str(row.page_content or "")
            elif isinstance(row, dict) and "entity" in row:
                entity = row.get("entity") if isinstance(row.get("entity"), dict) else {}
                metadata = entity
                score = row.get("distance")
                snippet = str(entity.get("text") or entity.get("parent_content") or "")
            elif isinstance(row, dict):
                metadata = row
                score = row.get("_rrf_score")
                snippet = str(row.get("text") or row.get("parent_content") or "")
            logger.info(
                "rag_candidate_diagnostics stage=%s rank=%s score=%s page=%s start_page=%s end_page=%s chunk_id=%s snippet=%s query=%s",
                stage,
                index,
                round(float(score), 6) if score is not None else None,
                metadata.get("page_num"),
                metadata.get("start_page"),
                metadata.get("end_page"),
                metadata.get("chunk_id"),
                str(snippet).replace("\n", " ").strip()[:120],
                str(query or "").strip()[:120],
            )

    async def hybrid_search_with_rerank(self, query, k=conf.RETRIEVAL_K, source_filter=None):
        """
        执行混合检索并返回重排序后的子块文档。

        流程包括查询 embedding、稠密检索、稀疏检索、RRF 融合、
        子块去重以及 reranker 二次排序。若集合没有稀疏向量字段，
        会自动降级为稠密检索加重排。

        Args:
            query: 查询文本。
            k: 检索结果数。
            source_filter: 来源过滤条件。

        Returns:
            list[Document]: 重排序后的子块文档列表。
        """
        with log_duration("milvus.hybrid_search_with_rerank", k=k, source_filter=source_filter):
            final_limit = max(1, int(k or 1))
            internal_limit = self._resolve_internal_recall_limit(final_limit)
            dense_limit = self._resolve_internal_recall_limit(final_limit, int(conf.RAG_DENSE_INTERNAL_RECALL_TOP_K or 0))
            sparse_limit = self._resolve_internal_recall_limit(final_limit, int(conf.RAG_SPARSE_INTERNAL_RECALL_TOP_K or 0))
            output_fields = self._build_search_output_fields()

            with log_duration("milvus.embed_query"):
                query_embeddings = self.embedding_function([query])

            dense_query_vector = _to_float32_vector(query_embeddings["dense"][0], self.dense_dim)
            sparse_query_vector = {}
            sparse_emb = query_embeddings["sparse"]
            if hasattr(sparse_emb, "getrow"):
                row = sparse_emb.getrow(0)
            else:
                row = sparse_emb[0]
            if hasattr(row, "toarray"):
                row = row.toarray().flatten()
            if hasattr(row, "indices"):
                indices = row.indices
                values = row.data
            else:
                indices = row.nonzero()[0] if hasattr(row, "nonzero") else [j for j, value in enumerate(row) if value != 0]
                values = [row[j] for j in indices] if not hasattr(row, "nonzero") else row[row.nonzero()[0]]
            for idx, value in zip(indices, values):
                sparse_query_vector[int(idx)] = float(value)

            filter_expr = f"source == '{source_filter}'" if source_filter else ""
            dense_field = "dense_vector" if "dense_vector" in self.collection_fields else ("embedding" if "embedding" in self.collection_fields else None)
            sparse_enabled = "sparse_vector" in self.collection_fields
            if dense_field is None:
                raise ValueError(f"集合 {self.collection_name} 缺少可用向量字段")
            dense_metric = "IP" if dense_field == "dense_vector" else "COSINE"

            with log_duration("milvus.search_dense", mode="hybrid", limit=dense_limit):
                dense_results = self._search_with_params(
                    search_kwargs={
                        "collection_name": self.collection_name,
                        "data": [dense_query_vector],
                        "anns_field": dense_field,
                        "limit": dense_limit,
                        "output_fields": output_fields,
                        "filter": filter_expr,
                    },
                    search_params={"metric_type": dense_metric, "params": {"nprobe": int(conf.MILVUS_SEARCH_NPROBE)}},
                )[0]
            self._log_candidate_diagnostics("dense", query, dense_results)

            if not sparse_enabled:
                sub_chunks = self._dedupe_sub_chunks([self._doc_from_hit(hit) for hit in dense_results])
                reranked_sub_chunks = self._rerank_sub_chunks(query, sub_chunks, limit=min(len(sub_chunks), internal_limit))
                self._log_candidate_diagnostics("rerank_dense_only", query, reranked_sub_chunks)
                return reranked_sub_chunks[:final_limit]

            sparse_results = []
            if sparse_query_vector:
                with log_duration("milvus.search_sparse", mode="hybrid", limit=sparse_limit):
                    sparse_results = self._search_with_params(
                        search_kwargs={
                            "collection_name": self.collection_name,
                            "data": [sparse_query_vector],
                            "anns_field": "sparse_vector",
                            "limit": sparse_limit,
                            "output_fields": output_fields,
                            "filter": filter_expr,
                        },
                        search_params={"metric_type": "IP", "params": {}},
                    )[0]
            self._log_candidate_diagnostics("sparse", query, sparse_results)

            logger.info("使用 RRF 算法融合检索结果")
            fused_entities = self._reciprocal_rank_fusion(dense_results, sparse_results)
            self._log_candidate_diagnostics("fused", query, fused_entities)

            sub_chunks = self._dedupe_sub_chunks([self._doc_from_hit(entity) for entity in fused_entities])
            reranked_sub_chunks = self._rerank_sub_chunks(query, sub_chunks, limit=min(len(sub_chunks), internal_limit))
            self._log_candidate_diagnostics("rerank", query, reranked_sub_chunks)
            return reranked_sub_chunks[:final_limit]

    def _get_unique_parent_docs(self, sub_chunks):
        """
        从子块结果中提取去重父文档。

        该函数保留父块内容并沿用首个命中子块元数据，
        适用于需要把子块证据折叠为父块展示的调用路径。

        Args:
            sub_chunks: 子块文档列表。

        Returns:
            list[Document]: 去重后的父文档列表。
        """
        # 初始化集合，用于存储已处理的父块内容（去重）
        parent_contents = set()
        # 初始化列表，用于存储唯一父文档
        unique_docs = []
        # 遍历所有子块
        for chunk in sub_chunks:
            # 获取子块的父块内容，默认为子块内容
            parent_content = chunk.metadata.get("parent_content", chunk.page_content)
            # 检查父块内容是否非空且未重复
            if parent_content and parent_content not in parent_contents:
                # 创建新的 Document 对象，包含父块内容和元数据
                unique_docs.append(Document(page_content=parent_content, metadata=chunk.metadata))
                # 将父块内容添加到去重集合
                parent_contents.add(parent_content)
            # 返回去重后的父文档列表
        return unique_docs

    def _doc_from_hit(self, hit):
        """
        将 Milvus 命中记录转换为 LangChain Document。

        Args:
            hit: Milvus 命中记录。

        Returns:
            Document: 转换后的文档对象。
        """
        hit_obj = hit if isinstance(hit, dict) else {}
        entity = hit_obj.get("entity") if isinstance(hit_obj.get("entity"), dict) else hit_obj
        score = hit_obj.get("_rrf_score")
        if score is None:
            score = hit_obj.get("distance")
        if score is None:
            score = hit_obj.get("score")
        metadata = {
            "parent_id": entity.get("parent_id"),
            "parent_content": entity.get("parent_content"),
            "source": entity.get("source"),
            "timestamp": entity.get("timestamp"),
            "page_num": _safe_int(entity.get("page_num")),
            "start_page": _safe_int(entity.get("start_page")),
            "end_page": _safe_int(entity.get("end_page")),
            "chunk_id": entity.get("chunk_id"),
            "parent_index": _safe_int(entity.get("parent_index")),
            "sub_index": _safe_int(entity.get("sub_index")),
        }
        if score is not None:
            try:
                metadata["search_score"] = float(score)
            except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError):
                pass
        return Document(
            page_content=entity.get("text") or "",
            metadata=metadata,
        )
