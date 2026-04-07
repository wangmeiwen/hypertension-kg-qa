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
import torch.cuda
from milvus_model.hybrid import BGEM3EmbeddingFunction
# 导入 Milvus 相关类，用于操作向量数据库
from pymilvus import MilvusClient, DataType
# 导入 Document 类，用于创建文档对象
from langchain_core.documents import Document
# 导入 CrossEncoder，用于重排序和 NLI 判断
from sentence_transformers import CrossEncoder
import uuid
import sys
import os
import numpy as np
from threading import Lock
# 获取当前文件所在目录的绝对路径
current_dir = os.path.dirname(os.path.abspath(__file__))

# 获取core文件所在的目录的绝对路径
rag_qa_path = os.path.dirname(current_dir)
sys.path.insert(0, rag_qa_path)
# 获取根目录文件所在的绝对位置
project_root = os.path.dirname(rag_qa_path)
sys.path.insert(0, project_root)

import logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from app.common.perf_logger import log_duration
from app.common.config import Config

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
        """单版本检索参数：仅使用 search_params。"""
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
             self.logger.warning(f"未找到 Reranker 模型: {reranker_path}")
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
             self.logger.warning(f"Embedding model not found at {m3_path}.")
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
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as e:
            try:
                desc = self.client.describe_collection(self.collection_name)
            except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as e2:
                logger.warning(f"获取集合字段失败: {e2}")
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
            logger.warning(f"提取集合字段信息失败: {e}")

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
                            self.logger.warning(f"无效 metadata.id={user_id}，改用 UUID 生成 ID")
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
        return [doc_map[doc_id] for doc_id, _ in sorted_docs]

    async def hybrid_search_with_rerank(self, query, k=conf.RETRIEVAL_K, source_filter=None):
        """
        执行混合检索并返回重排序后的父文档。

        Args:
            query: 查询文本。
            k: 检索结果数。
            source_filter: 来源过滤条件。

        Returns:
            list[Document]: 重排序后的父文档列表。
        """
        with log_duration("milvus.hybrid_search_with_rerank", k=k, source_filter=source_filter):
            with log_duration("milvus.embed_query"):
                query_embeddings = self.embedding_function([query])

            # 获取查询的稠密向量
            dense_query_vector = _to_float32_vector(query_embeddings["dense"][0], self.dense_dim)
            # 初始化查询的稀疏向量字典
            sparse_query_vector = {}
            # 获取查询稀疏向量的第 0 行数据
            # 兼容新旧版本 scipy: csr_matrix 和 csr_array
            sparse_emb = query_embeddings["sparse"]
            if hasattr(sparse_emb, 'getrow'):
                row = sparse_emb.getrow(0)
            else:
                # 新版本 scipy 使用 csr_array，直接用索引访问
                row = sparse_emb[0]
            # 确保 row 是一维的
            if hasattr(row, 'toarray'):
                row = row.toarray().flatten()
            # 获取稀疏向量的非零值索引
            if hasattr(row, 'indices'):
                indices = row.indices
                values = row.data
            else:
                # 如果是普通数组，找出非零元素
                indices = row.nonzero()[0] if hasattr(row, 'nonzero') else [j for j, v in enumerate(row) if v != 0]
                values = [row[j] for j in indices] if not hasattr(row, 'nonzero') else row[row.nonzero()[0]]
            # 将索引和值配对，填充稀疏向量字典
            for idx, value in zip(indices, values):
                sparse_query_vector[idx] = value
            # 初始化过滤表达式，默认不过滤
            filter_expr = f"source == '{source_filter}'" if source_filter else ""
            dense_field = "dense_vector" if "dense_vector" in self.collection_fields else ("embedding" if "embedding" in self.collection_fields else None)
            sparse_enabled = "sparse_vector" in self.collection_fields
            if dense_field is None:
                raise ValueError(f"集合 {self.collection_name} 缺少可用向量字段")
            dense_metric = "IP" if dense_field == "dense_vector" else "COSINE"

            if not sparse_enabled:
                with log_duration("milvus.search_dense", mode="dense_only", limit=k):
                    results = self._search_with_params(
                        search_kwargs={
                            "collection_name": self.collection_name,
                            "data": [dense_query_vector],
                            "anns_field": dense_field,
                            "limit": k,
                            "output_fields": ["text", "parent_id", "parent_content", "source", "timestamp"],
                            "filter": filter_expr,
                        },
                        search_params={"metric_type": dense_metric, "params": {"nprobe": int(conf.MILVUS_SEARCH_NPROBE)}},
                    )[0]
                sub_chunks = [self._doc_from_hit(hit["entity"]) for hit in results]
                parent_docs = self._get_unique_parent_docs(sub_chunks)
                if len(parent_docs) < 2:
                    return parent_docs[:conf.CANDIDATE_M]
                rerank_cap = max(2, int(conf.CANDIDATE_M))
                rerank_docs = parent_docs[:rerank_cap]
                pairs = [[query, doc.page_content] for doc in rerank_docs]
                with log_duration("milvus.rerank", pairs=len(pairs)):
                    scores = self.reranker.predict(pairs)
                ranked_parent_docs = [doc for _, doc in sorted(zip(scores, rerank_docs), reverse=True)]
                return ranked_parent_docs[:conf.CANDIDATE_M]

            # 使用 RRF 算法：分别执行两次检索，然后融合结果
            logger.info("使用 RRF 算法融合检索结果")

            with log_duration("milvus.search_dense", mode="rrf", limit=k * int(conf.RAG_DENSE_RECALL_MULTIPLIER)):
                dense_results = self._search_with_params(
                    search_kwargs={
                        "collection_name": self.collection_name,
                        "data": [dense_query_vector],
                        "anns_field": dense_field,
                        "limit": k * int(conf.RAG_DENSE_RECALL_MULTIPLIER),
                        "output_fields": ["text", "parent_id", "parent_content", "source", "timestamp"],
                        "filter": filter_expr,
                    },
                    search_params={"metric_type": dense_metric, "params": {"nprobe": int(conf.MILVUS_SEARCH_NPROBE)}},
                )[0]

            with log_duration("milvus.search_sparse", mode="rrf", limit=k * int(conf.RAG_SPARSE_RECALL_MULTIPLIER)):
                sparse_results = self._search_with_params(
                    search_kwargs={
                        "collection_name": self.collection_name,
                        "data": [sparse_query_vector],
                        "anns_field": "sparse_vector",
                        "limit": k * int(conf.RAG_SPARSE_RECALL_MULTIPLIER),
                        "output_fields": ["text", "parent_id", "parent_content", "source", "timestamp"],
                        "filter": filter_expr,
                    },
                    search_params={"metric_type": "IP", "params": {}},
                )[0]

            with log_duration("milvus.rrf_fusion", k=conf.RRF_K):
                fused_entities = self._reciprocal_rank_fusion(dense_results, sparse_results, k=conf.RRF_K)
            # 限制返回数量
            fused_entities = fused_entities[:k]
            # 转换为 Document 对象
            sub_chunks = [self._doc_from_hit(entity) for entity in fused_entities]

            # 从子块中提取去重的父文档
            parent_docs = self._get_unique_parent_docs(sub_chunks)

            # 如果只有1个文档或者没有，直接返回跳过重排序
            if len(parent_docs) < 2:
                return parent_docs[:conf.CANDIDATE_M]

            # 如果有父文档，进行重排序
            if parent_docs:
                # 创建查询与文档内容的配对列表
                rerank_cap = max(2, int(conf.CANDIDATE_M))
                rerank_docs = parent_docs[:rerank_cap]
                pairs = [[query, doc.page_content] for doc in rerank_docs]
                # 使用 BGE-Reranker 计算每个配对的得分
                with log_duration("milvus.rerank", pairs=len(pairs)):
                    try:
                        scores = self.reranker.predict(pairs)
                        # 根据得分从高到低排序文档
                        ranked_parent_docs = [doc for _, doc in sorted(zip(scores, rerank_docs), reverse=True)]
                    except Exception as e:
                        logger.warning(f"重排序失败: {e}")
                        ranked_parent_docs = rerank_docs
            else:
                ranked_parent_docs = []

            # 返回前 m 个重排序后的文档
            return ranked_parent_docs[:conf.CANDIDATE_M]

    def _get_unique_parent_docs(self, sub_chunks):
        """
        从子块结果中提取去重父文档。

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
        return Document(
            page_content=hit.get("text"),
            metadata={
                "parent_id": hit.get("parent_id"),
                "parent_content": hit.get("parent_content"),
                "source": hit.get("source"),
                "timestamp": hit.get("timestamp")
            }
        )




