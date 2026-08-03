"""Claim 与 Evidence 支持关系校验。"""

from __future__ import annotations

import json
import re
from typing import Any, cast

from app.a2a.evidence_fusion import evidence_identity, publication_identity
from app.a2a.models import (
    ClaimEvidenceAssessment,
    ClaimSupportStatus,
    Evidence,
    MustCoverItem,
    RetrievalTask,
)
from app.a2a.numeric_claims import (
    extract_numeric_requirements,
    extract_structured_numeric_claims,
    numeric_claim_matches_requirement,
)
from app.a2a.relation_claims import relation_direction, relation_endpoints
from app.a2a.telemetry import empty_token_usage

_INTERVAL_PATTERN = re.compile(r"\d+(?:\.\d+)?\s*(?:-|—|~|～|至|到)\s*\d+(?:\.\d+)?")
_UPPER_BOUND_PATTERN = re.compile(r"(?:≤|<=|＜|<|低于|小于|不超过|以下)")
_LOWER_BOUND_PATTERN = re.compile(r"(?:≥|>=|＞|>|高于|大于|至少|不低于|以上)")
_RELATION_KINDS = {"relationship", "relation", "path", "multi_hop"}
_CLAIM_SUPPORT_STATUSES: set[str] = {
    "supported",
    "partially_supported",
    "contradicted",
    "irrelevant",
    "uncertain",
}
_GROUNDING_CHAR_MAP = str.maketrans(
    {
        "，": ",",
        "。": ".",
        "：": ":",
        "；": ";",
        "（": "(",
        "）": ")",
        "＞": ">",
        "＜": "<",
        "～": "~",
        "—": "-",
    }
)


def _matched_applicability(must_cover: MustCoverItem, evidence: Evidence) -> list[str]:
    def normalize(value: str) -> str:
        normalized = re.sub(r"\s+", "", value.casefold())
        normalized = normalized.replace("家庭测血压", "家庭血压")
        normalized = normalized.replace("居家血压", "家庭血压")
        normalized = normalized.replace("hbpm", "家庭血压")
        normalized = normalized.replace("abpm", "动态血压")
        normalized = re.sub(r"岁及以上", "岁以上", normalized)
        normalized = re.sub(r"岁及以下", "岁以下", normalized)
        normalized = re.sub(r"≥(\d{1,3})岁", r"\1岁以上", normalized)
        normalized = re.sub(r"≤(\d{1,3})岁", r"\1岁以下", normalized)
        return normalized

    text = normalize(evidence.content)
    return [
        condition
        for condition in must_cover.applicability_conditions
        if normalize(condition) in text
    ]


def _numeric_shape_reason(question: str, content: str) -> str | None:
    """校验问题明确要求的区间或上下界表达。"""
    if any(
        term in question for term in ("范围", "区间")
    ) and not _INTERVAL_PATTERN.search(content):
        return "numeric_interval_missing"
    question_without_age = re.sub(r"\d{1,3}\s*岁(?:及?以上|及?以下)?", "", question)
    if _UPPER_BOUND_PATTERN.search(
        question_without_age
    ) and not _UPPER_BOUND_PATTERN.search(content):
        return "numeric_upper_bound_missing"
    if _LOWER_BOUND_PATTERN.search(
        question_without_age
    ) and not _LOWER_BOUND_PATTERN.search(content):
        return "numeric_lower_bound_missing"
    return None


def _assessment(
    *,
    task: RetrievalTask,
    must_cover: MustCoverItem,
    evidence: Evidence,
    support_status: ClaimSupportStatus,
    reason: str,
    support_excerpt: str = "",
    applicability_conditions: list[str] | None = None,
    verifier_version: str = "claim_support.deterministic.v1",
) -> ClaimEvidenceAssessment:
    return ClaimEvidenceAssessment(
        task_id=task.task_id,
        must_cover_id=must_cover.item_id,
        evidence_id=evidence_identity(evidence),
        support_status=support_status,
        support_excerpt=support_excerpt or evidence.content[:500],
        applicability_conditions=list(applicability_conditions or []),
        reason=reason,
        verifier_version=verifier_version,
    )


def deterministic_claim_support(
    *,
    task: RetrievalTask,
    must_cover: MustCoverItem,
    evidence: Evidence,
) -> ClaimEvidenceAssessment | None:
    """对可结构化判断的 claim 返回高精度支持状态。"""
    content = evidence.content.strip()
    if not content:
        return _assessment(
            task=task,
            must_cover=must_cover,
            evidence=evidence,
            support_status="irrelevant",
            reason="empty_evidence_content",
        )

    matched_applicability = _matched_applicability(must_cover, evidence)
    required_applicability = set(
        must_cover.evidence_policy.required_applicability_fields
    )
    applicability_complete = required_applicability.issubset(matched_applicability)

    if must_cover.claim_type in {"numeric", "comparison"}:
        requirements = extract_numeric_requirements(must_cover.question)
        claims = extract_structured_numeric_claims(content)
        if not requirements:
            return _assessment(
                task=task,
                must_cover=must_cover,
                evidence=evidence,
                support_status="partially_supported",
                reason="numeric_requirement_unresolved",
                applicability_conditions=matched_applicability,
            )
        matched_requirements = [
            requirement
            for requirement in requirements
            if any(
                numeric_claim_matches_requirement(claim, requirement)
                for claim in claims
            )
        ]
        if not matched_requirements:
            return _assessment(
                task=task,
                must_cover=must_cover,
                evidence=evidence,
                support_status="irrelevant",
                reason="numeric_claim_value_missing",
                applicability_conditions=matched_applicability,
            )
        shape_reason = _numeric_shape_reason(must_cover.question, content)
        if (
            len(matched_requirements) != len(requirements)
            or shape_reason is not None
            or not applicability_complete
        ):
            return _assessment(
                task=task,
                must_cover=must_cover,
                evidence=evidence,
                support_status="partially_supported",
                reason=(
                    "numeric_semantic_slot_missing"
                    if len(matched_requirements) != len(requirements)
                    else shape_reason or "applicability_condition_missing"
                ),
                applicability_conditions=matched_applicability,
            )
        return _assessment(
            task=task,
            must_cover=must_cover,
            evidence=evidence,
            support_status="supported",
            reason="numeric_semantic_slots_and_applicability_matched",
            applicability_conditions=matched_applicability,
        )

    if must_cover.claim_type == "relation":
        if evidence.source_type != "knowledge_graph":
            return None
        kind = str(evidence.provenance.get("kind") or "").strip().lower()
        raw = evidence.provenance.get("raw")
        raw_payload = raw if isinstance(raw, dict) else {}
        has_relation_shape = kind in _RELATION_KINDS or any(
            raw_payload.get(field)
            for field in ("relation_type", "rel_type", "target_name", "path")
        )
        relation_source, relation_type, relation_target = relation_direction(
            raw_payload
        )
        direction_complete = bool(relation_source and relation_type and relation_target)
        endpoints = relation_endpoints(must_cover.question)
        endpoints_complete = all(endpoint in content for endpoint in endpoints)
        requires_path = any(
            term in must_cover.question for term in ("路径", "多跳", "几跳")
        )
        has_path = kind in {"path", "multi_hop"} or bool(raw_payload.get("path"))
        if (
            not has_relation_shape
            or not direction_complete
            or not endpoints_complete
            or (requires_path and not has_path)
            or not applicability_complete
        ):
            if not has_relation_shape:
                reason = "relationship_structure_missing"
            elif not direction_complete:
                reason = "relationship_direction_missing"
            elif not endpoints_complete:
                reason = "relationship_endpoint_missing"
            elif requires_path and not has_path:
                reason = "required_path_missing"
            else:
                reason = "applicability_condition_missing"
            return _assessment(
                task=task,
                must_cover=must_cover,
                evidence=evidence,
                support_status="partially_supported",
                reason=reason,
                applicability_conditions=matched_applicability,
            )
        return _assessment(
            task=task,
            must_cover=must_cover,
            evidence=evidence,
            support_status="supported",
            reason="relationship_structure_and_applicability_matched",
            applicability_conditions=matched_applicability,
        )

    if must_cover.claim_type == "source":
        has_locator = bool(
            evidence.source_page
            or evidence.source_chunk_id
            or evidence.page
            or evidence.chunk_id
        )
        if publication_identity(evidence) is None:
            return _assessment(
                task=task,
                must_cover=must_cover,
                evidence=evidence,
                support_status="partially_supported",
                reason="publication_identity_missing",
            )
        if (
            evidence.page is not None
            and evidence.source_page is not None
            and evidence.page != evidence.source_page
        ) or (
            evidence.chunk_id
            and evidence.source_chunk_id
            and evidence.chunk_id != evidence.source_chunk_id
        ):
            return _assessment(
                task=task,
                must_cover=must_cover,
                evidence=evidence,
                support_status="contradicted",
                reason="source_locator_inconsistent",
            )
        return _assessment(
            task=task,
            must_cover=must_cover,
            evidence=evidence,
            support_status=("supported" if has_locator else "partially_supported"),
            reason=(
                "publication_and_locator_present"
                if has_locator
                else "source_locator_missing"
            ),
            applicability_conditions=matched_applicability,
        )

    return None


def _uncertain_assessment(
    *,
    task: RetrievalTask,
    must_cover: MustCoverItem,
    evidence: Evidence,
    reason: str,
) -> ClaimEvidenceAssessment:
    return _assessment(
        task=task,
        must_cover=must_cover,
        evidence=evidence,
        support_status="uncertain",
        reason=reason,
        applicability_conditions=_matched_applicability(must_cover, evidence),
        verifier_version="claim_support.semantic.v1",
    )


def _normalized_grounding_text(text: str) -> tuple[str, list[int]]:
    normalized: list[str] = []
    positions: list[int] = []
    for index, char in enumerate(text):
        if char.isspace():
            continue
        mapped = char.translate(_GROUNDING_CHAR_MAP).casefold()
        normalized.extend(mapped)
        positions.extend([index] * len(mapped))
    return "".join(normalized), positions


def _ground_support_excerpt(excerpt: str, content: str) -> str | None:
    """容忍 OCR 空白和等价标点，并返回证据中的原始连续片段。"""
    if not excerpt:
        return None
    if excerpt in content:
        return excerpt
    normalized_excerpt, _ = _normalized_grounding_text(excerpt)
    normalized_content, positions = _normalized_grounding_text(content)
    if len(normalized_excerpt) < 8:
        return None
    start = normalized_content.find(normalized_excerpt)
    if start < 0:
        return None
    end = start + len(normalized_excerpt) - 1
    return content[positions[start] : positions[end] + 1]


async def verify_claim_evidence(
    *,
    task: RetrievalTask,
    must_cover: MustCoverItem,
    evidence: list[Evidence],
    llm_client: Any | None,
    timeout: float,
) -> tuple[list[ClaimEvidenceAssessment], dict[str, int]]:
    """先执行确定性校验，再批量校验无法确定的自由文本。"""
    assessments: list[ClaimEvidenceAssessment] = []
    unresolved: list[Evidence] = []
    for item in evidence:
        deterministic = deterministic_claim_support(
            task=task,
            must_cover=must_cover,
            evidence=item,
        )
        if deterministic is None:
            unresolved.append(item)
        else:
            assessments.append(deterministic)
    if not unresolved:
        return assessments, empty_token_usage()
    if llm_client is None:
        assessments.extend(
            _uncertain_assessment(
                task=task,
                must_cover=must_cover,
                evidence=item,
                reason="semantic_verifier_unavailable",
            )
            for item in unresolved
        )
        return assessments, empty_token_usage()

    evidence_payload = [
        {
            "evidence_id": evidence_identity(item),
            "content": item.content[:1200],
            "source_type": item.source_type,
            "publication_identity": publication_identity(item),
        }
        for item in unresolved[:8]
    ]
    messages = [
        {
            "role": "system",
            "content": (
                "你是 claim-evidence 支持校验器。只判断证据是否直接支持目标问题点，"
                '不得补充外部知识。输出 JSON：{"assessments":[{"evidence_id":"",'
                '"support_status":"supported|partially_supported|contradicted|irrelevant|uncertain",'
                '"support_excerpt":"","applicability_conditions":[],"reason":""}]}。'
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "must_cover_id": must_cover.item_id,
                    "question": must_cover.question,
                    "claim_type": must_cover.claim_type,
                    "applicability_conditions": must_cover.applicability_conditions,
                    "evidence": evidence_payload,
                },
                ensure_ascii=False,
            ),
        },
    ]
    usage = empty_token_usage()
    parsed_by_id: dict[str, ClaimEvidenceAssessment] = {}
    evidence_by_id = {evidence_identity(item): item for item in unresolved[:8]}
    valid_evidence_ids = set(evidence_by_id)
    failure_reason = "semantic_verifier_invalid_response"
    try:
        response = await llm_client.chat_with_tokens_async(
            messages=messages,
            temperature=0.0,
            timeout=timeout,
            actor_role="claim_verifier",
            actor_name="claim_evidence_verifier",
            response_format={"type": "json_object"},
        )
        usage = {
            "prompt_tokens": int(getattr(response, "prompt_tokens", 0) or 0),
            "completion_tokens": int(getattr(response, "completion_tokens", 0) or 0),
            "total_tokens": int(getattr(response, "total_tokens", 0) or 0),
        }
        payload = json.loads(str(getattr(response, "content", "") or ""))
        rows = payload.get("assessments") if isinstance(payload, dict) else []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            evidence_id = str(row.get("evidence_id") or "").strip()
            if evidence_id not in valid_evidence_ids:
                continue
            raw_status = str(row.get("support_status") or "").strip()
            if raw_status not in _CLAIM_SUPPORT_STATUSES:
                continue
            support_status = cast(ClaimSupportStatus, raw_status)
            support_excerpt = str(row.get("support_excerpt") or "")[:2000]
            if support_status in {
                "supported",
                "partially_supported",
                "contradicted",
            }:
                grounded_excerpt = _ground_support_excerpt(
                    support_excerpt, evidence_by_id[evidence_id].content
                )
                if grounded_excerpt is None:
                    support_status = "uncertain"
                    support_excerpt = ""
                    reason = "semantic_verifier_excerpt_not_grounded"
                else:
                    support_excerpt = grounded_excerpt
                    reason = str(row.get("reason") or "semantic_verifier")[:1000]
            else:
                reason = str(row.get("reason") or "semantic_verifier")[:1000]
            candidate = ClaimEvidenceAssessment(
                task_id=task.task_id,
                must_cover_id=must_cover.item_id,
                evidence_id=evidence_id,
                support_status=support_status,
                support_excerpt=support_excerpt,
                applicability_conditions=[
                    str(value)
                    for value in row.get("applicability_conditions", [])
                    if str(value) in must_cover.applicability_conditions
                ],
                reason=reason,
                verifier_version="claim_support.semantic.v1",
            )
            parsed_by_id[evidence_id] = candidate
    except Exception as exc:
        parsed_by_id = {}
        failure_reason = f"semantic_verifier_error:{type(exc).__name__}"

    for item in unresolved:
        identity = evidence_identity(item)
        assessments.append(
            parsed_by_id.get(identity)
            or _uncertain_assessment(
                task=task,
                must_cover=must_cover,
                evidence=item,
                reason=failure_reason,
            )
        )
    return assessments, usage
