import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.backend.services.graph_public_service import GraphPublicService


def test_balanced_relationship_rows_prioritize_multiple_nodes_before_expanding_cap():
    """验证公开图谱关系筛选会保留多个源节点，而不是被 hub 节点完全占满。"""
    rows = [
        {
            "source_id": "hub",
            "target_id": f"leaf-{index}",
            "source_name": "Hub",
            "target_name": f"Leaf {index}",
            "source_labels": ["Entity"],
            "target_labels": ["Entity"],
            "rel_type": "RELATED_TO",
        }
        for index in range(1, 21)
    ]
    rows.extend(
        [
            {
                "source_id": "node-a",
                "target_id": f"node-a-{index}",
                "source_name": "Node A",
                "target_name": f"Node A {index}",
                "source_labels": ["Entity"],
                "target_labels": ["Entity"],
                "rel_type": "RELATED_TO",
            }
            for index in range(1, 7)
        ]
    )
    rows.extend(
        [
            {
                "source_id": "node-b",
                "target_id": f"node-b-{index}",
                "source_name": "Node B",
                "target_name": f"Node B {index}",
                "source_labels": ["Entity"],
                "target_labels": ["Entity"],
                "rel_type": "RELATED_TO",
            }
            for index in range(1, 7)
        ]
    )

    selected = GraphPublicService._select_balanced_relationship_rows(rows, limit=20)
    selected_source_counts = {}
    for row in selected:
        source_id = str(row["source_id"])
        selected_source_counts[source_id] = selected_source_counts.get(source_id, 0) + 1

    assert len(selected) == 20
    assert selected_source_counts["hub"] == 10
    assert selected_source_counts["node-a"] + selected_source_counts["node-b"] == 10
    assert selected_source_counts["node-a"] >= 4
    assert selected_source_counts["node-b"] >= 4
