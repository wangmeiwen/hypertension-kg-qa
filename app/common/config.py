import logging
import os
from dotenv import load_dotenv
from pathlib import Path

"""
配置模块：统一管理项目配置项
- 从环境变量读取配置，支持 .env 文件覆盖
- 配置项包括：大模型、LangSmith、Neo4j、Embedding/Reranker模型、Milvus、OCR、检索参数等
"""

logger = logging.getLogger("config")

# 确定项目根目录并加载 .env 文件
current_file_path = Path(__file__).resolve()
project_root = current_file_path.parent.parent.parent
env_path = project_root / ".env"
load_dotenv(dotenv_path=env_path, override=True)


class Config:
    def __init__(self):
        # 大模型相关
        self.MODEL_API_KEY = os.getenv("MODEL_API_KEY")
        self.MODEL_BASE_URL = os.getenv("MODEL_BASE_URL")
        self.MODEL_NAME = os.getenv("MODEL_NAME")

        # LangSmith 追踪与评估
        self.LANGSMITH_API_KEY = os.getenv("LANGSMITH_API_KEY")
        self.LANGSMITH_TRACING = os.getenv("LANGSMITH_TRACING")
        self.LANGSMITH_PROJECT = os.getenv("LANGSMITH_PROJECT")
        self.LANGSMITH_ENDPOINT = os.getenv("LANGSMITH_ENDPOINT")

        # neo4j相关
        self.NEO4J_URI = os.getenv("NEO4J_URI")
        self.NEO4J_USER = os.getenv("NEO4J_USER")
        self.NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

        # embedding模型
        self.EMBEDDING_MODEL_PATH = os.getenv("EMBEDDING_MODEL_PATH", str(project_root / "model" / "bge-m3"))
        self.RERANKER_MODEL_PATH = os.getenv("RERANKER_MODEL_PATH", str(project_root / "model" / "bge-reranker-large"))


        # Milvus相关
        self.MILVUS_HOST = os.getenv("MILVUS_HOST", "localhost")
        self.MILVUS_PORT = os.getenv("MILVUS_PORT", "19530")
        self.MILVUS_DATABASE_NAME = os.getenv("MILVUS_DATABASE_NAME", "hypertension_kg")
        self.MILVUS_COLLECTION_NAME = os.getenv("MILVUS_COLLECTION_NAME", "hypertension_kg_collection")
        self.KG_MILVUS_COLLECTION_NAME = os.getenv(
            "KG_MILVUS_COLLECTION_NAME",
            "hypertension_kg_graph_collection",
        )

        # ZhipuAI GLM-OCR API Key
        self.ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY", "your-api-key")

        # 检索参数
        self.RETRIEVAL_K = int(os.getenv("RETRIEVAL_K", "5"))
        self.CANDIDATE_M = int(os.getenv("CANDIDATE_M", "10"))
        self.RRF_K = int(os.getenv("RRF_K", "60"))
        self.RAG_SEARCH_TOP_K_DEFAULT = int(os.getenv("RAG_SEARCH_TOP_K_DEFAULT", "5"))
        self.RAG_SEARCH_TOP_K_MAX = int(os.getenv("RAG_SEARCH_TOP_K_MAX", "20"))
        self.MILVUS_SEARCH_NPROBE = int(os.getenv("MILVUS_SEARCH_NPROBE", "10"))
        self.RAG_DENSE_RECALL_MULTIPLIER = int(os.getenv("RAG_DENSE_RECALL_MULTIPLIER", "2"))
        self.RAG_SPARSE_RECALL_MULTIPLIER = int(os.getenv("RAG_SPARSE_RECALL_MULTIPLIER", "2"))

        # 图谱召回参数
        self.KG_RETRIEVAL_TOP_K_DEFAULT = int(os.getenv("KG_RETRIEVAL_TOP_K_DEFAULT", "10"))
        self.KG_RETRIEVAL_TOP_K_MAX = int(os.getenv("KG_RETRIEVAL_TOP_K_MAX", "50"))
        self.KG_RETRIEVAL_LEXICAL_WEIGHT_DEFAULT = float(os.getenv("KG_RETRIEVAL_LEXICAL_WEIGHT_DEFAULT", "0.35"))
        self.KG_RETRIEVAL_EXPANSION_LIMIT_DEFAULT = int(os.getenv("KG_RETRIEVAL_EXPANSION_LIMIT_DEFAULT", "30"))
        self.KG_RETRIEVAL_EXPANSION_LIMIT_MAX = int(os.getenv("KG_RETRIEVAL_EXPANSION_LIMIT_MAX", "100"))
        self.KG_RETRIEVAL_PRIMARY_LIMIT_MIN = int(os.getenv("KG_RETRIEVAL_PRIMARY_LIMIT_MIN", "10"))
        self.KG_RETRIEVAL_PRIMARY_LIMIT_MULTIPLIER = int(os.getenv("KG_RETRIEVAL_PRIMARY_LIMIT_MULTIPLIER", "4"))
        self.KG_RETRIEVAL_KEYWORD_LIMIT_MIN = int(os.getenv("KG_RETRIEVAL_KEYWORD_LIMIT_MIN", "8"))
        self.KG_RETRIEVAL_KEYWORD_LIMIT_MULTIPLIER = int(os.getenv("KG_RETRIEVAL_KEYWORD_LIMIT_MULTIPLIER", "3"))
        self.KG_RETRIEVAL_VECTOR_LIMIT_MAX = int(os.getenv("KG_RETRIEVAL_VECTOR_LIMIT_MAX", "100"))
        self.KG_RETRIEVAL_SEED_ENTITY_TOP_N = int(os.getenv("KG_RETRIEVAL_SEED_ENTITY_TOP_N", "6"))
        self.KG_RETRIEVAL_NEIGHBOR_LIMIT_MIN = int(os.getenv("KG_RETRIEVAL_NEIGHBOR_LIMIT_MIN", "10"))
        self.KG_RETRIEVAL_NEIGHBOR_LIMIT_MAX = int(os.getenv("KG_RETRIEVAL_NEIGHBOR_LIMIT_MAX", "100"))
        self.KG_COMMUNITY_TOP_K_DEFAULT = int(os.getenv("KG_COMMUNITY_TOP_K_DEFAULT", "3"))
        self.KG_COMMUNITY_TOP_K_MAX = int(os.getenv("KG_COMMUNITY_TOP_K_MAX", "10"))
        self.KG_DISAMBIGUATION_MARGIN_DEFAULT = float(os.getenv("KG_DISAMBIGUATION_MARGIN_DEFAULT", "0.12"))
        self.KG_DISAMBIGUATION_LLM_CONFIDENCE_MIN = float(os.getenv("KG_DISAMBIGUATION_LLM_CONFIDENCE_MIN", "0.55"))
        self.KG_FORCE_SUPPLEMENT_TOP_K = int(os.getenv("KG_FORCE_SUPPLEMENT_TOP_K", "12"))

        # 文档分块配置（父子块策略）
        self.PARENT_CHUNK_SIZE = int(os.getenv("PARENT_CHUNK_SIZE", "1200"))
        self.SUB_CHUNK_SIZE = int(os.getenv("SUB_CHUNK_SIZE", "300"))
        self.PARENT_CHUNK_OVERLAP = int(os.getenv("PARENT_CHUNK_OVERLAP", "100"))
        self.SUB_CHUNK_OVERLAP = int(os.getenv("SUB_CHUNK_OVERLAP", "50"))



