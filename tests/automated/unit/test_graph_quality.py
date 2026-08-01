from __future__ import annotations

from typing import Any

import pytest

from app.knowledge_graph.graph_quality import (
    GraphSourceContext,
    stage_graph_extraction,
)


def _source_context() -> GraphSourceContext:
    return {
        "source_id": "sha256:guide",
        "source_name": "guide.pdf",
        "source_hash": "sha256:guide",
        "source_chunk_index": 2,
        "extracted_at": "2026-07-29T08:00:00+00:00",
        "extraction_model": "graph-model",
    }


def test_graph_staging_normalizes_aliases_and_reports_connectivity() -> None:
    result = stage_graph_extraction(
        {
            "entities": [
                {
                    "name": "高血压病",
                    "type": "Disease",
                    "attributes": {
                        "confidence": "0.9",
                        "source_id": "model-spoofed-source",
                    },
                },
                {"name": "高血压", "type": "Disease"},
                {"name": "氨氯地平", "type": "Drug"},
                {"name": "中国高血压防治指南", "type": "Guideline"},
            ],
            "relations": [
                {
                    "subject": "高血压病",
                    "subject_type": "Disease",
                    "relation": "COMMON_DRUG",
                    "object": "氨氯地平",
                    "object_type": "Drug",
                    "attributes": {"confidence": 0.8},
                },
                {
                    "subject": "高血压",
                    "subject_type": "Disease",
                    "relation": "COMMON_DRUG",
                    "object": "氨氯地平",
                    "object_type": "Drug",
                },
            ],
        },
        _source_context(),
    )

    assert [item["name"] for item in result["entities"]] == [
        "高血压",
        "氨氯地平",
    ]
    assert result["entities"][0]["attributes"]["alias"] == "高血压病"
    assert result["entities"][0]["attributes"]["source_id"] == "sha256:guide"
    assert result["relations"][0]["subject"] == "高血压"
    assert result["relations"][0]["attributes"]["source_id"] == "sha256:guide"
    assert result["relations"][0]["attributes"]["quality_policy_version"] == 2
    assert result["metrics"]["duplicate_entities"] == 1
    assert result["metrics"]["duplicate_relations"] == 1
    assert result["metrics"]["isolated_node_rate"] == 0.0
    assert result["metrics"]["largest_connected_component_ratio"] == 1.0
    assert result["metrics"]["relationship_source_coverage_rate"] == 1.0
    assert result["metrics"]["confidence_coverage_rate"] == 0.666667


def test_graph_staging_keeps_policy_canonical_name_over_model_hint() -> None:
    result = stage_graph_extraction(
        {
            "entities": [
                {
                    "name": "踝臂血压指数",
                    "type": "Indicator",
                    "canonical_name": "ABI",
                },
                {"name": "高血压", "type": "Disease"},
            ],
            "relations": [
                {
                    "subject": "高血压",
                    "subject_type": "Disease",
                    "relation": "HAS_INDICATOR",
                    "object": "踝臂血压指数",
                    "object_type": "Indicator",
                }
            ],
        },
        _source_context(),
    )

    indicator = next(
        entity for entity in result["entities"] if entity["type"] == "Indicator"
    )
    assert indicator["name"] == "踝臂血压指数"
    assert indicator["canonical_name"] == "踝臂血压指数"


def test_graph_staging_rejects_invalid_direction_endpoint_and_attributes() -> None:
    result = stage_graph_extraction(
        {
            "entities": [
                {
                    "name": "缬沙坦",
                    "type": "Drug",
                    "attributes": {"year": "2024"},
                },
                {"name": "高血压", "type": "Disease"},
                {"name": "未知", "type": "Unknown"},
                {
                    "name": "坏置信度",
                    "type": "Disease",
                    "attributes": {"confidence": 2},
                },
            ],
            "relations": [
                {
                    "subject": "高血压",
                    "subject_type": "Disease",
                    "relation": "BELONGS_TO_CLASS",
                    "object": "缬沙坦",
                    "object_type": "Drug",
                },
                {
                    "subject": "缬沙坦",
                    "subject_type": "Drug",
                    "relation": "TREATS_DISEASE",
                    "object": "脑卒中",
                    "object_type": "Disease",
                },
                {
                    "subject": "缬沙坦",
                    "subject_type": "Drug",
                    "relation": "TREATS_DISEASE",
                    "object": "高血压",
                    "object_type": "Disease",
                    "attributes": {"confidence": 1.2},
                },
            ],
        },
        _source_context(),
    )

    assert result["entities"] == []
    assert result["relations"] == []
    assert {issue["reason"] for issue in result["rejected"]} == {
        "unsupported_entity_type",
        "invalid_confidence",
        "invalid_relation_direction",
        "relation_endpoint_missing",
        "isolated_entity",
    }
    assert result["metrics"]["rejected_entities"] == 4
    assert result["metrics"]["rejected_relations"] == 3


def test_graph_staging_requires_complete_trusted_source_context() -> None:
    context = _source_context()
    context["source_hash"] = ""

    with pytest.raises(ValueError, match="来源上下文不完整"):
        stage_graph_extraction({"entities": [], "relations": []}, context)


def test_graph_staging_resolves_relation_endpoints_from_declared_canonical_name() -> (
    None
):
    result = stage_graph_extraction(
        {
            "entities": [
                {
                    "name": "原发性高血压症",
                    "canonical_name": "原发性高血压",
                    "type": "Disease",
                },
                {"name": "硝苯地平", "type": "Drug"},
            ],
            "relations": [
                {
                    "subject": "原发性高血压症",
                    "subject_type": "Disease",
                    "relation": "COMMON_DRUG",
                    "object": "硝苯地平",
                    "object_type": "Drug",
                }
            ],
        },
        _source_context(),
    )

    assert result["relations"][0]["subject"] == "原发性高血压"
    assert result["metrics"]["rejected_relations"] == 0


def test_graph_staging_resolves_domain_abbreviations_deterministically() -> None:
    result = stage_graph_extraction(
        {
            "entities": [
                {"name": "CKD", "type": "Disease"},
                {"name": "慢性肾病", "type": "Disease"},
                {"name": "eGFR", "type": "Indicator"},
                {"name": "估算肾小球滤过率", "type": "Indicator"},
            ],
            "relations": [
                {
                    "subject": "CKD",
                    "subject_type": "Disease",
                    "relation": "HAS_INDICATOR",
                    "object": "eGFR",
                    "object_type": "Indicator",
                }
            ],
        },
        _source_context(),
    )

    assert [(item["name"], item["type"]) for item in result["entities"]] == [
        ("慢性肾脏病", "Disease"),
        ("估算肾小球滤过率", "Indicator"),
    ]
    assert result["metrics"]["duplicate_entities"] == 2


def test_graph_staging_merges_domain_synonyms_before_writing() -> None:
    result = stage_graph_extraction(
        {
            "entities": [
                {"name": "孤立性舒张期高血压", "type": "Phenotype"},
                {"name": "单纯舒张期高血压", "type": "Phenotype"},
                {"name": "高血压", "type": "Disease"},
                {"name": "踝/臂血压指数", "type": "Indicator"},
                {"name": "踝臂血压指数", "type": "Indicator"},
            ],
            "relations": [
                {
                    "subject": "高血压",
                    "subject_type": "Disease",
                    "relation": "HAS_PHENOTYPE",
                    "object": "孤立性舒张期高血压",
                    "object_type": "Phenotype",
                },
                {
                    "subject": "高血压",
                    "subject_type": "Disease",
                    "relation": "HAS_INDICATOR",
                    "object": "踝/臂血压指数",
                    "object_type": "Indicator",
                },
            ],
        },
        _source_context(),
    )

    assert {(item["name"], item["type"]) for item in result["entities"]} == {
        ("高血压", "Disease"),
        ("单纯舒张期高血压", "Phenotype"),
        ("踝臂血压指数", "Indicator"),
    }
    assert result["metrics"]["duplicate_entities"] == 2


def test_graph_query_building_only_uses_staging_records(monkeypatch) -> None:
    import app.knowledge_graph.extract_graph_data_utils as module

    monkeypatch.setattr(
        module,
        "extract_hypertension_knowledge",
        lambda *_args, **_kwargs: {
            "entities": [
                {"name": "高血压病", "type": "Disease"},
                {"name": "氨氯地平", "type": "Drug"},
            ],
            "relations": [
                {
                    "subject": "高血压病",
                    "subject_type": "Disease",
                    "relation": "COMMON_DRUG",
                    "object": "氨氯地平",
                    "object_type": "Drug",
                },
                {
                    "subject": "氨氯地平",
                    "subject_type": "Drug",
                    "relation": "COMMON_DRUG",
                    "object": "高血压病",
                    "object_type": "Disease",
                },
            ],
        },
    )
    events: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    result = module.extract_text_to_graph_queries(
        "高血压常用药包括氨氯地平。",
        source_context=_source_context(),
        stage_callback=lambda *args, **kwargs: events.append((args, kwargs)),
        llm_bundle={},
    )

    assert result["stats"]["entities"] == 2
    assert result["stats"]["relations"] == 1
    assert result["stats"]["rejected_relations"] == 1
    relation_query, relation_params = result["relation_queries"][0]
    assert relation_params["rows"][0]["subj"] == "高血压"
    assert relation_params["rows"][0]["source_id"] == "sha256:guide"
    assert "MERGE (a)-[r:COMMON_DRUG]->(b)" in relation_query
    assert "source_chunk_indices" in relation_query
    assert result["quality"]["rejected"][0]["reason"] == "invalid_relation_direction"
    assert any(event[0][1] == "graph_quality_staging" for event in events)
