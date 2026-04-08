"""
MCP 工具装配与运行时探测模块。

本模块负责构建本地 MCP 工具注册表，并提供以下能力：
- 组装知识图谱查询、向量检索、实体关系检索三类核心工具
- 在依赖不可用时自动降级为可用占位 handler，避免服务初始化中断
- 提供全局 MCPServer 单例访问入口
- 提供工具运行时健康探测与观测文本裁剪工具

主要结构：
- KnowledgeGraphTool: Neo4j 读查询工具封装
- RagSearchTool: 通用向量检索工具封装
- KgEntityRelationSearchTool: 图谱实体关系检索与消歧封装
- build_mcp_server: MCPServer 装配入口

"""

import os
import re
import sys
import json
import logging
from pathlib import Path
from typing import Any, Optional

from app.common.config import Config
from app.common.perf_logger import log_duration
from app.a2a.agent.llm_client import LLMClient
from app.backend.services.session_memory_service import SessionMemoryService
from app.knowledge_graph.neo4j_manager import neo4j_client
from app.mcp.server import MCPServer, MCPTool
from app.mcp.tool_manager import ToolManager
from app.mcp.kg_prompts import ENTITY_DISAMBIGUATION_PROMPT
from app.rag_core.runtime_resources import (
    get_shared_kg_milvus_store,
    get_shared_vector_store,
)


current_dir = Path(__file__).resolve().parent
src_dir = current_dir.parent
project_root = src_dir.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))


logger = logging.getLogger(__name__)


def _to_bool(value: Any, default: bool = False) -> bool:
    """将输入值解析为布尔值。

    支持 bool、None 以及常见字符串表示：
    1/true/yes/on -> True
    0/false/no/off -> False
    其他值回退到 default。

    Args:
        value: 待解析值。
        default: 无法解析时的默认值。

    Returns:
        bool: 解析后的布尔值。
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


class KnowledgeGraphTool:
    """知识图谱只读查询工具。

    基于 Neo4j 提供三种查询模式：
    - 只读 Cypher 查询
    - 全量节点浏览（受 limit 限制）
    - 关键词匹配查询（含弱匹配回退）
    """

    def __init__(self, conf: Config):
        """初始化图谱查询工具。

        Args:
            conf: 系统配置对象。
        """
        self.conf = conf
        self.client = neo4j_client
        self.forbidden = [
            "create",
            "merge",
            "delete",
            "detach",
            "drop",
            "set",
            "remove",
            "call",
            "apoc",
            "load csv",
        ]

    def _split_keyword_terms(self, keyword: str) -> list[str]:
        """按中英文标点切分关键词并去重。

        Args:
            keyword: 原始关键词文本。

        Returns:
            list[str]: 去重后的关键词数组。
        """
        cleaned = re.sub(r"[，。！？；：、,!?;:/\\|]+", " ", keyword)
        raw_terms = [item.strip() for item in cleaned.split(" ") if item.strip()]
        if not raw_terms:
            return []

        terms: list[str] = []
        seen = set()
        for item in raw_terms:
            if item not in seen:
                terms.append(item)
                seen.add(item)
        return terms

    async def query(self, payload: dict[str, Any]) -> dict[str, Any]:
        """执行知识图谱查询。

        输入支持以下字段：
        - cypher: 只读查询语句
        - keyword/question: 关键词检索文本
        - list_all_nodes: 是否浏览全部节点
        - limit: 返回条数上限

        Returns:
            dict[str, Any]: 标准查询结果。失败时返回 unavailable 结构。
        """
        cypher = (payload.get("cypher") or "").strip()
        keyword = (payload.get("keyword") or payload.get("question") or "").strip()
        list_all_nodes = bool(payload.get("list_all_nodes"))
        limit = int(payload.get("limit") or 10)
        limit = max(1, min(limit, 20))
        try:
            if cypher:
                lower = cypher.lower()
                if any(token in lower for token in self.forbidden):
                    return {"records": [], "blocked": True, "reason": "unsafe_cypher"}
                if not lower.startswith("match") and not lower.startswith("with"):
                    return {"records": [], "blocked": True, "reason": "read_only_required"}
                records = (await self.client.run_cypher(cypher))[:limit]
                return {"records": records, "blocked": False}

            if list_all_nodes:
                records = await self.client.run_cypher(
                    """
                    MATCH (n)
                    RETURN labels(n) AS labels, properties(n) AS properties
                    LIMIT $limit
                    """,
                    {"limit": limit},
                )
                return {"records": records, "blocked": False, "mode": "all_nodes"}

            if not keyword:
                return {"records": [], "blocked": False}

            tokens = self._split_keyword_terms(keyword)
            if not tokens:
                return {"records": [], "blocked": False}

            records = await self.client.run_cypher(
                """
                MATCH (n)
                WITH n, $tokens AS tokens
                WITH n, tokens,
                     reduce(
                         matched = 0,
                         token IN tokens |
                             matched + CASE
                                 WHEN toString(coalesce(n.name, '')) CONTAINS token
                                      OR any(k IN keys(n) WHERE toString(n[k]) CONTAINS token)
                                 THEN 1
                                 ELSE 0
                             END
                     ) AS matched
                WHERE matched > 0
                RETURN labels(n) AS labels, properties(n) AS properties,
                       matched AS matched_tokens, size(tokens) AS total_tokens
                ORDER BY matched_tokens DESC
                LIMIT $limit
                """,
                {"tokens": tokens, "limit": limit},
            )

            if not records:
                fallback_records = await self.client.run_cypher(
                    """
                    MATCH (n)
                    RETURN labels(n) AS labels, properties(n) AS properties
                    LIMIT $limit
                    """,
                    {"limit": limit},
                )
                return {
                    "records": fallback_records,
                    "blocked": False,
                    "mode": "keyword_fallback_all_nodes",
                }
            return {"records": records, "blocked": False}
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            logger.exception(
                "kg_query_failed: dependency=neo4j error_type=%s error=%s",
                type(exc).__name__,
                exc,
            )
            return {
                "records": [],
                "blocked": False,
                "unavailable": True,
                "dependency": "neo4j",
                "reason": f"neo4j_unavailable:{exc}",
            }


class RagSearchTool:
    """RAG 向量检索工具。

    基于共享向量库执行混合检索与重排，输出统一 hits 结构。
    """

    def __init__(self, conf: Config):
        """初始化 RAG 检索工具。

        Args:
            conf: 系统配置对象。
        """
        self.conf = conf
        self.vector_store = get_shared_vector_store(conf)

    async def query(self, payload: dict[str, Any]) -> dict[str, Any]:
        """执行向量检索并返回片段命中结果。

        Args:
            payload: 调用参数，支持 query/question、top_k、source_filter。

        Returns:
            dict[str, Any]: 包含 hits 数组的结果对象。
        """
        query = (payload.get("query") or payload.get("question") or "").strip()
        top_k = int(payload.get("top_k") or self.conf.RAG_SEARCH_TOP_K_DEFAULT)
        top_k = max(1, min(top_k, int(self.conf.RAG_SEARCH_TOP_K_MAX)))
        source_filter = payload.get("source_filter")
        source_filter = str(source_filter).strip() if source_filter is not None else None
        if source_filter == "":
            source_filter = None
        if not query:
            return {"hits": []}
        try:
            docs = await self.vector_store.hybrid_search_with_rerank(
                query=query,
                k=top_k,
                source_filter=source_filter,
            )

            hits = []
            total = len(docs)
            for idx, doc in enumerate(docs):
                rank_score = (total - idx) / total if total > 0 else 0.0
                metadata = doc.metadata if isinstance(doc.metadata, dict) else {}
                hits.append(
                    {
                        "id": metadata.get("parent_id") or idx + 1,
                        "score": round(rank_score, 6),
                        "text": doc.page_content,
                        "source": metadata.get("source"),
                        "timestamp": metadata.get("timestamp"),
                        "parent_content": metadata.get("parent_content", doc.page_content),
                    }
                )
            return {"hits": hits}
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            logger.exception(
                "rag_search_failed: dependency=milvus error_type=%s error=%s",
                type(exc).__name__,
                exc,
            )
            return {
                "hits": [],
                "unavailable": True,
                "dependency": "milvus",
                "reason": f"milvus_unavailable:{exc}",
            }


class KgEntityRelationSearchTool:
    """图谱实体关系检索工具。

    基于知识图谱专用 Milvus 集合检索实体与关系，
    并可选使用 LLM 对高相近候选做消歧。
    """

    def __init__(self, conf: Config):
        """初始化实体关系检索工具。

        Args:
            conf: 系统配置对象。
        """
        self.conf = conf
        self.store = get_shared_kg_milvus_store(conf)
        self._llm: Optional[LLMClient] = None
        try:
            self._llm = LLMClient(conf)
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            logger.warning(
                "kg_disambiguation_llm_init_failed: error_type=%s error=%s",
                type(exc).__name__,
                exc,
                exc_info=True,
            )
            self._llm = None

    def _resolve_disambiguation_with_llm(self, query: str, candidates: list[dict[str, Any]], margin: float) -> dict[str, Any]:
        """调用 LLM 对候选实体进行语义消歧。

        Args:
            query: 用户检索问题。
            candidates: 候选实体列表。
            margin: 消歧边界阈值。

        Returns:
            dict[str, Any]: 包含 canonical_name、confidence、reason、
                llm_calls 与 token_usage。
            若未启用 LLM，返回空字典。
        """
        if self._llm is None:
            return {}
        if not candidates:
            return {}

        prompt = ENTITY_DISAMBIGUATION_PROMPT
        messages = [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": (
                    f"问题: {str(query or '').strip()}\n"
                    f"margin: {margin}\n"
                    f"候选实体: {json.dumps(candidates, ensure_ascii=False)}"
                ),
            },
        ]

        try:
            response = self._llm.chat_with_tokens(messages, temperature=0.1)
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            logger.warning(
                "entity_disambiguation_call_failed: error_type=%s error=%s",
                type(exc).__name__,
                exc,
                exc_info=True,
            )
            return {}

        token_usage = {
            "prompt_tokens": int(response.prompt_tokens or 0),
            "completion_tokens": int(response.completion_tokens or 0),
            "total_tokens": int(response.total_tokens or 0),
        }

        try:
            text = str(response.content or "").strip()
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                text = text[start : end + 1]
            parsed = json.loads(text)
            if not isinstance(parsed, dict):
                return {
                    "canonical_name": "",
                    "confidence": 0.0,
                    "reason": "invalid_json_object",
                    "llm_calls": 1,
                    "token_usage": token_usage,
                }
            canonical_name = str(parsed.get("canonical_name") or "").strip()
            try:
                confidence = float(parsed.get("confidence") or 0.0)
            except (TypeError, ValueError) as exc:
                logger.debug(
                    "entity_disambiguation_confidence_parse_failed: raw=%s error_type=%s error=%s",
                    parsed.get("confidence"),
                    type(exc).__name__,
                    exc,
                )
                confidence = 0.0
            reason = str(parsed.get("reason") or "").strip()
            return {
                "canonical_name": canonical_name,
                "confidence": max(0.0, min(confidence, 1.0)),
                "reason": reason,
                "llm_calls": 1,
                "token_usage": token_usage,
            }
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            logger.warning(
                "entity_disambiguation_parse_failed: error_type=%s error=%s",
                type(exc).__name__,
                exc,
                exc_info=True,
            )
            return {
                "canonical_name": "",
                "confidence": 0.0,
                "reason": "parse_failed",
                "llm_calls": 1,
                "token_usage": token_usage,
            }

    async def query(self, payload: dict[str, Any]) -> dict[str, Any]:
        """执行实体关系检索。

        Args:
            payload: 调用参数，支持 query、top_k、词法融合权重、
                消歧与社区检测开关等扩展项。

        Returns:
            dict[str, Any]: 结构化实体关系检索结果。
        """
        query = (payload.get("query") or payload.get("keyword") or "").strip()
        top_k = int(payload.get("top_k") or payload.get("limit") or self.conf.KG_RETRIEVAL_TOP_K_DEFAULT)
        top_k = max(1, min(top_k, int(self.conf.KG_RETRIEVAL_TOP_K_MAX)))
        lexical_weight = float(payload.get("lexical_weight") or self.conf.KG_RETRIEVAL_LEXICAL_WEIGHT_DEFAULT)
        expansion_limit = int(payload.get("expansion_limit") or self.conf.KG_RETRIEVAL_EXPANSION_LIMIT_DEFAULT)
        enable_disambiguation = _to_bool(payload.get("enable_disambiguation"), default=True)
        enable_community_detection = _to_bool(payload.get("enable_community_detection"), default=True)
        community_top_k = int(payload.get("community_top_k") or self.conf.KG_COMMUNITY_TOP_K_DEFAULT)
        disambiguation_margin = float(payload.get("disambiguation_margin") or self.conf.KG_DISAMBIGUATION_MARGIN_DEFAULT)

        if not query:
            return {"entities": [], "relationships": [], "collection_name": self.store.collection_name}
        try:
            return await self.store.search_entity_relations(
                query=query,
                top_k=top_k,
                lexical_weight=lexical_weight,
                expansion_limit=expansion_limit,
                enable_disambiguation=enable_disambiguation,
                enable_community_detection=enable_community_detection,
                community_top_k=community_top_k,
                disambiguation_margin=disambiguation_margin,
                disambiguation_resolver=self._resolve_disambiguation_with_llm,
            )
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            logger.exception(
                "kg_entity_relation_search_failed: dependency=milvus error_type=%s error=%s",
                type(exc).__name__,
                exc,
            )
            return {
                "entities": [],
                "relationships": [],
                "collection_name": self.store.collection_name,
                "unavailable": True,
                "dependency": "milvus",
                "reason": f"kg_milvus_unavailable:{exc}",
            }


class HistoryReadTool:
    """读取会话历史检索记录。"""

    def __init__(self) -> None:
        """初始化历史检索读取工具并创建记忆服务实例。"""
        self._memory = SessionMemoryService()

    async def query(self, payload: dict[str, Any]) -> dict[str, Any]:
        """按 history_id 或用户问题读取会话内历史检索记录。"""
        session_id = str(payload.get("session_id") or "").strip()
        history_id = str(payload.get("history_id") or "").strip()
        user_question = str(payload.get("user_question") or "").strip()
        if not session_id:
            return {"matched": False, "records": [], "error": "session_id_required"}
        if not history_id and not user_question:
            return {"matched": False, "records": [], "error": "history_id_or_user_question_required"}
        return await self._memory.read_retrieval_history(
            session_id=session_id,
            history_id=history_id,
            user_question=user_question,
        )


# 全局 MCP 服务器实例（单例模式）
_mcp_server_instance: Optional[MCPServer] = None

def get_mcp_server() -> MCPServer:
    """获取全局 MCPServer 单例。

    首次调用时自动构建实例，后续直接复用。

    Returns:
        MCPServer: 全局 MCP 服务实例。
    """
    global _mcp_server_instance
    if _mcp_server_instance is None:
        _mcp_server_instance = build_mcp_server()
    return _mcp_server_instance

def build_mcp_server() -> MCPServer:
    """构建并注册 MCP 工具服务。

    构建流程：
    1) 初始化核心内置工具注册表
    2) 按需初始化 Milvus 相关工具并提供降级 handler
    3) 通过 ToolManager 读取 YAML 扩展工具配置
    4) 在配置异常时兜底保留核心图谱工具

    Returns:
        MCPServer: 已完成工具注册的 MCPServer 实例。
    """
    conf = Config()
    server = MCPServer()
    kg_tool = KnowledgeGraphTool(conf)

    async def kg_handler(payload: dict[str, Any]) -> dict[str, Any]:
        """处理 `kg_query` 工具调用请求。

        Args:
            payload: 工具输入参数字典。

        Returns:
            dict[str, Any]: 图谱查询结果。
        """
        return await kg_tool.query(payload)

    # 先构建内置工具目录，再由 ToolManager 按 YAML 动态注册。
    builtin_registry: dict[str, dict[str, Any]] = {
        "kg_query": {
            "description": "查询高血压知识图谱，支持 keyword、list_all_nodes 或只读 cypher",
            "input_schema": {
                "keyword": "str?",
                "list_all_nodes": "bool?",
                "cypher": "str?",
                "limit": "int?",
            },
            "handler": kg_handler,
        }
    }

    try:
        rag_tool = RagSearchTool(conf)

        async def rag_handler(payload: dict[str, Any]) -> dict[str, Any]:
            """处理 `rag_search` 工具请求（正常路径）。

            Args:
                payload: 工具输入参数字典。

            Returns:
                dict[str, Any]: 向量检索返回结果。
            """
            return await rag_tool.query(payload)

    except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
        message = str(exc)
        logger.exception(
            "build_mcp_server_rag_tool_failed_fallback: error_type=%s error=%s",
            type(exc).__name__,
            exc,
        )

        async def rag_handler(payload: dict[str, Any]) -> dict[str, Any]:
            """处理 `rag_search` 工具请求（降级路径）。

            Args:
                payload: 工具输入参数字典（降级场景下不使用）。

            Returns:
                dict[str, Any]: 标记 unavailable 的空结果。
            """
            return {"hits": [], "unavailable": True, "reason": message}

    builtin_registry["rag_search"] = {
        "description": "基于Milvus进行向量检索，返回相关片段",
        "input_schema": {"query": "str", "top_k": "int?"},
        "handler": rag_handler,
    }

    try:
        kg_er_tool = KgEntityRelationSearchTool(conf)

        preload_embedder = _to_bool(
            os.getenv("KG_ENTITY_RELATION_PRELOAD_EMBEDDER", "1"),
            default=True,
        )
        if preload_embedder:
            try:
                with log_duration("kg_milvus.preload_embedder"):
                    kg_er_tool.store.ensure_embedder_loaded()
                logger.info("kg_entity_relation_search 预热完成")
            except (
                RuntimeError,
                ValueError,
                TypeError,
                KeyError,
                AttributeError,
                OSError,
                ImportError,
            ) as exc:
                logger.warning(
                    "kg_entity_relation_search_prewarm_failed: error_type=%s error=%s",
                    type(exc).__name__,
                    exc,
                    exc_info=True,
                )

        async def kg_er_handler(payload: dict[str, Any]) -> dict[str, Any]:
            """处理 `kg_entity_relation_search` 请求（正常路径）。

            Args:
                payload: 工具输入参数字典。

            Returns:
                dict[str, Any]: 实体关系检索结果。
            """
            return await kg_er_tool.query(payload)

    except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
        message = str(exc)
        logger.exception(
            "build_mcp_server_kg_er_tool_failed_fallback: error_type=%s error=%s",
            type(exc).__name__,
            exc,
        )

        async def kg_er_handler(payload: dict[str, Any]) -> dict[str, Any]:
            """处理 `kg_entity_relation_search` 请求（降级路径）。

            Args:
                payload: 工具输入参数字典（降级场景下不使用）。

            Returns:
                dict[str, Any]: 标记 unavailable 的空实体/关系结果。
            """
            return {
                "entities": [],
                "relationships": [],
                "unavailable": True,
                "reason": message,
            }

    builtin_registry["kg_entity_relation_search"] = {
        "description": "基于知识图谱专用Milvus集合检索并返回实体与关系",
        "input_schema": {
            "query": "str",
            "top_k": "int?",
            "lexical_weight": "float?",
            "expansion_limit": "int?",
            "enable_disambiguation": "bool?",
            "enable_community_detection": "bool?",
            "community_top_k": "int?",
            "disambiguation_margin": "float?",
        },
        "handler": kg_er_handler,
    }

    history_read_tool = HistoryReadTool()

    async def history_read_handler(payload: dict[str, Any]) -> dict[str, Any]:
        """处理 `history_read` 请求，返回指定历史问题下的检索记录。"""
        return await history_read_tool.query(payload)

    builtin_registry["history_read"] = {
        "description": "读取当前会话中某个历史检索问题对应的 query 与结果内容",
        "input_schema": {
            "session_id": "str",
            "history_id": "str?",
            "user_question": "str?",
        },
        "handler": history_read_handler,
    }

    config_path = Path(__file__).resolve().with_name("mcp_tools.yaml")
    manager = ToolManager(config_path=config_path)

    extra_blacklist_raw = os.getenv("MCP_TOOL_BLACKLIST", "")
    extra_blacklist = [item.strip() for item in extra_blacklist_raw.split(",") if item.strip()]
    manager.register_tools(
        server=server,
        builtin_registry=builtin_registry,
        extra_blacklist=extra_blacklist,
    )

    # 兜底保护：配置错误时至少保留核心知识图谱工具。
    if not server.list_tools():
        server.register(
            MCPTool(
                name="kg_query",
                server_name="local",
                tool_name="kg_query",
                description="查询高血压知识图谱，支持 keyword 或只读 cypher",
                input_schema={
                    "keyword": "str?",
                    "list_all_nodes": "bool?",
                    "cypher": "str?",
                    "limit": "int?",
                },
                handler=kg_handler,
            )
        )

    return server


async def get_mcp_runtime_status(server: Optional[MCPServer] = None) -> dict[str, Any]:
    """检查 MCP 运行时状态，用于 readiness 判定。

    该函数同时校验：
    - 必需工具是否已注册
    - 可选工具是否缺失
    - 工具探测调用是否出现 runtime unavailable

    Args:
        server: 可选 MCPServer，未传入时使用全局单例。

    Returns:
        dict[str, Any]: 运行时状态快照，包含核心就绪标记和异常详情。
    """
    target_server = server
    if target_server is None:
        target_server = get_mcp_server()

    tools = target_server.list_tools()
    tool_names = {
        str(item.get("name") or "")
        for item in tools
        if isinstance(item, dict)
    }

    required_tools = ["kg_query", "rag_search", "history_read"]
    optional_tools = ["kg_entity_relation_search"]

    missing_required = [name for name in required_tools if name not in tool_names]
    missing_optional = [name for name in optional_tools if name not in tool_names]

    runtime_unavailable_required: list[str] = []
    runtime_unavailable_optional: list[str] = []
    runtime_errors: dict[str, str] = {}

    probe_payloads: dict[str, dict[str, Any]] = {
        "kg_query": {"keyword": "", "limit": 1},
        "rag_search": {"query": "", "top_k": 1},
        "history_read": {"session_id": "probe", "history_id": "missing"},
        "kg_entity_relation_search": {"query": "", "top_k": 1},
    }

    for tool_name, payload in probe_payloads.items():
        if tool_name not in tool_names:
            continue
        try:
            result = await target_server.ainvoke(tool_name, payload)
            unavailable = False
            if isinstance(result, dict):
                if bool(result.get("unavailable")):
                    unavailable = True
                payload_obj = result.get("result")
                payload_data = payload_obj if isinstance(payload_obj, dict) else {}
                if bool(payload_data.get("unavailable")):
                    unavailable = True
                if not bool(result.get("ok", True)):
                    unavailable = True
            if unavailable:
                if tool_name in required_tools:
                    runtime_unavailable_required.append(tool_name)
                else:
                    runtime_unavailable_optional.append(tool_name)
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            runtime_errors[tool_name] = str(exc)
            logger.warning(
                "mcp_runtime_probe_failed: tool=%s error_type=%s error=%s",
                tool_name,
                type(exc).__name__,
                exc,
                exc_info=True,
            )
            if tool_name in required_tools:
                runtime_unavailable_required.append(tool_name)
            else:
                runtime_unavailable_optional.append(tool_name)

    core_ready = not missing_required and not runtime_unavailable_required
    optional_issues = missing_optional + runtime_unavailable_optional

    return {
        "core_ready": core_ready,
        "missing_required": missing_required,
        "runtime_unavailable_required": runtime_unavailable_required,
        "missing_optional": missing_optional,
        "runtime_unavailable_optional": runtime_unavailable_optional,
        "runtime_errors": runtime_errors,
        "tool_count": len(tool_names),
        "tool_names": sorted(tool_names),
        "optional_issues": optional_issues,
    }


def sanitize_tool_observation(observation: dict[str, Any], max_chars: int = 4000) -> str:
    """将工具观测对象裁剪为紧凑可日志化文本。

    Args:
        observation: 原始观测对象。
        max_chars: 最大保留字符数。

    Returns:
        str: 归一化后的文本，超长时附加截断标记。
    """
    text = str(observation)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_chars:
        return text[:max_chars] + "...[truncated]"
    return text
