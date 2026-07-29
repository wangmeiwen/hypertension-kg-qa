"""图谱导入解析缓存组件。"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Callable, Optional
from uuid import uuid4

from app.core.concurrency import concurrency_guard

logger = logging.getLogger(__name__)


class GraphImportCacheMixin:
    """提供文件哈希、缓存校验和原子写入能力。"""

    GRAPH_IMPORT_CACHE_VERSION: int
    GRAPH_IMPORT_CHUNK_LIMIT: int
    GRAPH_IMPORT_CHUNK_STRATEGY_VERSION: int
    _utc_now_iso: Callable[[], str]

    @staticmethod
    def _compute_file_hash(file_bytes: bytes) -> str:
        return hashlib.sha256(file_bytes).hexdigest()

    @classmethod
    def _get_chunk_strategy_signature(cls) -> str:
        return f"v{cls.GRAPH_IMPORT_CHUNK_STRATEGY_VERSION}:limit={cls.GRAPH_IMPORT_CHUNK_LIMIT}"

    @classmethod
    def _get_graph_import_cache_dir(cls) -> Path:
        return Path(__file__).resolve().parents[3] / "runtime_cache" / "graph_import"

    @classmethod
    def _get_graph_import_cache_path(cls, file_hash: str) -> Path:
        return cls._get_graph_import_cache_dir() / f"{file_hash}.json.gz"

    @staticmethod
    def _json_dumps(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _json_loads(value: Any, default: Any) -> Any:
        if value in (None, ""):
            return default
        try:
            return json.loads(str(value))
        except (TypeError, ValueError, json.JSONDecodeError):
            return default

    def _load_graph_import_cache(
        self,
        file_hash: str,
    ) -> tuple[Optional[dict[str, Any]], Optional[str]]:
        cache_path = self._get_graph_import_cache_path(file_hash)
        if not cache_path.exists():
            return None, None
        try:
            with concurrency_guard.sync_keyed_lock(f"graph-import-cache:{file_hash}"):
                with gzip.open(cache_path, "rt", encoding="utf-8") as handle:
                    payload = json.load(handle)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            logger.warning(
                "读取图谱导入缓存失败，将忽略缓存: path=%s error=%s", cache_path, exc
            )
            return None, f"本地缓存读取失败，已忽略: {exc}"

        invalidation_reason = self._get_cache_invalidation_reason(payload, file_hash)
        if invalidation_reason:
            return None, invalidation_reason
        prepared = payload.get("prepared")
        chunks = payload.get("chunks")
        if not isinstance(prepared, dict) or not isinstance(chunks, list):
            return None, "本地缓存内容不完整，已重新解析"
        prepared = dict(prepared)
        prepared["_chunks"] = [str(chunk or "") for chunk in chunks]
        prepared["cache_used"] = True
        prepared["cache_path"] = str(cache_path)
        prepared["cache_invalidated_reason"] = None
        prepared["chunk_limit"] = int(payload["chunk_limit"])
        prepared["chunk_strategy_version"] = int(payload["chunk_strategy_version"])
        return prepared, None

    @classmethod
    def _get_cache_invalidation_reason(
        cls,
        payload: dict[str, Any],
        file_hash: str,
    ) -> Optional[str]:
        if int(payload.get("version") or 0) != cls.GRAPH_IMPORT_CACHE_VERSION:
            return "本地缓存版本已过期，已重新解析"
        if int(payload.get("chunk_limit") or 0) != cls.GRAPH_IMPORT_CHUNK_LIMIT:
            return "本地缓存分块大小与当前策略不一致，已重新分块"
        if (
            int(payload.get("chunk_strategy_version") or 0)
            != cls.GRAPH_IMPORT_CHUNK_STRATEGY_VERSION
        ):
            return "本地缓存分块策略已过期，已重新解析"
        if str(payload.get("file_hash") or "") != file_hash:
            return "本地缓存文件哈希不匹配，已重新解析"
        return None

    def _write_graph_import_cache(
        self,
        file_hash: str,
        prepared: dict[str, Any],
        chunks: list[str],
    ) -> None:
        cache_dir = self._get_graph_import_cache_dir()
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = self._get_graph_import_cache_path(file_hash)
        payload = {
            "version": self.GRAPH_IMPORT_CACHE_VERSION,
            "file_hash": file_hash,
            "chunk_limit": self.GRAPH_IMPORT_CHUNK_LIMIT,
            "chunk_strategy_version": self.GRAPH_IMPORT_CHUNK_STRATEGY_VERSION,
            "cached_at": self._utc_now_iso(),
            "prepared": {
                "file_name": prepared.get("file_name"),
                "parsed_text_length": prepared.get("parsed_text_length") or 0,
                "total_chunk_chars": prepared.get("total_chunk_chars") or 0,
                "processed_text_length": 0,
                "parser_used": prepared.get("parser_used"),
                "ocr_engine": prepared.get("ocr_engine"),
                "parse_warning": prepared.get("parse_warning") or "",
                "docling_failed_pages": list(
                    prepared.get("docling_failed_pages") or []
                ),
                "pdf_fallback_used": bool(prepared.get("pdf_fallback_used")),
                "chunk_count": prepared.get("chunk_count") or 0,
                "entity_count": 0,
                "relation_count": 0,
                "node_batches": 0,
                "relation_batches": 0,
                "message": prepared.get("message") or "",
                "chunk_limit": self.GRAPH_IMPORT_CHUNK_LIMIT,
                "chunk_strategy_version": self.GRAPH_IMPORT_CHUNK_STRATEGY_VERSION,
            },
            "chunks": chunks,
        }
        tmp_path = cache_path.with_name(f"{cache_path.name}.{uuid4().hex}.tmp")
        with concurrency_guard.sync_keyed_lock(f"graph-import-cache:{file_hash}"):
            try:
                with gzip.open(tmp_path, "wt", encoding="utf-8") as handle:
                    json.dump(payload, handle, ensure_ascii=False)
                tmp_path.replace(cache_path)
            finally:
                try:
                    if tmp_path.exists():
                        tmp_path.unlink()
                except OSError:
                    pass
