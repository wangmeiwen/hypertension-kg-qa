"""Neo4j 全图质量快照的纯计算逻辑。"""

from __future__ import annotations

import math
import unicodedata
from collections import Counter, defaultdict
from typing import Any, TypedDict

from app.knowledge_graph.graph_quality import GRAPH_QUALITY_POLICY_VERSION


class GraphQualitySnapshotReport(TypedDict):
    """一次全图只读快照的质量指标。"""

    captured_at: str
    consistency: str
    total_nodes: int
    total_relationships: int
    duplicate_entity_count: int
    duplicate_entity_rate: float
    unnamed_node_count: int
    unnamed_node_rate: float
    isolated_node_count: int
    isolated_node_rate: float
    largest_connected_component_nodes: int
    largest_connected_component_ratio: float
    dangling_relationship_count: int
    dangling_relationship_rate: float
    relationship_source_covered_count: int
    relationship_source_coverage_rate: float
    relationship_confidence_covered_count: int
    relationship_confidence_coverage_rate: float
    quality_policy_covered_relationship_count: int
    quality_policy_coverage_rate: float
    quality_policy_version: int


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def _normalized_text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(unicodedata.normalize("NFKC", str(value)).strip().split())


def _has_valid_confidence(value: Any) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(confidence) and 0 <= confidence <= 1


def _has_source_provenance(relationship: dict[str, Any]) -> bool:
    source_id = _normalized_text(relationship.get("source_id"))
    chunk_index = relationship.get("source_chunk_index")
    if not source_id or chunk_index is None or isinstance(chunk_index, bool):
        return False
    try:
        return int(chunk_index) > 0
    except (TypeError, ValueError):
        return False


def _has_current_quality_policy(relationship: dict[str, Any]) -> bool:
    value = relationship.get("quality_policy_version")
    if value is None or isinstance(value, bool):
        return False
    try:
        return int(value) == GRAPH_QUALITY_POLICY_VERSION
    except (TypeError, ValueError):
        return False


def build_graph_quality_snapshot(
    nodes: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
    *,
    captured_at: str,
) -> GraphQualitySnapshotReport:
    """根据 Neo4j 节点和关系投影计算全图质量指标。"""
    nodes_by_id = {row["id"]: row for row in nodes}
    node_ids = set(nodes_by_id)
    total_nodes = len(node_ids)
    total_relationships = len(relationships)
    entity_keys = [
        (
            _normalized_text(row.get("entity_type")).casefold(),
            _normalized_text(row.get("canonical_name")).casefold(),
        )
        for row in nodes_by_id.values()
        if _normalized_text(row.get("canonical_name"))
    ]
    key_counts = Counter(entity_keys)
    duplicate_entity_count = sum(count - 1 for count in key_counts.values())
    unnamed_node_count = sum(
        1
        for row in nodes_by_id.values()
        if not _normalized_text(row.get("canonical_name"))
    )

    adjacency: dict[Any, set[Any]] = defaultdict(set)
    dangling_relationship_count = 0
    for relationship in relationships:
        source_id = relationship.get("source_node_id")
        target_id = relationship.get("target_node_id")
        if source_id not in node_ids or target_id not in node_ids:
            dangling_relationship_count += 1
            continue
        adjacency[source_id].add(target_id)
        adjacency[target_id].add(source_id)

    isolated_node_count = sum(1 for node_id in node_ids if not adjacency[node_id])
    largest_component = 0
    remaining = set(node_ids)
    while remaining:
        pending = [remaining.pop()]
        component_size = 0
        while pending:
            node_id = pending.pop()
            component_size += 1
            for neighbor in adjacency[node_id]:
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    pending.append(neighbor)
        largest_component = max(largest_component, component_size)

    source_covered = sum(
        1 for relationship in relationships if _has_source_provenance(relationship)
    )
    confidence_covered = sum(
        1
        for relationship in relationships
        if _has_valid_confidence(relationship.get("confidence"))
    )
    quality_policy_covered = sum(
        1 for relationship in relationships if _has_current_quality_policy(relationship)
    )
    return {
        "captured_at": captured_at,
        "consistency": "best_effort_read",
        "total_nodes": total_nodes,
        "total_relationships": total_relationships,
        "duplicate_entity_count": duplicate_entity_count,
        "duplicate_entity_rate": _ratio(duplicate_entity_count, total_nodes),
        "unnamed_node_count": unnamed_node_count,
        "unnamed_node_rate": _ratio(unnamed_node_count, total_nodes),
        "isolated_node_count": isolated_node_count,
        "isolated_node_rate": _ratio(isolated_node_count, total_nodes),
        "largest_connected_component_nodes": largest_component,
        "largest_connected_component_ratio": _ratio(largest_component, total_nodes),
        "dangling_relationship_count": dangling_relationship_count,
        "dangling_relationship_rate": _ratio(
            dangling_relationship_count, total_relationships
        ),
        "relationship_source_covered_count": source_covered,
        "relationship_source_coverage_rate": _ratio(
            source_covered, total_relationships
        ),
        "relationship_confidence_covered_count": confidence_covered,
        "relationship_confidence_coverage_rate": _ratio(
            confidence_covered, total_relationships
        ),
        "quality_policy_covered_relationship_count": quality_policy_covered,
        "quality_policy_coverage_rate": _ratio(
            quality_policy_covered, total_relationships
        ),
        "quality_policy_version": GRAPH_QUALITY_POLICY_VERSION,
    }
