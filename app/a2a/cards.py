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
    """执行 _base_card 内部逻辑。
    
    Args:
        name: 参数。
        description: 参数。
        url: 参数。
        skills: 参数。
        protocol_version: 参数。
    
    Returns:
        执行结果。
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
    """构建 orchestrator_card。
    
    Args:
        agent_url: 参数。
        protocol_version: 参数。
    
    Returns:
        执行结果。
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
    """构建 kg_subagent_card。
    
    Args:
        agent_url: 参数。
        protocol_version: 参数。
    
    Returns:
        执行结果。
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
    """构建 rag_subagent_card。
    
    Args:
        agent_url: 参数。
        protocol_version: 参数。
    
    Returns:
        执行结果。
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


def build_evaluator_card(
    agent_url: str = "/a2a/evaluator/rpc",
    protocol_version: str = "0.2.5",
) -> dict[str, Any]:
    """构建 evaluator_card。
    
    Args:
        agent_url: 参数。
        protocol_version: 参数。
    
    Returns:
        执行结果。
    """
    return _base_card(
        name="Hypertension Evaluator Agent",
        description="证据门控代理，负责评估证据充分性与风险。",
        url=agent_url,
        protocol_version=protocol_version,
        skills=[
            {
                "id": "medical_evidence_gating",
                "name": "Medical Evidence Gating",
                "description": "对证据进行门控评估，输出是否可终答。",
            }
        ],
    )


def all_agent_cards(protocol_version: str = "0.2.5") -> list[dict[str, Any]]:
    """执行 all_agent_cards。
    
    Args:
        protocol_version: 参数。
    
    Returns:
        执行结果。
    """
    return [
        build_orchestrator_card(protocol_version=protocol_version),
        build_kg_subagent_card(protocol_version=protocol_version),
        build_rag_subagent_card(protocol_version=protocol_version),
        build_evaluator_card(protocol_version=protocol_version),
    ]
