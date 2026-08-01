from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

from langchain_core.documents import Document
import numpy as np
import pytest

from app.rag_core.document_embedding import DocumentEmbedder
from app.rag_core.document_pipeline import (
    DOCUMENT_CHUNK_STRATEGY_VERSION,
    DOCUMENT_EMBEDDING_PIPELINE_VERSION,
    DOCUMENT_PARSER_VERSION,
    build_document_chunk_identity,
    build_embedding_model_signature,
)
from app.rag_core.vector_store import VectorStore


def _identity(*, model_signature: str, sub_chunk_size: int = 256) -> tuple[int, str]:
    return build_document_chunk_identity(
        file_hash="a" * 64,
        chunk_index=0,
        text="家庭血压达到或超过135/85 mmHg。",
        parent_chunk_size=1024,
        sub_chunk_size=sub_chunk_size,
        parent_chunk_overlap=100,
        sub_chunk_overlap=50,
        embedding_model_signature=model_signature,
    )


def test_document_chunk_identity_is_stable_and_version_bound() -> None:
    model_signature = build_embedding_model_signature("model/bge-m3")
    same_model_signature = build_embedding_model_signature("model\\bge-m3\\")

    first = _identity(model_signature=model_signature)
    second = _identity(model_signature=same_model_signature)
    changed_model = _identity(
        model_signature=build_embedding_model_signature("model/bge-m3-v2")
    )
    changed_chunking = _identity(
        model_signature=model_signature,
        sub_chunk_size=512,
    )

    assert first == second
    assert first != changed_model
    assert first != changed_chunking
    assert 0 < first[0] < 2**63
    assert len(first[1]) == 64


def test_document_embedder_replaces_same_source_with_deterministic_ids() -> None:
    events: list[tuple[str, Any]] = []

    class VectorStoreStub:
        def add_documents(self, documents) -> None:
            events.append(("add", documents))

        def delete_obsolete_by_source_hash(
            self,
            source_hash: str,
            *,
            keep_ids: list[int],
        ) -> None:
            events.append(("delete", (source_hash, keep_ids)))

    embedder = DocumentEmbedder(vector_store=VectorStoreStub())  # type: ignore[arg-type]
    parsed = SimpleNamespace(
        format="pdf",
        pages=[1],
        tables=[],
        images=[],
    )
    source_hash = "b" * 64
    sub_chunks = [
        {
            "text": "家庭血压达到或超过135/85 mmHg。",
            "parent_index": 0,
            "sub_index": 0,
            "parent_text": "家庭血压诊断标准。",
            "page_num": 1,
            "start_page": 1,
            "end_page": 1,
        }
    ]

    result = embedder.embed_docling_sub_chunks(
        sub_chunks,
        "guide.pdf",
        parsed,  # type: ignore[arg-type]
        source_hash=source_hash,
    )

    assert [event[0] for event in events] == ["add", "delete"]
    document = events[0][1][0]
    assert document.metadata["source_hash"] == source_hash
    assert isinstance(document.metadata["id"], int)
    assert document.metadata["parser_version"] == DOCUMENT_PARSER_VERSION
    assert (
        document.metadata["chunk_strategy_version"] == DOCUMENT_CHUNK_STRATEGY_VERSION
    )
    assert (
        document.metadata["embedding_pipeline_version"]
        == DOCUMENT_EMBEDDING_PIPELINE_VERSION
    )
    assert events[1][1] == (source_hash, [document.metadata["id"]])
    assert result["source_hash"] == source_hash


def test_vector_store_delete_rejects_untrusted_source_hash() -> None:
    store = object.__new__(VectorStore)

    with pytest.raises(ValueError, match="invalid_document_source_hash"):
        store.delete_obsolete_by_source_hash('a" or true', keep_ids=[1])
    with pytest.raises(ValueError, match="invalid_document_keep_ids"):
        store.delete_obsolete_by_source_hash("a" * 64, keep_ids=[])


def test_vector_store_persists_versioned_dynamic_metadata() -> None:
    captured: list[dict[str, Any]] = []

    class ClientStub:
        def upsert(self, *, collection_name: str, data: list[dict[str, Any]]) -> None:
            assert collection_name == "documents"
            captured.extend(data)

    store = object.__new__(VectorStore)
    store.collection_name = "documents"
    store.collection_fields = {"dense_vector", "sparse_vector"}
    store.dense_dim = 2
    store.logger = logging.getLogger(__name__)
    store.client = ClientStub()
    store.embedding_function = lambda _texts: {
        "dense": np.asarray([[0.1, 0.2]], dtype=np.float32),
        "sparse": np.asarray([[0.0, 0.5]], dtype=np.float32),
    }

    store.add_documents(
        [
            Document(
                page_content="家庭血压标准。",
                metadata={
                    "id": 7,
                    "source": "guide.pdf",
                    "source_hash": "c" * 64,
                    "chunk_id": "chunk-1",
                    "parser_version": 1,
                    "chunk_strategy_version": 1,
                    "embedding_pipeline_version": 1,
                    "embedding_model_signature": "model-signature",
                },
            )
        ]
    )

    assert captured[0]["id"] == 7
    assert captured[0]["source_hash"] == "c" * 64
    assert captured[0]["chunk_id"] == "chunk-1"
    assert captured[0]["embedding_model_signature"] == "model-signature"
