"""A2A Agent Card 构建。"""

from __future__ import annotations

from typing import Any


def _base_card(
    *,
    name: str,
    description: str,
    url: str,
    skills: list[dict[str, Any]],
    protocol_version: str = "0.2.5",
) -> dict[str, Any]:
    """生成符合 A2A Agent Card 协议的基础描述结构。

    Args:
        name: 对外暴露的 Agent 名称。
        description: Agent 能力和职责的简短说明。
        url: Agent RPC 服务入口地址。
        skills: Agent Card 中声明的技能列表。
        protocol_version: A2A 协议版本号。

    Returns:
        可直接序列化为 JSON 的 Agent Card 字典。
    """
    return {
        "protocolVersion": protocol_version,
        "name": name,
        "description": description,
        "url": url,
        "capabilities": {
            "streaming": True,
            "pushNotifications": False,
        },
        "defaultInputModes": ["application/json", "text/plain"],
        "defaultOutputModes": ["application/json"],
        "skills": skills,
    }


def build_orchestrator_card(
    agent_url: str = "/a2a/rpc",
    protocol_version: str = "0.2.5",
) -> dict[str, Any]:
    """构建主编排 Agent 的 Agent Card。

    Args:
        agent_url: 主代理 A2A RPC 入口路径。
        protocol_version: A2A 协议版本号。

    Returns:
        描述高血压问答编排能力的 Agent Card。
    """
    return _base_card(
        name="Hypertension Orchestrator Agent",
        description="高血压问答主代理，负责问题分解、重构、补检索决策与终答。",
        url=agent_url,
        protocol_version=protocol_version,
        skills=[
            {
                "id": "medical_qa_orchestration",
                "name": "Medical QA Orchestration",
                "description": "主代理编排，串行调度子代理并融合证据。",
            },
            {
                "id": "supplement_decision",
                "name": "Supplement Decision",
                "description": "根据证据充分性决定是否补检索。",
            },
        ],
    )


def build_kg_subagent_card(
    agent_url: str = "/a2a/graph/rpc",
    protocol_version: str = "0.2.5",
) -> dict[str, Any]:
    """构建知识图谱子代理的 Agent Card。

    Args:
        agent_url: 图谱子代理 A2A RPC 入口路径。
        protocol_version: A2A 协议版本号。

    Returns:
        描述图谱检索能力的 Agent Card。
    """
    return _base_card(
        name="Hypertension Graph Agent",
        description="图谱子代理，负责知识图谱检索语句生成与证据提取。",
        url=agent_url,
        protocol_version=protocol_version,
        skills=[
            {
                "id": "hypertension_graph_retrieval",
                "name": "Hypertension Graph Retrieval",
                "description": "调用 kg_query 与 kg_entity_relation_search 获取图谱证据。",
            }
        ],
    )


def build_rag_subagent_card(
    agent_url: str = "/a2a/retrieval/rpc",
    protocol_version: str = "0.2.5",
) -> dict[str, Any]:
    """构建语义检索子代理的 Agent Card。

    Args:
        agent_url: RAG 子代理 A2A RPC 入口路径。
        protocol_version: A2A 协议版本号。

    Returns:
        描述向量检索和文本证据提取能力的 Agent Card。
    """
    return _base_card(
        name="Hypertension Retrieval Agent",
        description="检索子代理，负责向量检索语句生成与文本证据提取。",
        url=agent_url,
        protocol_version=protocol_version,
        skills=[
            {
                "id": "hypertension_semantic_retrieval",
                "name": "Hypertension Semantic Retrieval",
                "description": "调用 rag_search 获取语义相关文本证据。",
            }
        ],
    )


def all_agent_cards(protocol_version: str = "0.2.5") -> list[dict[str, Any]]:
    """汇总系统内所有对外声明的 A2A Agent Card。

    Args:
        protocol_version: 统一写入各 Agent Card 的 A2A 协议版本号。

    Returns:
        主代理、图谱子代理和 RAG 子代理的 Card 列表。
    """
    return [
        build_orchestrator_card(protocol_version=protocol_version),
        build_kg_subagent_card(protocol_version=protocol_version),
        build_rag_subagent_card(protocol_version=protocol_version),
    ]
