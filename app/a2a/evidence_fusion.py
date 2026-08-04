"""跨代理 Evidence 去重、来源归一化与支持度融合。"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Iterable

from pydantic import ValidationError

from app.a2a.models import Evidence

_PUBLICATION_REGISTRY: tuple[tuple[str, frozenset[str]], ...] = (
    (
        "china_hypertension_guideline_2024",
        frozenset(
            {
                "中国高血压防治指南2024pdf",
                "中国高血压防治指南2024",
                "中国指南2024",
                "guidelinepdf2024",
                "guideline_pdf_2024",
                "669d02309aec874e5f707ff3a2c23399619d8d041e1608f19d8dd81ae6819f93",  # pragma: allowlist secret
            }
        ),
    ),
)


@dataclass(frozen=True)
class EvidenceFusionResult:
    """一次证据融合的确定性结果。"""

    evidence: list[Evidence]
    raw_evidence_count: int
    duplicate_count: int
    distinct_publication_count: int
    corroborated_claim_count: int


def _normalized_text(value: str) -> str:
    """生成用于比对的稳定文本。"""
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", value.casefold())


def _content_fingerprint(evidence: Evidence) -> str:
    """生成证据内容指纹。"""
    normalized = _normalized_text(evidence.content)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def legacy_evidence_identity(evidence: Evidence) -> str:
    """生成 evidence.v2 使用的对象身份，仅用于兼容读取。"""
    source_type = _normalized_text(evidence.source_type) or "unknown"
    source_id = _normalized_text(evidence.source_id)
    if source_id:
        return f"evidence:{source_type}:{source_id}"
    return f"evidence:{source_type}:content:{_content_fingerprint(evidence)}"


def evidence_identity(evidence: Evidence) -> str:
    """生成包含出版物、locator 与内容指纹的 chunk 级稳定身份。"""
    source_type = _normalized_text(evidence.source_type) or "unknown"
    publication = publication_identity(evidence) or "publication:unknown"
    page = evidence.source_page if evidence.source_page is not None else evidence.page
    chunk = _normalized_text(evidence.source_chunk_id or evidence.chunk_id) or "none"
    object_id = _normalized_text(evidence.source_id) or "none"
    fingerprint = _content_fingerprint(evidence)
    return (
        f"evidence:v3:{source_type}:{publication}:page:{page if page is not None else 'none'}:"
        f"chunk:{chunk}:object:{object_id}:content:{fingerprint}"
    )


def evidence_identity_index(
    evidence_rows: Iterable[Evidence],
) -> dict[str, Evidence]:
    """构建身份索引；旧身份只有在唯一指向一个 chunk 时才可兼容读取。"""
    evidence = list(evidence_rows)
    indexed: dict[str, Evidence] = {}
    for item in evidence:
        identity = evidence_identity(item)
        existing = indexed.get(identity)
        if existing is not None and _content_fingerprint(
            existing
        ) != _content_fingerprint(item):
            raise ValueError(f"evidence_identity_collision:{identity}")
        indexed[identity] = item

    legacy_targets: dict[str, set[str]] = {}
    for item in evidence:
        legacy_targets.setdefault(legacy_evidence_identity(item), set()).add(
            evidence_identity(item)
        )
    for legacy_identity, targets in legacy_targets.items():
        if len(targets) == 1:
            indexed[legacy_identity] = indexed[next(iter(targets))]
    return indexed


def _publication_aliases(evidence: Evidence) -> set[str]:
    aliases: set[str] = set()
    registry_identity = _registry_publication_identity(evidence)
    if registry_identity:
        aliases.add(registry_identity)
    normalized_hash = _normalized_text(evidence.source_hash)
    if normalized_hash:
        aliases.add(f"publication:hash:{normalized_hash}")
    for value in (evidence.origin_guideline_id, evidence.origin_document_id):
        normalized_id = _normalized_text(value)
        if normalized_id:
            aliases.add(f"publication:id:{normalized_id}")
    return aliases


def _registry_publication_identity(evidence: Evidence) -> str | None:
    raw_obj = evidence.provenance.get("raw")
    raw = raw_obj if isinstance(raw_obj, dict) else {}
    candidates = {
        _normalized_text(value)
        for value in (
            evidence.document_id,
            evidence.origin_guideline_id,
            evidence.origin_document_id,
            evidence.source_hash,
            str(raw.get("source_name") or ""),
        )
        if str(value or "").strip()
    }
    for registry_id, aliases in _PUBLICATION_REGISTRY:
        normalized_aliases = {_normalized_text(alias) for alias in aliases}
        if candidates.intersection(normalized_aliases):
            return f"publication:registry:{registry_id}"
    return None


def publication_identity(evidence: Evidence) -> str | None:
    """仅使用可信来源字段生成出版物身份。"""
    canonical = str(
        evidence.provenance.get("canonical_publication_identity") or ""
    ).strip()
    if canonical.startswith(
        ("publication:registry:", "publication:hash:", "publication:id:")
    ):
        return canonical
    registry_identity = _registry_publication_identity(evidence)
    if registry_identity:
        return registry_identity
    candidates = (
        ("hash", evidence.source_hash),
        ("id", evidence.origin_guideline_id),
        ("id", evidence.origin_document_id),
    )
    for prefix, value in candidates:
        normalized = _normalized_text(value)
        if normalized:
            return f"publication:{prefix}:{normalized}"
    return None


def _canonicalize_publications(
    rows: list[tuple[str, Evidence]],
) -> list[tuple[str, Evidence]]:
    """通过同一 Evidence 同时携带的 hash/id 将跨检索器来源别名合并。"""
    parent: dict[str, str] = {}

    def find(alias: str) -> str:
        parent.setdefault(alias, alias)
        while parent[alias] != alias:
            parent[alias] = parent[parent[alias]]
            alias = parent[alias]
        return alias

    def union(left: str, right: str) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    aliases_by_row: list[set[str]] = []
    for _, evidence in rows:
        aliases = _publication_aliases(evidence)
        aliases_by_row.append(aliases)
        ordered = sorted(aliases)
        for alias in ordered:
            find(alias)
        for alias in ordered[1:]:
            union(ordered[0], alias)

    components: dict[str, set[str]] = {}
    for alias in parent:
        components.setdefault(find(alias), set()).add(alias)
    canonical_by_alias: dict[str, str] = {}
    for aliases in components.values():
        canonical = min(
            aliases,
            key=lambda value: (
                (
                    0
                    if value.startswith("publication:registry:")
                    else 1 if value.startswith("publication:hash:") else 2
                ),
                value,
            ),
        )
        canonical_by_alias.update({alias: canonical for alias in aliases})

    canonicalized: list[tuple[str, Evidence]] = []
    for (agent, evidence), aliases in zip(rows, aliases_by_row):
        normalized = evidence.model_copy(deep=True)
        if aliases:
            first_alias = min(aliases)
            normalized.provenance = {
                **normalized.provenance,
                "canonical_publication_identity": canonical_by_alias[first_alias],
            }
        canonicalized.append((agent, normalized))
    return canonicalized


def _deduplication_key(evidence: Evidence) -> tuple[str, int | None, str, str]:
    """构建同一来源内的精确去重键。"""
    return (
        publication_identity(evidence) or evidence_identity(evidence),
        evidence.source_page,
        _normalized_text(evidence.section),
        _content_fingerprint(evidence),
    )


def _validated_rows(
    rows: Iterable[tuple[str, Evidence | dict[str, Any]]],
) -> list[tuple[str, Evidence]]:
    """过滤不满足共享 Evidence 契约的输入。"""
    validated: list[tuple[str, Evidence]] = []
    for agent, row in rows:
        try:
            evidence = (
                row if isinstance(row, Evidence) else Evidence.model_validate(row)
            )
        except ValidationError:
            continue
        if not evidence.content.strip():
            continue
        validated.append((str(agent).strip() or "unknown", evidence))
    return validated


def fuse_evidence(
    rows: Iterable[tuple[str, Evidence | dict[str, Any]]],
) -> EvidenceFusionResult:
    """融合跨 KG、RAG、历史读取的 Evidence。"""
    validated = _canonicalize_publications(_validated_rows(rows))
    grouped: dict[tuple[str, int | None, str, str], list[tuple[str, Evidence]]] = {}
    for agent, evidence in validated:
        grouped.setdefault(_deduplication_key(evidence), []).append((agent, evidence))

    claim_publications: dict[str, set[str]] = {}
    for _, evidence in validated:
        publication = publication_identity(evidence)
        if publication is not None:
            claim_publications.setdefault(_content_fingerprint(evidence), set()).add(
                publication
            )

    fused: list[Evidence] = []
    for duplicates in grouped.values():
        agents = sorted({agent for agent, _ in duplicates})
        evidence_rows = [evidence for _, evidence in duplicates]
        representative = evidence_rows[0].model_copy(deep=True)
        scores = [item.score for item in evidence_rows if item.score is not None]
        if scores:
            representative.score = max(scores)
        methods = sorted(
            {
                item.retrieval_method
                for item in evidence_rows
                if item.retrieval_method.strip()
            }
        )
        fingerprint = _content_fingerprint(representative)
        publication_ids = sorted(claim_publications.get(fingerprint, set()))
        representative.provenance = {
            **representative.provenance,
            "evidence_identity": evidence_identity(representative),
            "publication_identity": publication_identity(representative),
            "supporting_agents": agents,
            "retrieval_methods": methods,
            "duplicate_count": len(evidence_rows) - 1,
            "supporting_publication_ids": publication_ids,
            "distinct_publication_count": len(publication_ids),
            "unknown_origin": not publication_ids,
        }
        fused.append(representative)

    distinct_publications = {
        identity
        for item in fused
        if (identity := publication_identity(item)) is not None
    }
    corroborated_claims = sum(
        1
        for publication_ids in claim_publications.values()
        if len(publication_ids) >= 2
    )
    return EvidenceFusionResult(
        evidence=fused,
        raw_evidence_count=len(validated),
        duplicate_count=len(validated) - len(fused),
        distinct_publication_count=len(distinct_publications),
        corroborated_claim_count=corroborated_claims,
    )
