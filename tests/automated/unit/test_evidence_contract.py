"""跨检索链路 Evidence 契约测试。"""

import asyncio
from types import SimpleNamespace

from langchain_core.documents import Document
from pydantic import ValidationError
import pytest

from app.a2a.evidence import build_kg_evidence, build_rag_evidence
from app.a2a.evidence_fusion import evidence_identity
from app.a2a.medical_risk import MedicalRiskAssessment
from app.a2a.models import ClaimEvidenceAssessment, Evidence, SubAgentResult
from app.a2a.orchestrator.controller import UnifiedOrchestratorController
from app.a2a.question_planner import build_question_plan
from app.a2a.subagents.kg.nodes import node_normalize_results as normalize_kg_results
from app.a2a.subagents.rag.nodes import (
    node_normalize_results as normalize_rag_results,
)
from app.mcp.tools import (
    KgEntityRelationSearchTool,
    KnowledgeGraphTool,
    RagSearchTool,
)
from app.rag_core.vector_store import VectorStore

EVIDENCE_FIELDS = {
    "schema_version",
    "source_type",
    "source_id",
    "document_id",
    "chunk_id",
    "page",
    "origin_guideline_id",
    "origin_document_id",
    "source_hash",
    "source_page",
    "source_chunk_id",
    "origin_status",
    "section",
    "content",
    "score",
    "retrieval_method",
    "provenance",
}


def test_rag_hit_is_normalized_to_shared_evidence() -> None:
    evidence = build_rag_evidence(
        {
            "id": "hit-1",
            "source": "guideline.pdf",
            "document_id": "doc-1",
            "source_hash": "a" * 64,
            "chunk_id": "chunk-1",
            "page_num": 12,
            "parent_index": 3,
            "sub_index": 1,
            "text": "高血压患者应规律监测血压。",
            "score": 0.92,
            "retrieval_method": "dense_rerank",
        }
    )

    assert set(evidence) == EVIDENCE_FIELDS
    assert evidence["source_type"] == "document"
    assert evidence["source_id"] == "guideline.pdf"
    assert evidence["document_id"] == "doc-1"
    assert evidence["origin_document_id"] == "doc-1"
    assert evidence["source_hash"] == "a" * 64
    assert evidence["origin_status"] == "known"
    assert evidence["chunk_id"] == "chunk-1"
    assert evidence["page"] == 12
    assert evidence["section"] == "3/1"
    assert evidence["retrieval_method"] == "dense_rerank"


def test_kg_record_is_normalized_without_losing_raw_provenance() -> None:
    evidence = build_kg_evidence(
        {
            "id": "node-1",
            "name": "高血压",
            "labels": ["Disease"],
            "score": "0.88",
            "source_hash": "b" * 64,
            "source_chunk_index": 3,
        },
        tool_name="kg_query",
        evidence_kind="entity",
    )

    assert set(evidence) == EVIDENCE_FIELDS
    assert evidence["source_type"] == "knowledge_graph"
    assert evidence["source_id"] == "node-1"
    assert evidence["content"] == "高血压"
    assert evidence["score"] == 0.88
    assert evidence["source_hash"] == "b" * 64
    assert evidence["source_chunk_id"] == "3"
    assert evidence["provenance"]["kind"] == "entity"
    assert evidence["provenance"]["raw"]["labels"] == ["Disease"]


def test_subagent_result_enforces_evidence_model() -> None:
    result = SubAgentResult(
        target_agent="rag",
        evidence=[
            {
                "source_type": "document",
                "source_id": "guideline.pdf",
                "content": "控制盐摄入。",
                "retrieval_method": "hybrid_rerank",
            }
        ],
    )

    assert isinstance(result.evidence[0], Evidence)
    assert set(result.model_dump()["evidence"][0]) == EVIDENCE_FIELDS


def test_evidence_rejects_legacy_wrapper_fields() -> None:
    with pytest.raises(ValidationError):
        Evidence(
            source_type="document",
            source_id="guide.pdf",
            content="控制盐摄入。",
            retrieval_method="dense",
            hit={"text": "旧结构"},
        )


def test_rag_and_kg_nodes_emit_the_same_evidence_fields() -> None:
    rag_state = asyncio.run(
        normalize_rag_results(
            {
                "tool_results": [
                    {
                        "result": {
                            "evidence": [
                                build_rag_evidence(
                                    {
                                        "id": "chunk-1",
                                        "source": "guide.pdf",
                                        "text": "规律监测血压。",
                                        "score": 0.9,
                                        "retrieval_method": "dense_sparse_rrf_rerank",
                                    }
                                )
                            ]
                        }
                    }
                ],
                "constraints": {},
            }
        )
    )
    kg_state = asyncio.run(
        normalize_kg_results(
            {
                "tool_results": [
                    {
                        "tool_name": "kg_entity_relation_search",
                        "result": {
                            "evidence": [
                                build_kg_evidence(
                                    {"id": "node-1", "name": "高血压"},
                                    tool_name="kg_entity_relation_search",
                                    evidence_kind="entity",
                                )
                            ],
                        },
                    }
                ],
                "constraints": {},
            }
        )
    )

    rag_evidence = rag_state["evidence"][0]
    kg_evidence = kg_state["evidence"][0]
    assert set(rag_evidence) == EVIDENCE_FIELDS
    assert set(kg_evidence) == EVIDENCE_FIELDS
    assert rag_evidence["source_type"] == "document"
    assert kg_evidence["source_type"] == "knowledge_graph"


def test_vector_hit_records_the_completed_retrieval_stage() -> None:
    store = object.__new__(VectorStore)

    document = store._doc_from_hit(
        {
            "entity": {
                "text": "指南证据",
                "source": "guide.pdf",
                "chunk_id": "chunk-1",
            },
            "distance": 0.8,
        },
        retrieval_method="dense",
    )

    assert document.metadata["retrieval_method"] == "dense"
    assert document.metadata["search_score"] == 0.8


def test_rag_search_tool_returns_shared_evidence_contract() -> None:
    class VectorStoreStub:
        async def hybrid_search_with_rerank(
            self,
            *,
            query: str,
            k: int,
            source_filter: str | None,
        ) -> list[Document]:
            assert query == "高血压"
            assert k == 2
            assert source_filter is None
            return [
                Document(
                    page_content="规律监测血压。",
                    metadata={
                        "source": "guide.pdf",
                        "parent_id": "doc-1",
                        "chunk_id": "chunk-1",
                        "page_num": 12,
                        "final_rank_score": 0.93,
                        "retrieval_method": "dense_sparse_rrf_rerank",
                    },
                )
            ]

        async def search_by_mode(
            self,
            *,
            query: str,
            k: int,
            source_filter: str | None,
            retrieval_mode: str,
        ) -> list[Document]:
            assert query == "高血压"
            assert k == 2
            assert source_filter is None
            return [
                Document(
                    page_content="稠密检索证据。",
                    metadata={
                        "source": "guide.pdf",
                        "chunk_id": "chunk-dense",
                        "search_score": 0.81,
                        "retrieval_method": retrieval_mode,
                    },
                )
            ]

    tool = object.__new__(RagSearchTool)
    tool.conf = SimpleNamespace(
        RAG_SEARCH_TOP_K_DEFAULT=5,
        RAG_SEARCH_TOP_K_MAX=10,
    )
    tool.vector_store = VectorStoreStub()

    result = asyncio.run(tool.query({"query": "高血压", "top_k": 2}))

    assert "hits" not in result
    assert len(result["evidence"]) == 1
    assert set(result["evidence"][0]) == EVIDENCE_FIELDS
    assert result["evidence"][0]["retrieval_method"] == "dense_sparse_rrf_rerank"

    dense_result = asyncio.run(
        tool.query({"query": "高血压", "top_k": 2, "retrieval_mode": "dense"})
    )
    invalid_result = asyncio.run(
        tool.query({"query": "高血压", "retrieval_mode": "invalid"})
    )

    assert dense_result["evidence"][0]["retrieval_method"] == "dense"
    assert invalid_result["error"] == "unsupported_retrieval_mode:invalid"


def test_rag_search_tool_loads_vector_store_lazily(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        "app.mcp.tools.get_shared_vector_store",
        lambda conf: calls.append(conf),
    )
    conf = SimpleNamespace()

    tool = RagSearchTool(conf)

    assert calls == []
    _ = tool.vector_store
    assert calls == [conf]


def test_kg_query_tool_returns_shared_evidence_contract() -> None:
    class Neo4jClientStub:
        async def run_cypher(
            self,
            query: str,
            parameters: dict[str, object] | None = None,
        ) -> list[dict[str, object]]:
            assert query.startswith("MATCH")
            assert parameters is None
            return [
                {
                    "labels": ["Disease"],
                    "properties": {
                        "id": "node-1",
                        "name": "高血压",
                    },
                }
            ]

    tool = object.__new__(KnowledgeGraphTool)
    tool.client = Neo4jClientStub()
    tool.forbidden = []

    result = asyncio.run(
        tool.query(
            {
                "cypher": "MATCH (n) RETURN labels(n) AS labels, properties(n) AS properties",
                "limit": 1,
            }
        )
    )

    assert "records" not in result
    assert result["blocked"] is False
    assert result["mode"] == "cypher"
    assert len(result["evidence"]) == 1
    assert set(result["evidence"][0]) == EVIDENCE_FIELDS
    assert result["evidence"][0]["source_id"] == "node-1"
    assert result["evidence"][0]["content"] == "高血压"
    assert result["evidence"][0]["retrieval_method"] == "neo4j_cypher"


def test_kg_entity_relation_tool_returns_shared_evidence_contract() -> None:
    class KgStoreStub:
        collection_name = "kg_collection"

        async def search_entity_relations(
            self,
            **kwargs: object,
        ) -> dict[str, object]:
            assert kwargs["query"] == "高血压与肾病"
            return {
                "entities": [
                    {
                        "doc_id": "entity-1",
                        "name": "高血压",
                        "text": "高血压",
                        "score": 0.91,
                    }
                ],
                "relationships": [
                    {
                        "doc_id": "relation-1",
                        "source_name": "高血压",
                        "relation_type": "并发症",
                        "target_name": "慢性肾病",
                        "text": "高血压 -> 并发症 -> 慢性肾病",
                        "score": 0.88,
                    }
                ],
                "disambiguated_entities": [{"doc_id": "entity-1"}],
                "collection_name": self.collection_name,
                "retrieval_meta": {"lexical_weight": 0.35},
            }

    tool = object.__new__(KgEntityRelationSearchTool)
    tool.conf = SimpleNamespace(
        KG_RETRIEVAL_TOP_K_DEFAULT=5,
        KG_RETRIEVAL_TOP_K_MAX=10,
        KG_RETRIEVAL_LEXICAL_WEIGHT_DEFAULT=0.35,
        KG_RETRIEVAL_EXPANSION_LIMIT_DEFAULT=30,
        KG_COMMUNITY_TOP_K_DEFAULT=3,
        KG_DISAMBIGUATION_MARGIN_DEFAULT=0.12,
    )
    tool.store = KgStoreStub()
    tool._llm = None

    result = asyncio.run(
        tool.query(
            {
                "query": "高血压与肾病",
                "top_k": 2,
            }
        )
    )

    assert "entities" not in result
    assert "relationships" not in result
    assert "disambiguated_entities" not in result
    assert len(result["evidence"]) == 2
    assert all(set(item) == EVIDENCE_FIELDS for item in result["evidence"])
    assert result["evidence"][0]["source_id"] == "relation-1"
    assert result["evidence"][1]["source_id"] == "entity-1"
    assert result["evidence"][0]["provenance"]["kind"] == "relationship"
    assert (
        result["evidence"][0]["provenance"]["context"]["collection_name"]
        == "kg_collection"
    )


def test_orchestrator_history_and_final_prompt_consume_evidence() -> None:
    controller = object.__new__(UnifiedOrchestratorController)
    plan = build_question_plan("如何监测血压？", MedicalRiskAssessment())
    planned_task = next(
        item for item in plan.retrieval_tasks if item.target_agent == "rag"
    )
    evidence = build_rag_evidence(
        {
            "source": "guide.pdf",
            "chunk_id": "chunk-1",
            "page_num": 12,
            "text": "高血压患者应规律监测血压。",
            "retrieval_method": "dense_rerank",
        }
    )
    subagent_result = SubAgentResult(
        target_agent="rag",
        question="如何监测血压？",
        evidence=[evidence],
        query_records=[
            {
                "agent": "rag",
                "query": "旧摘要",
                "result_content": "不应成为终答证据来源",
                "citations": ["legacy"],
                "has_signal": True,
            }
        ],
        signal="strong",
    )

    observation = controller._build_subagent_observation(
        subagent_result,
        {
            "action": "call_rag",
            "task_id": planned_task.task_id,
            "must_cover_id": planned_task.must_cover_id,
        },
    )
    normalized_evidence = Evidence.model_validate(evidence)
    plan.must_cover[0].support_status = "supported"
    plan.must_cover[0].status = "covered"
    plan.claim_evidence_assessments.append(
        ClaimEvidenceAssessment(
            task_id=planned_task.task_id,
            must_cover_id=planned_task.must_cover_id,
            evidence_id=evidence_identity(normalized_evidence),
            support_status="supported",
            support_excerpt=normalized_evidence.content,
        )
    )
    answer_context = controller._build_answer_generation_context(
        question_plan=plan,
        tool_observations=[observation],
        conflict_rows=[],
    )
    assert observation["evidence"] == [evidence]
    evidence_summary = controller._build_evidence_summary([observation])
    assert evidence_summary["evidence_count"] == 1
    assert evidence_summary["citations"] == ["[guide.pdf p12 chunk:chunk-1]"]
    prompt = controller._build_final_answer_user_prompt(
        question="如何监测血压？",
        answer_context=answer_context,
    )
    assert "[guide.pdf p12 chunk:chunk-1]" in prompt
    assert "高血压患者应规律监测血压" in prompt
    assert "不应成为终答证据来源" not in prompt

    compact = controller._compact_tool_observations_for_compression([observation])
    assert compact[0]["evidence"] == [evidence]
    assert compact[0]["task_id"] == planned_task.task_id
    assert compact[0]["must_cover_id"] == planned_task.must_cover_id
    assert "query_records" not in compact[0]
    normalized_compact = controller._normalize_compressed_tool_observations(compact)
    assert normalized_compact[0]["task_id"] == planned_task.task_id
    assert normalized_compact[0]["must_cover_id"] == planned_task.must_cover_id

    history_observation = controller._build_history_observation(
        {
            "history_id": "h-1-2",
            "task_id": "task-history-1",
            "must_cover_id": "mc-1",
        },
        {
            "ok": True,
            "result": {
                "matched": True,
                "history_id": "h-1-2",
                "user_question": "如何监测血压？",
                "evidence": [evidence],
            },
        },
    )
    assert history_observation["evidence"] == [evidence]
    assert history_observation["task_id"] == "task-history-1"
    assert history_observation["must_cover_id"] == "mc-1"
    assert "query_records" not in history_observation


def test_orchestrator_history_action_is_reachable() -> None:
    evidence = build_rag_evidence(
        {
            "source": "guide.pdf",
            "document_id": "guide-document",
            "text": "历史证据",
            "retrieval_method": "dense",
        }
    )
    plan = build_question_plan("复用历史问题", MedicalRiskAssessment())

    class GatewayStub:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, object]]] = []

        async def invoke_tool(
            self,
            tool_name: str,
            payload: dict[str, object],
        ) -> dict[str, object]:
            self.calls.append((tool_name, payload))
            return {
                "ok": True,
                "result": {
                    "matched": True,
                    "history_id": "h-1-2",
                    "user_question": "历史问题",
                    "evidence": [evidence],
                    "claim_assessments": [
                        {
                            "task_id": plan.retrieval_tasks[0].task_id,
                            "must_cover_id": plan.must_cover[0].item_id,
                            "evidence_id": evidence_identity(
                                Evidence.model_validate(evidence)
                            ),
                            "support_status": "supported",
                            "support_excerpt": "历史证据",
                            "verifier_version": "claim_support.semantic.v1",
                        }
                    ],
                },
            }

    decisions = iter(
        [
            (
                {
                    "action": "call_history_read",
                    "task_id": plan.retrieval_tasks[0].task_id,
                    "must_cover_id": plan.must_cover[0].item_id,
                    "history_id": "h-1-2",
                    "reason": "复用历史证据",
                },
                {"total_tokens": 1},
            ),
            (
                {
                    "action": "final_answer",
                    "answer": "历史证据支持该回答。",
                },
                {"total_tokens": 1},
            ),
        ]
    )

    async def decide_action(
        **kwargs: object,
    ) -> tuple[dict[str, object], dict[str, int]]:
        _ = kwargs
        return next(decisions)

    async def stream_final_answer(
        **kwargs: object,
    ) -> tuple[str, dict[str, int]]:
        observations = kwargs["tool_observations"]
        assert isinstance(observations, list)
        assert observations[0]["evidence"] == [evidence]
        return "历史证据支持该回答。[guide.pdf]", {"total_tokens": 1}

    gateway = GatewayStub()
    controller = object.__new__(UnifiedOrchestratorController)
    controller._mcp_gateway = gateway
    controller._conf = SimpleNamespace(
        A2A_SUBAGENT_MAX_TOOL_CALLS_DEFAULT=3,
        A2A_SUBAGENT_MAX_TOOL_CALLS_MAX=3,
    )
    controller._decide_action = decide_action
    controller._stream_final_answer = stream_final_answer

    result = asyncio.run(
        controller.run(
            input_text="复用历史问题",
            session_id="session-1",
            max_turns=2,
            memory_context={
                "qa_memory_text": "当前暂无问答对",
                "retrieval_question_index_text": "history_id=h-1-2",
            },
        )
    )

    assert result["output"] == "历史证据支持该回答。[guide.pdf]"
    assert result["answer_validation"]["valid"] is True
    assert result["confidence"] == "medium"
    assert result["confidence_breakdown"]["publication_diversity_rate"] == 0.5
    assert result["evidence_summary"]["evidence_count"] == 1
    assert gateway.calls == [
        (
            "history_read",
            {
                "session_id": "session-1",
                "history_id": "h-1-2",
                "user_question": "",
            },
        )
    ]
