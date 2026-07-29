"""文档解析、分块与 embedding 的版本化身份。"""

from __future__ import annotations

import hashlib
import json


DOCUMENT_PARSER_VERSION = 1
DOCUMENT_CHUNK_STRATEGY_VERSION = 1
DOCUMENT_EMBEDDING_PIPELINE_VERSION = 1
DOCUMENT_EMBEDDING_DENSE_DIM = 1024


def build_embedding_model_signature(model_path: str) -> str:
    """根据模型来源和 embedding 管线版本生成稳定签名。"""
    encoded = json.dumps(
        {
            "pipeline_version": DOCUMENT_EMBEDDING_PIPELINE_VERSION,
            "model_path": model_path.replace("\\", "/").rstrip("/"),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def build_document_chunk_identity(
    *,
    file_hash: str,
    chunk_index: int,
    text: str,
    parent_chunk_size: int,
    sub_chunk_size: int,
    parent_chunk_overlap: int,
    sub_chunk_overlap: int,
    embedding_model_signature: str,
) -> tuple[int, str]:
    """生成同时绑定文件、分块和模型版本的 Milvus 主键与 chunk ID。"""
    encoded = json.dumps(
        {
            "file_hash": file_hash,
            "parser_version": DOCUMENT_PARSER_VERSION,
            "chunk_strategy_version": DOCUMENT_CHUNK_STRATEGY_VERSION,
            "chunk_index": chunk_index,
            "text_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "parent_chunk_size": parent_chunk_size,
            "sub_chunk_size": sub_chunk_size,
            "parent_chunk_overlap": parent_chunk_overlap,
            "sub_chunk_overlap": sub_chunk_overlap,
            "embedding_model_signature": embedding_model_signature,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(encoded.encode("utf-8")).digest()
    document_id = int.from_bytes(digest[:8], "big") & ((1 << 63) - 1)
    return document_id if document_id > 0 else 1, digest.hex()
