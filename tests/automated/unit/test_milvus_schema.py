from __future__ import annotations

from typing import Any

import pytest

from app.rag_core.milvus_schema import (
    KG_MILVUS_SCHEMA,
    MilvusSchemaContract,
    describe_and_validate_milvus_schema,
    document_milvus_schema,
    validate_milvus_schema,
)


def _description(contract: MilvusSchemaContract) -> dict[str, Any]:
    return {
        "auto_id": contract.auto_id,
        "enable_dynamic_field": contract.enable_dynamic_field,
        "fields": [
            {
                "name": field.name,
                "type": field.type_code,
                "params": dict(field.params),
                "is_primary": field.is_primary,
                "auto_id": field.auto_id,
            }
            for field in contract.fields
        ],
    }


def test_milvus_schema_contract_accepts_exact_document_and_kg_schemas() -> None:
    document_contract = document_milvus_schema(1024)

    assert (
        validate_milvus_schema(_description(document_contract), document_contract)
        == document_contract.fingerprint
    )
    assert (
        validate_milvus_schema(_description(KG_MILVUS_SCHEMA), KG_MILVUS_SCHEMA)
        == KG_MILVUS_SCHEMA.fingerprint
    )
    assert document_contract.version == 1
    assert KG_MILVUS_SCHEMA.version == 1


@pytest.mark.parametrize(
    ("mutate", "expected_error"),
    [
        (
            lambda description: description["fields"].pop(),
            "missing_field:end_page",
        ),
        (
            lambda description: description["fields"][2]["params"].update({"dim": 768}),
            "field_param:dense_vector:dim",
        ),
        (
            lambda description: description["fields"].append(
                {"name": "future_field", "type": 21, "params": {}}
            ),
            "unexpected_field:future_field",
        ),
    ],
)
def test_milvus_schema_contract_rejects_incompatible_collections(
    mutate,
    expected_error: str,
) -> None:
    contract = document_milvus_schema(1024)
    description = _description(contract)
    mutate(description)

    with pytest.raises(RuntimeError, match=expected_error):
        validate_milvus_schema(description, contract)


def test_milvus_schema_description_supports_legacy_positional_client() -> None:
    contract = document_milvus_schema(1024)

    class ClientStub:
        def describe_collection(self, *args, **kwargs):
            if kwargs:
                raise TypeError("legacy_signature")
            assert args == ("documents",)
            return _description(contract)

    assert (
        describe_and_validate_milvus_schema(
            ClientStub(),
            collection_name="documents",
            contract=contract,
        )
        == contract.fingerprint
    )
