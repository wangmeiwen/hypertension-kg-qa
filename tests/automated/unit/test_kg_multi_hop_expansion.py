from unittest.mock import AsyncMock

import pytest

from app.rag_core.kg_milvus_store import KgMilvusStore


@pytest.mark.asyncio
async def test_expand_relation_neighbors_follows_two_hops() -> None:
    store = object.__new__(KgMilvusStore)
    store._query_relation_neighbors = AsyncMock(
        side_effect=[
            [
                {
                    "doc_id": "rel-1",
                    "source_name": "高血压",
                    "target_name": "脑卒中",
                }
            ],
            [
                {
                    "doc_id": "rel-1",
                    "source_name": "高血压",
                    "target_name": "脑卒中",
                },
                {
                    "doc_id": "rel-2",
                    "source_name": "脑卒中",
                    "target_name": "降压治疗",
                },
            ],
        ]
    )

    rows = await store._expand_relation_neighbors(
        ["高血压"],
        limit=10,
        max_hops=2,
    )

    assert [(row["doc_id"], row["graph_hop"]) for row in rows] == [
        ("rel-1", 1),
        ("rel-2", 2),
    ]
    assert store._query_relation_neighbors.await_args_list[1].args[0] == ["脑卒中"]


@pytest.mark.asyncio
async def test_build_entity_rows_reads_each_knowledge_entity_once() -> None:
    store = object.__new__(KgMilvusStore)
    neo4j_client = AsyncMock()
    neo4j_client.run_cypher.return_value = [
        {
            "n": {"name": "高血压", "entity_type": "Disease"},
            "node_id": 1,
            "entity_type": "Disease",
            "node_labels": ["KnowledgeEntity", "Disease"],
        }
    ]

    rows = await store._build_entity_rows(neo4j_client, None, "2026-07-30")

    assert len(rows) == 1
    assert rows[0]["label"] == "Disease"
    assert "类型 Disease" in rows[0]["text"]
    assert "MATCH (n:KnowledgeEntity)" in neo4j_client.run_cypher.call_args.args[0]


@pytest.mark.asyncio
async def test_build_relationship_rows_prefers_domain_entity_type() -> None:
    store = object.__new__(KgMilvusStore)
    neo4j_client = AsyncMock()
    neo4j_client.run_cypher.return_value = [
        {
            "from_id": 1,
            "from_labels": ["KnowledgeEntity", "Disease"],
            "from_name": "高血压",
            "from_entity_type": "Disease",
            "rel_type": "HAS_COMPLICATION",
            "rel_props": {"source_id": "guide"},
            "to_id": 2,
            "to_labels": ["KnowledgeEntity", "Complication"],
            "to_name": "脑卒中",
            "to_entity_type": "Complication",
        }
    ]

    rows = await store._build_relationship_rows(neo4j_client, "2026-07-30")

    assert rows[0]["source_label"] == "Disease"
    assert rows[0]["target_label"] == "Complication"
    assert "高血压(Disease)" in rows[0]["text"]


def test_relationship_selection_keeps_query_relevant_connected_second_hop() -> None:
    store = object.__new__(KgMilvusStore)
    rows = [
        {
            "doc_id": "edge-1",
            "item_type": "relationship",
            "source_name": "单纯夜间高血压",
            "relation_type": "DIAGNOSED_BY_METHOD",
            "target_name": "ABPM",
            "hybrid_score": 0.49,
        },
        {
            "doc_id": "edge-2",
            "item_type": "relationship",
            "source_name": "夜间高血压",
            "relation_type": "DIAGNOSED_BY_METHOD",
            "target_name": "ABPM",
            "hybrid_score": 0.48,
        },
        {
            "doc_id": "edge-3",
            "item_type": "relationship",
            "source_name": "ABPM",
            "relation_type": "FROM_GUIDELINE",
            "target_name": "《中国动态血压监测指南》",
            "hybrid_score": 0.18,
        },
        {
            "doc_id": "edge-4",
            "item_type": "relationship",
            "source_name": "ABPM",
            "relation_type": "METHOD_MEASURES",
            "target_name": "夜间血压",
            "hybrid_score": 0.47,
        },
    ]

    selected = store._select_relationship_path_rows(
        rows,
        query="哪份指南支持用于识别单纯夜间高血压的动态血压监测方法？",
        limit=2,
    )

    assert [row["doc_id"] for row in selected] == ["edge-1", "edge-3"]
