from __future__ import annotations

from typing import Any

import pytest

from app.backend.services.graph_quality_service import GraphQualityService
from app.knowledge_graph.graph_quality_snapshot import (
    build_graph_quality_snapshot,
)


def test_graph_quality_snapshot_computes_global_metrics() -> None:
    report = build_graph_quality_snapshot(
        [
            {
                "id": 1,
                "entity_type": "Disease",
                "canonical_name": "高血压",
            },
            {
                "id": 2,
                "entity_type": "Disease",
                "canonical_name": "高血压",
            },
            {"id": 3, "entity_type": "Drug", "canonical_name": "氨氯地平"},
            {"id": 4, "entity_type": "Guideline", "canonical_name": ""},
        ],
        [
            {
                "source_node_id": 1,
                "target_node_id": 3,
                "source_id": "guide-a",
                "source_chunk_index": 1,
                "confidence": 0.8,
                "quality_policy_version": 2,
            },
            {
                "source_node_id": 2,
                "target_node_id": 3,
                "source_id": None,
                "source_chunk_index": None,
                "confidence": None,
                "quality_policy_version": None,
            },
        ],
        captured_at="2026-07-29T09:00:00+00:00",
    )

    assert report["duplicate_entity_count"] == 1
    assert report["duplicate_entity_rate"] == 0.25
    assert report["unnamed_node_rate"] == 0.25
    assert report["isolated_node_rate"] == 0.25
    assert report["largest_connected_component_ratio"] == 0.75
    assert report["relationship_source_coverage_rate"] == 0.5
    assert report["relationship_confidence_coverage_rate"] == 0.5
    assert report["quality_policy_coverage_rate"] == 0.5


def test_graph_quality_snapshot_counts_dangling_relationship_projection() -> None:
    report = build_graph_quality_snapshot(
        [{"id": 1, "entity_type": "Disease", "canonical_name": "高血压"}],
        [
            {
                "source_node_id": 1,
                "target_node_id": 9,
                "source_id": "guide-a",
                "source_chunk_index": 1,
                "confidence": 0.5,
                "quality_policy_version": 2,
            }
        ],
        captured_at="2026-07-29T09:00:00+00:00",
    )

    assert report["dangling_relationship_rate"] == 1.0
    assert report["isolated_node_rate"] == 1.0
    assert report["largest_connected_component_ratio"] == 1.0


class _QualityNeo4jStub:
    def __init__(self, *, total_nodes: int = 2, total_relationships: int = 1) -> None:
        self.total_nodes = total_nodes
        self.total_relationships = total_relationships
        self.queries: list[str] = []

    async def run_cypher(self, query: str) -> list[dict[str, Any]]:
        self.queries.append(query)
        if "count(n) AS total" in query:
            return [{"total": self.total_nodes}]
        if "count(r) AS total" in query:
            return [{"total": self.total_relationships}]
        if "coalesce(n.entity_type" in query:
            return [
                {
                    "id": 1,
                    "entity_type": "Disease",
                    "canonical_name": "高血压",
                },
                {"id": 2, "entity_type": "Drug", "canonical_name": "氨氯地平"},
            ]
        if "r.source_id AS source_id" in query:
            return [
                {
                    "source_node_id": 1,
                    "target_node_id": 2,
                    "source_id": "guide-a",
                    "source_chunk_index": 1,
                    "confidence": 0.9,
                    "quality_policy_version": 2,
                }
            ]
        raise AssertionError(f"unexpected query: {query}")


async def _await_call(func, *args):
    return await func(*args)


@pytest.mark.asyncio
async def test_graph_quality_service_returns_complete_snapshot() -> None:
    neo4j = _QualityNeo4jStub()
    errors: list[tuple[Any, ...]] = []
    service = GraphQualityService(
        await_call=_await_call,
        neo4j_client=neo4j,
        log_api_error=lambda *args, **_kwargs: errors.append(args),
    )

    result = await service.get_quality_report(
        max_nodes=10,
        max_relationships=10,
    )

    assert result["ok"] is True
    assert result["data"]["total_nodes"] == 2
    assert result["data"]["relationship_source_coverage_rate"] == 1.0
    assert len(neo4j.queries) == 4
    assert errors == []


@pytest.mark.asyncio
async def test_graph_quality_service_refuses_partial_metrics_when_snapshot_is_too_large() -> (
    None
):
    neo4j = _QualityNeo4jStub(total_nodes=11, total_relationships=20)
    service = GraphQualityService(
        await_call=_await_call,
        neo4j_client=neo4j,
        log_api_error=lambda *_args, **_kwargs: None,
    )

    result = await service.get_quality_report(
        max_nodes=10,
        max_relationships=100,
    )

    assert result["ok"] is False
    assert result["code"] == "graph_quality_snapshot_too_large"
    assert result["data"]["total_nodes"] == 11
    assert len(neo4j.queries) == 2
