"""Milvus 集合 schema 版本与兼容性校验。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

from pymilvus import DataType  # type: ignore[import-untyped]


@dataclass(frozen=True)
class MilvusFieldContract:
    """单个 Milvus 字段的稳定约束。"""

    name: str
    type_code: int
    params: tuple[tuple[str, int], ...] = ()
    is_primary: bool = False
    auto_id: bool = False


@dataclass(frozen=True)
class MilvusSchemaContract:
    """一个版本化 Milvus 集合 schema。"""

    component: str
    version: int
    auto_id: bool
    enable_dynamic_field: bool
    fields: tuple[MilvusFieldContract, ...]

    @property
    def fingerprint(self) -> str:
        """返回 schema 合同的稳定指纹。"""
        payload = {
            "component": self.component,
            "version": self.version,
            "auto_id": self.auto_id,
            "enable_dynamic_field": self.enable_dynamic_field,
            "fields": [
                {
                    "name": field.name,
                    "type_code": field.type_code,
                    "params": list(field.params),
                    "is_primary": field.is_primary,
                    "auto_id": field.auto_id,
                }
                for field in self.fields
            ],
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _field(
    name: str,
    data_type: DataType,
    *,
    params: tuple[tuple[str, int], ...] = (),
    is_primary: bool = False,
    auto_id: bool = False,
) -> MilvusFieldContract:
    return MilvusFieldContract(
        name=name,
        type_code=int(data_type),
        params=params,
        is_primary=is_primary,
        auto_id=auto_id,
    )


def document_milvus_schema(dense_dim: int) -> MilvusSchemaContract:
    """返回文档检索集合 v1 schema。"""
    return MilvusSchemaContract(
        component="document_retrieval",
        version=1,
        auto_id=False,
        enable_dynamic_field=True,
        fields=(
            _field("id", DataType.INT64, is_primary=True),
            _field("text", DataType.VARCHAR, params=(("max_length", 65535),)),
            _field(
                "dense_vector",
                DataType.FLOAT_VECTOR,
                params=(("dim", dense_dim),),
            ),
            _field("sparse_vector", DataType.SPARSE_FLOAT_VECTOR),
            _field("parent_id", DataType.VARCHAR, params=(("max_length", 100),)),
            _field(
                "parent_content",
                DataType.VARCHAR,
                params=(("max_length", 65535),),
            ),
            _field("source", DataType.VARCHAR, params=(("max_length", 50),)),
            _field("timestamp", DataType.VARCHAR, params=(("max_length", 50),)),
            _field("page_num", DataType.INT64),
            _field("start_page", DataType.INT64),
            _field("end_page", DataType.INT64),
        ),
    )


KG_MILVUS_SCHEMA = MilvusSchemaContract(
    component="knowledge_graph_retrieval",
    version=1,
    auto_id=True,
    enable_dynamic_field=False,
    fields=(
        _field("pk_id", DataType.INT64, is_primary=True, auto_id=True),
        _field("doc_id", DataType.VARCHAR, params=(("max_length", 64),)),
        _field("item_type", DataType.VARCHAR, params=(("max_length", 32),)),
        _field("name", DataType.VARCHAR, params=(("max_length", 512),)),
        _field("label", DataType.VARCHAR, params=(("max_length", 128),)),
        _field("source_name", DataType.VARCHAR, params=(("max_length", 512),)),
        _field("source_label", DataType.VARCHAR, params=(("max_length", 128),)),
        _field("relation_type", DataType.VARCHAR, params=(("max_length", 128),)),
        _field("target_name", DataType.VARCHAR, params=(("max_length", 512),)),
        _field("target_label", DataType.VARCHAR, params=(("max_length", 128),)),
        _field("text", DataType.VARCHAR, params=(("max_length", 65535),)),
        _field("timestamp", DataType.VARCHAR, params=(("max_length", 64),)),
        _field("embedding", DataType.FLOAT_VECTOR, params=(("dim", 1024),)),
    ),
)


def validate_milvus_schema(
    description: dict[str, Any],
    contract: MilvusSchemaContract,
) -> str:
    """校验实际集合 schema，兼容时返回合同指纹。"""
    errors: list[str] = []
    if bool(description.get("auto_id")) != contract.auto_id:
        errors.append("auto_id")
    if bool(description.get("enable_dynamic_field")) != contract.enable_dynamic_field:
        errors.append("enable_dynamic_field")
    raw_fields = description.get("fields")
    actual_fields = (
        {
            str(field.get("name") or field.get("field_name")): field
            for field in raw_fields
            if isinstance(field, dict)
        }
        if isinstance(raw_fields, list)
        else {}
    )
    expected_names = {field.name for field in contract.fields}
    actual_names = set(actual_fields)
    for name in sorted(expected_names - actual_names):
        errors.append(f"missing_field:{name}")
    for name in sorted(actual_names - expected_names):
        errors.append(f"unexpected_field:{name}")
    for expected in contract.fields:
        actual = actual_fields.get(expected.name)
        if actual is None:
            continue
        actual_type = actual.get("type")
        if actual_type is None or int(actual_type) != expected.type_code:
            errors.append(f"field_type:{expected.name}")
        params = actual.get("params")
        params = params if isinstance(params, dict) else {}
        for key, value in expected.params:
            actual_value = params.get(key)
            if actual_value is None or int(actual_value) != value:
                errors.append(f"field_param:{expected.name}:{key}")
        if bool(actual.get("is_primary")) != expected.is_primary:
            errors.append(f"field_primary:{expected.name}")
        if bool(actual.get("auto_id")) != expected.auto_id:
            errors.append(f"field_auto_id:{expected.name}")
    if errors:
        raise RuntimeError(
            "milvus_schema_mismatch:"
            f"component={contract.component}:version={contract.version}:"
            f"errors={','.join(errors)}"
        )
    return contract.fingerprint


def describe_and_validate_milvus_schema(
    client: Any,
    *,
    collection_name: str,
    contract: MilvusSchemaContract,
) -> str:
    """读取并校验指定集合 schema。"""
    try:
        description = client.describe_collection(collection_name=collection_name)
    except TypeError:
        description = client.describe_collection(collection_name)
    if not isinstance(description, dict):
        raise RuntimeError(f"milvus_collection_description_invalid:{collection_name}")
    return validate_milvus_schema(description, contract)
