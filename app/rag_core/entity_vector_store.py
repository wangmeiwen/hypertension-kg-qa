# -*- coding:utf-8 -*-
"""
实体向量存储模块。

本模块基于 VectorStore 提供实体专用向量存储能力，支持：
- 创建/加载实体集合
- 批量写入实体向量
- 实体向量相似检索

主要类：
- EntityVectorStore: 实体向量存储类

"""

import hashlib

from pymilvus import DataType

from app.rag_core.vector_store import VectorStore, conf


class EntityVectorStore(VectorStore):
    """提供实体粒度向量写入与相似检索能力。

    该存储面向知识图谱实体名称，不保存完整文档片段；
    集合 schema 仅包含实体文本、主键和 dense 向量字段。
    """

    def __init__(self, collection_name="entity_collection", host=conf.MILVUS_HOST, port=conf.MILVUS_PORT, database=conf.MILVUS_DATABASE_NAME):
        """
        初始化实体向量存储。

        Args:
            collection_name: 集合名称。
            host: Milvus 主机地址。
            port: Milvus 端口。
            database: Milvus 数据库名称。

        Returns:
            None。
        """
        super().__init__(collection_name, host, port, database)

    def _create_or_load_collection(self):
        """
        创建或加载实体专用集合。

        Args:
            None。

        Returns:
            None。
        """
        if not self.client.has_collection(self.collection_name):
            schema = self.client.create_schema(auto_id=False, enable_dynamic_field=True)
            schema.add_field(field_name="id", datatype=DataType.VARCHAR, is_primary=True, max_length=100)
            schema.add_field(field_name="text", datatype=DataType.VARCHAR, max_length=1024)
            schema.add_field(field_name="dense_vector", datatype=DataType.FLOAT_VECTOR, dim=self.dense_dim)

            index_params = self.client.prepare_index_params()
            index_params.add_index(
                field_name="dense_vector",
                index_name="dense_index",
                index_type="IVF_FLAT",
                metric_type="IP",
                params={"nlist": 128}
            )

            self.client.create_collection(
                collection_name=self.collection_name,
                schema=schema,
                index_params=index_params
            )
            self.logger.info(f"Created entity collection: {self.collection_name}")
        else:
            self.client.load_collection(self.collection_name)
            self.logger.info(f"Loaded entity collection: {self.collection_name}")

    def add_entities(self, entities):
        """
        批量嵌入实体并写入向量库。

        函数按固定批次调用 embedding_function，并以实体文本的 MD5
        作为主键，适合在构建图谱索引时批量导入实体名称。

        Args:
            entities: 实体名称列表。

        Returns:
            None。
        """
        if not entities:
            return

        data = []
        batch_size = 100
        for i in range(0, len(entities), batch_size):
            batch_entities = entities[i:i+batch_size]
            embeddings = self.embedding_function(batch_entities)["dense"]

            for entity_text, dense_vec in zip(batch_entities, embeddings):
                entity_id = hashlib.md5(entity_text.encode("utf-8")).hexdigest()
                entry = {
                    "id": entity_id,
                    "text": entity_text,
                    "dense_vector": dense_vec
                }
                data.append(entry)

            self.client.insert(collection_name=self.collection_name, data=data)
            self.logger.info(f"Inserted batch of {len(data)} entities into {self.collection_name}")
            data = []

    def search_entities(self, query, top_k=None):
        """
        执行实体向量检索。

        查询时仅使用 dense_vector 做相似度搜索，返回实体文本、
        Milvus 主键和向量距离分数。

        Args:
            query: 查询文本。
            top_k: 返回结果数量。

        Returns:
            list[dict]: 检索结果列表。
        """
        query_dense_vec = self.embedding_function([query])["dense"][0]
        safe_top_k = int(top_k) if top_k is not None else int(conf.RAG_SEARCH_TOP_K_DEFAULT)
        safe_top_k = max(1, min(safe_top_k, int(conf.RAG_SEARCH_TOP_K_MAX)))

        res = self.client.search(
            collection_name=self.collection_name,
            data=[query_dense_vec],
            anns_field="dense_vector",
            param={"metric_type": "IP", "params": {"nprobe": int(conf.MILVUS_SEARCH_NPROBE)}},
            limit=safe_top_k,
            output_fields=["text"]
        )

        results = []
        for hits in res:
            for hit in hits:
                results.append({
                    "text": hit["entity"].get("text"),
                    "score": hit["distance"],
                    "id": hit["id"]
                })
        return results

