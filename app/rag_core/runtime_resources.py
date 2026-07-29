"""
RAG 运行时共享资源。

集中复用 Milvus 向量存储对象，避免在热路径重复初始化模型与客户端。
缓存作用域为当前 Python 进程，进程退出或测试手动清理前会一直持有实例。
"""

from __future__ import annotations

from threading import Lock
from typing import Dict, Optional

from app.core.config import Config
from app.rag_core.kg_milvus_store import KgMilvusStore
from app.rag_core.vector_store import VectorStore


_VECTOR_LOCK = Lock()
_KG_LOCK = Lock()
_VECTOR_STORES: Dict[str, VectorStore] = {}
_KG_STORES: Dict[str, KgMilvusStore] = {}


def _vector_key(conf: Config) -> str:
    """构建 VectorStore 共享缓存键。

    Args:
        conf: 当前配置对象。

    Returns:
        str: 基于 Milvus 连接与集合参数拼接的唯一键。
    """
    return "|".join(
        [
            str(conf.MILVUS_HOST),
            str(conf.MILVUS_PORT),
            str(conf.MILVUS_DATABASE_NAME),
            str(conf.MILVUS_COLLECTION_NAME),
        ]
    )


def _kg_key(conf: Config) -> str:
    """构建 KgMilvusStore 共享缓存键。

    Args:
        conf: 当前配置对象。

    Returns:
        str: 基于图谱 Milvus 连接与集合参数拼接的唯一键。
    """
    return "|".join(
        [
            str(conf.MILVUS_HOST),
            str(conf.MILVUS_PORT),
            str(conf.MILVUS_DATABASE_NAME),
            str(conf.KG_MILVUS_COLLECTION_NAME),
        ]
    )


def get_shared_vector_store(conf: Optional[Config] = None) -> VectorStore:
    """获取进程内共享的 VectorStore 实例。

    按配置生成缓存键并加锁复用，避免重复初始化模型与 Milvus 客户端。

    Args:
        conf: 可选配置对象；为空时自动创建默认配置。

    Returns:
        VectorStore: 共享向量存储实例。
    """
    active_conf = conf or Config()
    key = _vector_key(active_conf)
    with _VECTOR_LOCK:
        store = _VECTOR_STORES.get(key)
        if store is None:
            store = VectorStore(
                collection_name=active_conf.MILVUS_COLLECTION_NAME,
                host=active_conf.MILVUS_HOST,
                port=active_conf.MILVUS_PORT,
                database=active_conf.MILVUS_DATABASE_NAME,
            )
            _VECTOR_STORES[key] = store
    return store


def get_shared_kg_milvus_store(conf: Optional[Config] = None) -> KgMilvusStore:
    """获取进程内共享的 KgMilvusStore 实例。

    Args:
        conf: 可选配置对象；为空时自动创建默认配置。

    Returns:
        KgMilvusStore: 共享图谱向量存储实例。
    """
    active_conf = conf or Config()
    key = _kg_key(active_conf)
    with _KG_LOCK:
        store = _KG_STORES.get(key)
        if store is None:
            store = KgMilvusStore(
                conf=active_conf,
                collection_name=active_conf.KG_MILVUS_COLLECTION_NAME,
            )
            _KG_STORES[key] = store
    return store
