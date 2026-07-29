"""高血压知识图谱抽取结果的 staging 质量门槛。"""

from __future__ import annotations

import math
import unicodedata
from collections import defaultdict
from datetime import datetime
from typing import Any, TypedDict


GRAPH_QUALITY_POLICY_VERSION = 1


class GraphSourceContext(TypedDict):
    """图谱抽取记录的可信来源上下文。"""

    source_id: str
    source_name: str
    source_hash: str
    source_chunk_index: int
    extracted_at: str
    extraction_model: str


class GraphQualityIssue(TypedDict):
    """被 staging 拒绝的记录及原因。"""

    record_type: str
    record_index: int
    reason: str


class GraphQualityMetrics(TypedDict):
    """单个文本块的图谱质量指标。"""

    input_entities: int
    accepted_entities: int
    rejected_entities: int
    duplicate_entities: int
    duplicate_entity_rate: float
    input_relations: int
    accepted_relations: int
    rejected_relations: int
    duplicate_relations: int
    duplicate_relation_rate: float
    isolated_node_rate: float
    largest_connected_component_ratio: float
    relationship_source_coverage_rate: float
    confidence_coverage_rate: float


class GraphStagingResult(TypedDict):
    """通过质量门槛后可进入正式图的数据。"""

    entities: list[dict[str, Any]]
    relations: list[dict[str, Any]]
    rejected: list[GraphQualityIssue]
    metrics: GraphQualityMetrics


_ENTITY_TYPES = frozenset(
    {
        "Disease",
        "DiseaseStage",
        "DiseaseGrade",
        "RiskStratification",
        "Phenotype",
        "BloodPressurePattern",
        "Symptom",
        "RiskFactor",
        "Complication",
        "TargetOrganDamage",
        "Check",
        "MeasurementMethod",
        "Indicator",
        "MeasurementContext",
        "ThresholdRule",
        "Drug",
        "DrugClass",
        "Reaction",
        "TreatmentPlan",
        "PatientProfile",
        "ClinicalRule",
        "Guideline",
        "Source",
    }
)

_ALL_SOURCE_SUBJECT_TYPES = _ENTITY_TYPES - {"Source"}
_RELATION_SIGNATURES: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "HAS_STAGE": (frozenset({"Disease"}), frozenset({"DiseaseStage"})),
    "HAS_GRADE": (
        frozenset({"Disease", "PatientProfile"}),
        frozenset({"DiseaseGrade"}),
    ),
    "HAS_RISK_STRATIFICATION": (
        frozenset({"Disease", "PatientProfile"}),
        frozenset({"RiskStratification"}),
    ),
    "HAS_PHENOTYPE": (frozenset({"Disease"}), frozenset({"Phenotype"})),
    "HAS_BP_PATTERN": (
        frozenset({"Disease", "PatientProfile"}),
        frozenset({"BloodPressurePattern"}),
    ),
    "HAS_SYMPTOM": (frozenset({"Disease"}), frozenset({"Symptom"})),
    "HAS_RISK_FACTOR": (frozenset({"Disease"}), frozenset({"RiskFactor"})),
    "HAS_COMPLICATION": (frozenset({"Disease"}), frozenset({"Complication"})),
    "HAS_TARGET_ORGAN_DAMAGE": (
        frozenset({"Disease"}),
        frozenset({"TargetOrganDamage"}),
    ),
    "NEEDS_CHECK": (
        frozenset({"Disease", "PatientProfile"}),
        frozenset({"Check"}),
    ),
    "DIAGNOSED_BY_METHOD": (
        frozenset({"Disease", "Phenotype", "BloodPressurePattern"}),
        frozenset({"MeasurementMethod"}),
    ),
    "HAS_INDICATOR": (
        frozenset({"Disease", "Check", "TargetOrganDamage"}),
        frozenset({"Indicator"}),
    ),
    "METHOD_MEASURES": (
        frozenset({"MeasurementMethod", "Check"}),
        frozenset({"Indicator"}),
    ),
    "MEASURED_IN_CONTEXT": (
        frozenset({"Indicator", "Check", "ThresholdRule"}),
        frozenset({"MeasurementContext"}),
    ),
    "HAS_THRESHOLD": (
        frozenset({"Disease", "Phenotype", "Indicator", "MeasurementContext"}),
        frozenset({"ThresholdRule"}),
    ),
    "HAS_CONTROL_TARGET": (
        frozenset({"Disease", "PatientProfile", "BloodPressurePattern"}),
        frozenset({"ThresholdRule"}),
    ),
    "START_TREATMENT_AT": (
        frozenset({"Disease", "PatientProfile"}),
        frozenset({"ThresholdRule"}),
    ),
    "IDENTIFIED_BY": (
        frozenset({"Phenotype", "TargetOrganDamage"}),
        frozenset({"Check"}),
    ),
    "COMMON_DRUG": (frozenset({"Disease"}), frozenset({"Drug"})),
    "BELONGS_TO_CLASS": (frozenset({"Drug"}), frozenset({"DrugClass"})),
    "MAY_CAUSE_REACTION": (frozenset({"Drug"}), frozenset({"Reaction"})),
    "TREATS_DISEASE": (
        frozenset({"Drug", "TreatmentPlan"}),
        frozenset({"Disease"}),
    ),
    "SUITABLE_FOR_PROFILE": (
        frozenset({"Drug", "TreatmentPlan"}),
        frozenset({"PatientProfile"}),
    ),
    "HAS_COMORBIDITY": (
        frozenset({"PatientProfile"}),
        frozenset({"Disease"}),
    ),
    "HAS_TRIGGER": (
        frozenset({"ClinicalRule"}),
        frozenset(
            {
                "Symptom",
                "RiskFactor",
                "Indicator",
                "TargetOrganDamage",
                "Phenotype",
                "Disease",
                "ThresholdRule",
            }
        ),
    ),
    "APPLIES_TO_DISEASE": (
        frozenset({"ClinicalRule"}),
        frozenset({"Disease"}),
    ),
    "APPLIES_TO_PROFILE": (
        frozenset({"ClinicalRule"}),
        frozenset({"PatientProfile"}),
    ),
    "RECOMMENDS_DRUG": (frozenset({"ClinicalRule"}), frozenset({"Drug"})),
    "RECOMMENDS_DRUG_CLASS": (
        frozenset({"ClinicalRule"}),
        frozenset({"DrugClass"}),
    ),
    "RECOMMENDS_CHECK": (frozenset({"ClinicalRule"}), frozenset({"Check"})),
    "RECOMMENDS_PLAN": (
        frozenset({"ClinicalRule"}),
        frozenset({"TreatmentPlan"}),
    ),
    "FROM_GUIDELINE": (_ALL_SOURCE_SUBJECT_TYPES, frozenset({"Guideline"})),
    "FROM_SOURCE": (_ALL_SOURCE_SUBJECT_TYPES, frozenset({"Source"})),
    "SUPPORTED_BY": (_ALL_SOURCE_SUBJECT_TYPES, frozenset({"Source", "Guideline"})),
}

_ALIASES = {
    ("Disease", "高血压病"): "高血压",
    ("DrugClass", "ace抑制剂"): "ACEI",
    ("DrugClass", "血管紧张素转换酶抑制剂"): "ACEI",
    ("DrugClass", "血管紧张素ⅱ受体阻滞剂"): "ARB",
    ("DrugClass", "血管紧张素ii受体阻滞剂"): "ARB",
    ("DrugClass", "血管紧张素ⅱ受体拮抗剂"): "ARB",
    ("DrugClass", "血管紧张素ii受体拮抗剂"): "ARB",
    ("DrugClass", "钙通道阻滞剂"): "CCB",
    ("MeasurementMethod", "动态血压监测"): "ABPM",
    ("MeasurementMethod", "家庭血压监测"): "HBPM",
}


def _normalized_text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(unicodedata.normalize("NFKC", str(value)).strip().split())


def _lookup_key(value: Any) -> str:
    return _normalized_text(value).casefold()


def _canonical_name(entity_type: str, name: Any, canonical_name: Any = "") -> str:
    normalized_name = _normalized_text(name)
    mapped_name = _ALIASES.get((entity_type, _lookup_key(normalized_name)))
    if mapped_name is not None:
        return mapped_name
    normalized_canonical = _normalized_text(canonical_name)
    return normalized_canonical if normalized_canonical else normalized_name


def _sanitize_attributes(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    attributes: dict[str, Any] = {}
    for raw_key, raw_value in value.items():
        key = _normalized_text(raw_key)
        if not key or raw_value is None:
            continue
        if isinstance(raw_value, (str, int, float, bool)):
            attributes[key] = raw_value
        else:
            attributes[key] = str(raw_value)
    return attributes


def _normalize_confidence(attributes: dict[str, Any]) -> bool:
    if "confidence" not in attributes:
        return True
    value = attributes["confidence"]
    if isinstance(value, bool):
        return False
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(confidence) or confidence < 0 or confidence > 1:
        return False
    attributes["confidence"] = confidence
    return True


def _normalize_year(attributes: dict[str, Any]) -> bool:
    if "year" not in attributes:
        return True
    value = attributes["year"]
    if isinstance(value, bool):
        return False
    try:
        year = int(str(value).strip())
    except (TypeError, ValueError):
        return False
    if year < 1800 or year > 2100:
        return False
    attributes["year"] = year
    return True


def _validate_source_context(context: GraphSourceContext) -> None:
    required_text = (
        context["source_id"],
        context["source_name"],
        context["source_hash"],
        context["extracted_at"],
        context["extraction_model"],
    )
    if any(not _normalized_text(value) for value in required_text):
        raise ValueError("图谱 staging 来源上下文不完整")
    if context["source_chunk_index"] < 1:
        raise ValueError("图谱 staging 分块序号必须大于 0")
    try:
        datetime.fromisoformat(context["extracted_at"].replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("图谱 staging 抽取时间不是 ISO 8601 格式") from exc


def _with_provenance(
    attributes: dict[str, Any], context: GraphSourceContext
) -> dict[str, Any]:
    enriched = dict(attributes)
    enriched.update(
        {
            "source_id": context["source_id"],
            "source_name": context["source_name"],
            "source_hash": context["source_hash"],
            "source_chunk_index": context["source_chunk_index"],
            "extracted_at": context["extracted_at"],
            "extraction_model": context["extraction_model"],
            "quality_policy_version": GRAPH_QUALITY_POLICY_VERSION,
        }
    )
    return enriched


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def _connectivity_metrics(
    entities: list[dict[str, Any]], relations: list[dict[str, Any]]
) -> tuple[float, float]:
    nodes = {(item["name"], item["type"]) for item in entities}
    if not nodes:
        return 0.0, 0.0
    adjacency: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
    for relation in relations:
        source = (relation["subject"], relation["subject_type"])
        target = (relation["object"], relation["object_type"])
        adjacency[source].add(target)
        adjacency[target].add(source)
    isolated_count = sum(1 for node in nodes if not adjacency[node])
    remaining = set(nodes)
    largest_component = 0
    while remaining:
        pending = [remaining.pop()]
        component_size = 0
        while pending:
            node = pending.pop()
            component_size += 1
            for neighbor in adjacency[node]:
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    pending.append(neighbor)
        largest_component = max(largest_component, component_size)
    return _ratio(isolated_count, len(nodes)), _ratio(largest_component, len(nodes))


def stage_graph_extraction(
    extraction: dict[str, Any], context: GraphSourceContext
) -> GraphStagingResult:
    """逐条验证抽取结果，仅返回可安全写入正式图的记录。"""
    _validate_source_context(context)
    raw_entities = extraction.get("entities")
    raw_relations = extraction.get("relations")
    entity_items = raw_entities if isinstance(raw_entities, list) else []
    relation_items = raw_relations if isinstance(raw_relations, list) else []
    accepted_entities: list[dict[str, Any]] = []
    accepted_relations: list[dict[str, Any]] = []
    rejected: list[GraphQualityIssue] = []
    entity_keys: set[tuple[str, str]] = set()
    entity_records: dict[tuple[str, str], dict[str, Any]] = {}
    entity_aliases: dict[tuple[str, str], str] = {}
    duplicate_entities = 0

    for index, raw_entity in enumerate(entity_items):
        if not isinstance(raw_entity, dict):
            rejected.append(
                {
                    "record_type": "entity",
                    "record_index": index,
                    "reason": "entity_not_object",
                }
            )
            continue
        entity_type = _normalized_text(raw_entity.get("type"))
        name = _normalized_text(raw_entity.get("name"))
        if entity_type not in _ENTITY_TYPES:
            rejected.append(
                {
                    "record_type": "entity",
                    "record_index": index,
                    "reason": "unsupported_entity_type",
                }
            )
            continue
        if not name:
            rejected.append(
                {
                    "record_type": "entity",
                    "record_index": index,
                    "reason": "empty_entity_name",
                }
            )
            continue
        canonical_name = _canonical_name(
            entity_type, name, raw_entity.get("canonical_name")
        )
        attributes = _sanitize_attributes(raw_entity.get("attributes"))
        if not _normalize_confidence(attributes):
            rejected.append(
                {
                    "record_type": "entity",
                    "record_index": index,
                    "reason": "invalid_confidence",
                }
            )
            continue
        if not _normalize_year(attributes):
            rejected.append(
                {
                    "record_type": "entity",
                    "record_index": index,
                    "reason": "invalid_year",
                }
            )
            continue
        entity_key = (canonical_name, entity_type)
        entity_aliases[(name, entity_type)] = canonical_name
        entity_aliases[(canonical_name, entity_type)] = canonical_name
        if entity_key in entity_keys:
            duplicate_entities += 1
            existing_attributes = entity_records[entity_key]["attributes"]
            for attribute_name, attribute_value in attributes.items():
                if attribute_name not in existing_attributes:
                    existing_attributes[attribute_name] = attribute_value
            continue
        if canonical_name != name and "alias" not in attributes:
            attributes["alias"] = name
        entity_keys.add(entity_key)
        entity_record = {
            "name": canonical_name,
            "type": entity_type,
            "canonical_name": canonical_name,
            "attributes": _with_provenance(attributes, context),
        }
        entity_records[entity_key] = entity_record
        accepted_entities.append(entity_record)

    relation_keys: set[tuple[str, str, str, str, str]] = set()
    relation_records: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    duplicate_relations = 0
    for index, raw_relation in enumerate(relation_items):
        if not isinstance(raw_relation, dict):
            rejected.append(
                {
                    "record_type": "relation",
                    "record_index": index,
                    "reason": "relation_not_object",
                }
            )
            continue
        subject_type = _normalized_text(raw_relation.get("subject_type"))
        object_type = _normalized_text(raw_relation.get("object_type"))
        relation_type = _normalized_text(raw_relation.get("relation"))
        signature = _RELATION_SIGNATURES.get(relation_type)
        if signature is None:
            rejected.append(
                {
                    "record_type": "relation",
                    "record_index": index,
                    "reason": "unsupported_relation_type",
                }
            )
            continue
        if subject_type not in signature[0] or object_type not in signature[1]:
            rejected.append(
                {
                    "record_type": "relation",
                    "record_index": index,
                    "reason": "invalid_relation_direction",
                }
            )
            continue
        raw_subject = _normalized_text(raw_relation.get("subject"))
        raw_object = _normalized_text(raw_relation.get("object"))
        subject = entity_aliases.get(
            (raw_subject, subject_type),
            _canonical_name(subject_type, raw_subject),
        )
        obj = entity_aliases.get(
            (raw_object, object_type),
            _canonical_name(object_type, raw_object),
        )
        if (subject, subject_type) not in entity_keys or (
            obj,
            object_type,
        ) not in entity_keys:
            rejected.append(
                {
                    "record_type": "relation",
                    "record_index": index,
                    "reason": "relation_endpoint_missing",
                }
            )
            continue
        attributes = _sanitize_attributes(raw_relation.get("attributes"))
        if not _normalize_confidence(attributes):
            rejected.append(
                {
                    "record_type": "relation",
                    "record_index": index,
                    "reason": "invalid_confidence",
                }
            )
            continue
        if not _normalize_year(attributes):
            rejected.append(
                {
                    "record_type": "relation",
                    "record_index": index,
                    "reason": "invalid_year",
                }
            )
            continue
        relation_key = (subject, subject_type, relation_type, obj, object_type)
        if relation_key in relation_keys:
            duplicate_relations += 1
            existing_attributes = relation_records[relation_key]["attributes"]
            for attribute_name, attribute_value in attributes.items():
                if attribute_name not in existing_attributes:
                    existing_attributes[attribute_name] = attribute_value
            continue
        relation_keys.add(relation_key)
        relation_record = {
            "subject": subject,
            "subject_type": subject_type,
            "relation": relation_type,
            "object": obj,
            "object_type": object_type,
            "attributes": _with_provenance(attributes, context),
        }
        relation_records[relation_key] = relation_record
        accepted_relations.append(relation_record)

    rejected_entities = sum(1 for issue in rejected if issue["record_type"] == "entity")
    rejected_relations = sum(
        1 for issue in rejected if issue["record_type"] == "relation"
    )
    isolated_rate, component_ratio = _connectivity_metrics(
        accepted_entities, accepted_relations
    )
    source_covered = sum(
        1
        for relation in accepted_relations
        if relation["attributes"].get("source_id") == context["source_id"]
    )
    accepted_records = [*accepted_entities, *accepted_relations]
    confidence_covered = sum(
        1 for record in accepted_records if "confidence" in record["attributes"]
    )
    metrics: GraphQualityMetrics = {
        "input_entities": len(entity_items),
        "accepted_entities": len(accepted_entities),
        "rejected_entities": rejected_entities,
        "duplicate_entities": duplicate_entities,
        "duplicate_entity_rate": _ratio(duplicate_entities, len(entity_items)),
        "input_relations": len(relation_items),
        "accepted_relations": len(accepted_relations),
        "rejected_relations": rejected_relations,
        "duplicate_relations": duplicate_relations,
        "duplicate_relation_rate": _ratio(duplicate_relations, len(relation_items)),
        "isolated_node_rate": isolated_rate,
        "largest_connected_component_ratio": component_ratio,
        "relationship_source_coverage_rate": _ratio(
            source_covered, len(accepted_relations)
        ),
        "confidence_coverage_rate": _ratio(confidence_covered, len(accepted_records)),
    }
    return {
        "entities": accepted_entities,
        "relations": accepted_relations,
        "rejected": rejected,
        "metrics": metrics,
    }
