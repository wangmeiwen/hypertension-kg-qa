"""项目配置加载模块。

职责说明：
1) 从项目根目录 .env 读取配置并覆盖进程环境变量。
2) 提供 Config 对象，集中暴露模型、数据库、检索与运行参数。
3) 对少量网络地址配置做标准化处理，降低环境差异带来的连接问题。
"""

import logging
import os
from dotenv import load_dotenv
from pathlib import Path


def _normalize_loopback_host(host: str | None) -> str | None:
    """规范化回环主机名。

    当输入为 localhost 时，统一替换为 127.0.0.1，
    避免不同运行环境下对本地回环地址解析不一致。

    Args:
        host: 原始主机名，允许为 None。

    Returns:
        str | None: 规范化后的主机名；若输入为空则返回原值。
    """
    value = str(host or "").strip()
    if not value:
        return host
    if value.lower() == "localhost":
        return "127.0.0.1"
    return value


def _normalize_neo4j_uri(uri: str | None) -> str | None:
    """规范化 Neo4j 连接 URI 中的主机部分。

    对 bolt://localhost 与 neo4j://localhost 场景做统一替换，
    将 localhost 改写为 127.0.0.1，减少连接器在 IPv4/IPv6 上的歧义。

    Args:
        uri: 原始 Neo4j URI，允许为 None。

    Returns:
        str | None: 规范化后的 URI；若输入为空则返回原值。
    """
    value = str(uri or "").strip()
    if not value:
        return uri
    for scheme in ("bolt://", "neo4j://"):
        prefix = f"{scheme}localhost"
        if value.lower().startswith(prefix):
            return f"{scheme}127.0.0.1{value[len(prefix):]}"
    return value

logger = logging.getLogger("config")

# 确定项目根目录并加载 .env 文件
current_file_path = Path(__file__).resolve()
project_root = current_file_path.parent.parent.parent
env_path = project_root / ".env"
load_dotenv(dotenv_path=env_path, override=True)


class Config:
    """集中承载项目运行时配置并提供统一访问入口。"""

    def __init__(self):
        """加载并组织项目运行所需配置项。

        配置来源优先级为：
        1) .env 文件（通过 load_dotenv 注入）
        2) 当前进程环境变量

        本构造函数只负责读取与轻量标准化，不执行外部连接。

        Returns:
            None: 无返回值。
        """
        # 大模型相关
        self.MODEL_API_KEY = os.getenv("MODEL_API_KEY")
        self.MODEL_BASE_URL = os.getenv("MODEL_BASE_URL")
        self.MODEL_NAME = os.getenv("MODEL_NAME")
        self.SESSION_MEMORY_MODEL_NAME = os.getenv("SESSION_MEMORY_MODEL_NAME", self.MODEL_NAME)
        self.GRAPH_LLM_LANE_CONFIGS = self._build_graph_llm_lane_configs()
        self.GRAPH_LLM_PARALLELISM = len(self.GRAPH_LLM_LANE_CONFIGS)

        # LangSmith 追踪与评估
        self.LANGSMITH_API_KEY = os.getenv("LANGSMITH_API_KEY")
        self.LANGSMITH_TRACING = os.getenv("LANGSMITH_TRACING")
        self.LANGSMITH_PROJECT = os.getenv("LANGSMITH_PROJECT")
        self.LANGSMITH_ENDPOINT = os.getenv("LANGSMITH_ENDPOINT")

        # neo4j相关
        self.NEO4J_URI = _normalize_neo4j_uri(os.getenv("NEO4J_URI"))
        self.NEO4J_USER = os.getenv("NEO4J_USER")
        self.NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

        # embedding模型
        self.EMBEDDING_MODEL_PATH = os.getenv("EMBEDDING_MODEL_PATH", str(project_root / "model" / "bge-m3"))
        self.RERANKER_MODEL_PATH = os.getenv("RERANKER_MODEL_PATH", str(project_root / "model" / "bge-reranker-large"))


        # Milvus相关
        self.MILVUS_HOST = _normalize_loopback_host(os.getenv("MILVUS_HOST", "127.0.0.1"))
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
        self.RETRIEVAL_K = int(os.getenv("RETRIEVAL_K", "4"))
        self.CANDIDATE_M = int(os.getenv("CANDIDATE_M", "6"))
        self.RRF_K = int(os.getenv("RRF_K", "60"))
        self.RAG_SEARCH_TOP_K_DEFAULT = int(os.getenv("RAG_SEARCH_TOP_K_DEFAULT", "4"))
        self.RAG_SEARCH_TOP_K_MAX = int(os.getenv("RAG_SEARCH_TOP_K_MAX", "8"))
        self.MILVUS_SEARCH_NPROBE = int(os.getenv("MILVUS_SEARCH_NPROBE", "10"))
        self.RAG_DENSE_RECALL_MULTIPLIER = int(os.getenv("RAG_DENSE_RECALL_MULTIPLIER", "1"))
        self.RAG_SPARSE_RECALL_MULTIPLIER = int(os.getenv("RAG_SPARSE_RECALL_MULTIPLIER", "1"))

        # 图谱召回参数
        self.KG_RETRIEVAL_TOP_K_DEFAULT = int(os.getenv("KG_RETRIEVAL_TOP_K_DEFAULT", "8"))
        self.KG_RETRIEVAL_TOP_K_MAX = int(os.getenv("KG_RETRIEVAL_TOP_K_MAX", "20"))
        self.KG_RETRIEVAL_LEXICAL_WEIGHT_DEFAULT = float(os.getenv("KG_RETRIEVAL_LEXICAL_WEIGHT_DEFAULT", "0.35"))
        self.KG_RETRIEVAL_EXPANSION_LIMIT_DEFAULT = int(os.getenv("KG_RETRIEVAL_EXPANSION_LIMIT_DEFAULT", "20"))
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

        # A2A 主控与子代理执行上限（单轨重构统一入口）
        self.A2A_ORCHESTRATOR_MAX_TURNS_DEFAULT = int(
            os.getenv("A2A_ORCHESTRATOR_MAX_TURNS_DEFAULT", "2")
        )
        self.A2A_ORCHESTRATOR_MAX_TURNS_MAX = int(
            os.getenv("A2A_ORCHESTRATOR_MAX_TURNS_MAX", "6")
        )
        self.A2A_ORCHESTRATOR_SUPPLEMENT_MAX_ROUNDS = int(
            os.getenv("A2A_ORCHESTRATOR_SUPPLEMENT_MAX_ROUNDS", "2")
        )
        self.A2A_SUBAGENT_MAX_TOOL_CALLS_DEFAULT = int(
            os.getenv("A2A_SUBAGENT_MAX_TOOL_CALLS_DEFAULT", "2")
        )
        self.A2A_SUBAGENT_MAX_TOOL_CALLS_MAX = int(
            os.getenv("A2A_SUBAGENT_MAX_TOOL_CALLS_MAX", "8")
        )

        # 文档分块配置（父子块策略）
        self.PARENT_CHUNK_SIZE = int(os.getenv("PARENT_CHUNK_SIZE", "1200"))
        self.SUB_CHUNK_SIZE = int(os.getenv("SUB_CHUNK_SIZE", "300"))
        self.PARENT_CHUNK_OVERLAP = int(os.getenv("PARENT_CHUNK_OVERLAP", "100"))
        self.SUB_CHUNK_OVERLAP = int(os.getenv("SUB_CHUNK_OVERLAP", "50"))

    def _build_graph_llm_lane_configs(self) -> list[dict[str, str | int]]:
        """构建图谱抽取专用的多路模型配置。"""
        lanes: list[dict[str, str | int]] = []
        for lane_id in range(1, 4):
            api_key = os.getenv(f"GRAPH_LLM_{lane_id}_API_KEY")
            base_url = os.getenv(f"GRAPH_LLM_{lane_id}_BASE_URL")
            model_name = os.getenv(f"GRAPH_LLM_{lane_id}_MODEL_NAME")
            source = f"GRAPH_LLM_{lane_id}_*"
            if lane_id == 1 and not (api_key and base_url and model_name):
                api_key = api_key or self.MODEL_API_KEY
                base_url = base_url or self.MODEL_BASE_URL
                model_name = model_name or self.MODEL_NAME
                source = "MODEL_* fallback"
            if not (api_key and base_url and model_name):
                continue
            lanes.append(
                {
                    "lane_id": lane_id,
                    "api_key": str(api_key),
                    "base_url": str(base_url),
                    "model_name": str(model_name),
                    "source": source,
                }
            )
        return lanes


