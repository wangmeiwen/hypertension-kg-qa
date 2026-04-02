"""终答写作器：证据打包 + LLM 生成 + 异常降级。"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.a2a.agent.llm_client import LLMClient
from app.common.config import Config


logger = logging.getLogger(__name__)

_PROMPT_VERSION = "v1"
_COMPARATOR_HINTS = ("vs", "versus", "compare", "comparison", "比较", "对比", "相比", "单药", "复方")
_OUTCOME_HINTS = (
    "outcome",
    "efficacy",
    "safety",
    "protection",
    "renal",
    "cardio",
    "疗效",
    "效果",
    "降压",
    "靶器官",
    "保护",
    "安全",
    "不良",
    "风险",
    "结局",
)


def _dedup_keep_order(rows: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for item in rows:
        token = str(item or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        output.append(token)
    return output


def _normalize_tokens(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(item or "").strip().lower() for item in raw if str(item or "").strip()]


def _contains_any(text: str, tokens: list[str]) -> bool:
    if not tokens:
        return False
    lowered = str(text or "").lower()
    return any(token and token in lowered for token in tokens)


def _extract_row_text(row: dict[str, Any]) -> str:
    if not isinstance(row, dict):
        return ""

    entity = row.get("entity") if isinstance(row.get("entity"), dict) else {}
    if entity:
        return str(entity.get("text") or entity.get("name") or "").strip()

    relationship = row.get("relationship") if isinstance(row.get("relationship"), dict) else {}
    if relationship:
        src = str(relationship.get("source_name") or "").strip()
        rel = str(relationship.get("relation_type") or "").strip()
        dst = str(relationship.get("target_name") or "").strip()
        text = str(relationship.get("text") or "").strip()
        return f"{src} {rel} {dst} {text}".strip()

    hit = row.get("hit") if isinstance(row.get("hit"), dict) else {}
    if hit:
        return str(hit.get("text") or hit.get("parent_content") or hit.get("content") or "").strip()

    record = row.get("record") if isinstance(row.get("record"), dict) else {}
    if record:
        return str(record).strip()

    return str(row.get("text") or "").strip()


def _extract_row_citation(row: dict[str, Any]) -> str:
    if not isinstance(row, dict):
        return ""

    for key in ("entity", "hit", "relationship", "record"):
        part = row.get(key)
        if not isinstance(part, dict):
            continue
        for ref_key in ("doc_id", "chunk_id", "source_id", "id"):
            value = str(part.get(ref_key) or "").strip()
            if value:
                return value
    return ""


def _build_row_score(
    *,
    text: str,
    source: str,
    must_cover_tokens: list[str],
    entity_tokens: list[str],
    missing_tokens: list[str],
) -> tuple[float, bool]:
    score = 0.0
    lower_text = str(text or "").lower()

    entity_hit = _contains_any(lower_text, entity_tokens)
    must_cover_hit = _contains_any(lower_text, must_cover_tokens)
    comparator_hit = _contains_any(lower_text, [token.lower() for token in _COMPARATOR_HINTS])
    outcome_hit = _contains_any(lower_text, [token.lower() for token in _OUTCOME_HINTS])
    missing_hit = _contains_any(lower_text, missing_tokens)

    if entity_hit:
        score += 2.0
    if must_cover_hit:
        score += 2.0
    if comparator_hit:
        score += 2.0
    if outcome_hit:
        score += 2.0
    if missing_hit:
        score += 1.0
    if source == "rag":
        score += 1.0

    # 偏好有信息密度的文本。
    if len(lower_text) >= 32:
        score += 0.5

    direct_like = comparator_hit and outcome_hit
    return score, direct_like


def pack_final_answer_evidence(
    *,
    evidence_bundle: dict[str, Any],
    gate_result: dict[str, Any],
    coverage_map: dict[str, dict[str, Any]],
    resolved_query: dict[str, Any],
    max_per_subtask: int = 5,
) -> dict[str, Any]:
    kg_rows = evidence_bundle.get("kg_evidence") if isinstance(evidence_bundle.get("kg_evidence"), list) else []
    rag_rows = evidence_bundle.get("rag_evidence") if isinstance(evidence_bundle.get("rag_evidence"), list) else []

    must_cover_tokens = _normalize_tokens(resolved_query.get("must_cover") if isinstance(resolved_query, dict) else [])
    entity_tokens = _normalize_tokens(resolved_query.get("entities") if isinstance(resolved_query, dict) else [])

    missing_points = []
    missing_points_obj = gate_result.get("missing_points") if isinstance(gate_result, dict) else []
    uncovered_points_obj = gate_result.get("uncovered_points") if isinstance(gate_result, dict) else []
    if isinstance(missing_points_obj, list):
        missing_points.extend([str(item or "").strip() for item in missing_points_obj if str(item or "").strip()])
    if isinstance(uncovered_points_obj, list):
        missing_points.extend([str(item or "").strip() for item in uncovered_points_obj if str(item or "").strip()])
    missing_points = _dedup_keep_order(missing_points)
    missing_tokens = [item.lower() for item in missing_points]

    scored_rows: list[dict[str, Any]] = []

    def _consume_rows(rows: list[dict[str, Any]], source: str) -> None:
        for row in rows:
            if not isinstance(row, dict):
                continue
            text = _extract_row_text(row)
            if not text:
                continue
            score, direct_like = _build_row_score(
                text=text,
                source=source,
                must_cover_tokens=must_cover_tokens,
                entity_tokens=entity_tokens,
                missing_tokens=missing_tokens,
            )
            group_id = str(
                row.get("subtask_id")
                or (row.get("meta") if isinstance(row.get("meta"), dict) else {}).get("subtask_id")
                or "global"
            ).strip() or "global"
            scored_rows.append(
                {
                    "group_id": group_id,
                    "source": source,
                    "text": text,
                    "score": score,
                    "direct_like": direct_like,
                    "citation": _extract_row_citation(row),
                }
            )

    _consume_rows(kg_rows, "kg")
    _consume_rows(rag_rows, "rag")

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in scored_rows:
        grouped.setdefault(str(row.get("group_id") or "global"), []).append(row)

    selected_rows: list[dict[str, Any]] = []
    for group_id, rows in grouped.items():
        ranked = sorted(rows, key=lambda item: float(item.get("score") or 0.0), reverse=True)
        _ = group_id
        selected_rows.extend(ranked[: max(3, min(max_per_subtask, 5))])

    selected_rows = sorted(selected_rows, key=lambda item: float(item.get("score") or 0.0), reverse=True)

    supported_points: list[str] = []
    indirect_points: list[str] = []
    citations: list[str] = []

    for row in selected_rows:
        text = str(row.get("text") or "").strip()
        citation = str(row.get("citation") or "").strip()
        if citation:
            citations.append(citation)
        if not text:
            continue
        if bool(row.get("direct_like")) or float(row.get("score") or 0.0) >= 4.0:
            supported_points.append(text)
        else:
            indirect_points.append(text)

    if not supported_points and indirect_points:
        supported_points = indirect_points[:2]
        indirect_points = indirect_points[2:]

    high_risk_notes: list[str] = []
    if bool(gate_result.get("high_risk")):
        tokens = gate_result.get("high_risk_tokens") if isinstance(gate_result.get("high_risk_tokens"), list) else []
        if tokens:
            high_risk_notes.append(
                "检测到高风险关键词：" + "、".join([str(item or "").strip() for item in tokens if str(item or "").strip()])
            )

    coverage_rows = coverage_map if isinstance(coverage_map, dict) else {}
    low_coverage_points: list[str] = []
    for row in coverage_rows.values():
        if not isinstance(row, dict):
            continue
        missing = row.get("missing_points") if isinstance(row.get("missing_points"), list) else []
        low_coverage_points.extend([str(item or "").strip() for item in missing if str(item or "").strip()])

    return {
        "supported_points": _dedup_keep_order(supported_points)[:12],
        "indirect_points": _dedup_keep_order(indirect_points)[:12],
        "missing_points": _dedup_keep_order([*missing_points, *low_coverage_points])[:12],
        "high_risk_notes": _dedup_keep_order(high_risk_notes),
        "evidence_citations": _dedup_keep_order(citations)[:20],
        "selected_evidence_count": len(selected_rows),
    }


def _build_writer_messages(
    *,
    question: str,
    answer_mode: str,
    packed_evidence: dict[str, Any],
) -> list[dict[str, str]]:
    system_prompt = (
        "你是医疗问答终答写作器。"
        "只能基于给定证据回答，不得编造未支持结论。"
        "输出必须是三段短文，且仅包含以下三段标题："
        "结论：\n证据支持点：\n不确定性与适用边界：。"
        "禁止列表化模板。"
        "若证据为间接支持，必须明确写“现有证据更偏间接/关联支持”。"
        "涉及靶器官保护但缺少直接比较证据时，需写“可能有潜在优势，但当前证据未直接完成头对头证明”。"
        "禁止给出超出证据的治疗承诺。"
    )

    user_payload = {
        "question": str(question or "").strip(),
        "answer_mode": str(answer_mode or "").strip(),
        "evidence_pack": packed_evidence,
        "mode_requirements": {
            "direct": "先给明确结论，再给证据支持点，最后给适用边界或注意事项。",
            "partial": "先写当前证据支持的部分，再写目前不能直接下结论的部分，并明确缺口属于 comparator/outcome/头对头证据。",
        },
    }

    return [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": "请基于以下输入生成终答：\n" + json.dumps(user_payload, ensure_ascii=False),
        },
    ]


def build_degraded_answer(
    *,
    question: str,
    answer_mode: str,
    packed_evidence: dict[str, Any],
) -> str:
    supported_points = packed_evidence.get("supported_points") if isinstance(packed_evidence.get("supported_points"), list) else []
    indirect_points = packed_evidence.get("indirect_points") if isinstance(packed_evidence.get("indirect_points"), list) else []
    missing_points = packed_evidence.get("missing_points") if isinstance(packed_evidence.get("missing_points"), list) else []
    high_risk_notes = packed_evidence.get("high_risk_notes") if isinstance(packed_evidence.get("high_risk_notes"), list) else []

    supported_text = "；".join([str(item or "").strip() for item in supported_points[:4] if str(item or "").strip()])
    if not supported_text:
        supported_text = "现有证据已覆盖部分关键问题，但仍需结合上下文谨慎解释。"

    indirect_text = "；".join([str(item or "").strip() for item in indirect_points[:3] if str(item or "").strip()])
    missing_text = "；".join([str(item or "").strip() for item in missing_points[:4] if str(item or "").strip()])
    risk_text = "；".join([str(item or "").strip() for item in high_risk_notes[:2] if str(item or "").strip()])

    if answer_mode == "direct":
        conclusion = "结论：数据库已检索到可支撑回答的证据，当前可给出受证据约束的结论。"
    else:
        conclusion = "结论：数据库已检索到相关证据，当前证据支持的部分可以先回答。"

    support_paragraph = "证据支持点：" + supported_text
    if indirect_text:
        support_paragraph = support_paragraph + "；现有证据更偏间接/关联支持：" + indirect_text

    boundary_parts = []
    if missing_text:
        boundary_parts.append("目前不能直接下结论的部分主要缺少：" + missing_text)
    else:
        boundary_parts.append("目前不能直接下结论的部分主要在于缺少直接头对头比较证据。")
    if risk_text:
        boundary_parts.append(risk_text)
    boundary_parts.append("建议结合具体人群、结局指标和对照条件进一步核实。")

    return (
        conclusion
        + "\n"
        + support_paragraph
        + f"\n不确定性与适用边界：{'；'.join(boundary_parts)}"
        + (f"（问题：{str(question or '').strip()}）" if str(question or "").strip() else "")
    )


class AnswerWriter:
    """终答写作器。"""

    def __init__(self, llm_client: LLMClient | None = None, conf: Config | None = None) -> None:
        self._conf = conf if conf is not None else Config()
        self._client = llm_client if llm_client is not None else LLMClient(self._conf)

    async def generate(
        self,
        *,
        question: str,
        answer_mode: str,
        evidence_bundle: dict[str, Any],
        gate_result: dict[str, Any],
        coverage_map: dict[str, dict[str, Any]],
        resolved_query: dict[str, Any],
        processing_steps: list[str],
    ) -> dict[str, Any]:
        packed_evidence = pack_final_answer_evidence(
            evidence_bundle=evidence_bundle,
            gate_result=gate_result,
            coverage_map=coverage_map,
            resolved_query=resolved_query,
        )

        messages = _build_writer_messages(
            question=question,
            answer_mode=answer_mode,
            packed_evidence=packed_evidence,
        )

        try:
            response = await self._client.chat_with_tokens_async(
                messages=messages,
                temperature=0.1,
                timeout=90,
            )
            answer_text = str(getattr(response, "content", "") or "").strip()
            if not answer_text:
                raise ValueError("empty_llm_answer")

            token_usage = {
                "prompt_tokens": int(getattr(response, "prompt_tokens", 0) or 0),
                "completion_tokens": int(getattr(response, "completion_tokens", 0) or 0),
                "total_tokens": int(getattr(response, "total_tokens", 0) or 0),
            }

            return {
                "answer_text": answer_text,
                "writer_metadata": {
                    "writer_used": "llm",
                    "writer_fallback_reason": "",
                    "prompt_version": _PROMPT_VERSION,
                    "mode": str(answer_mode or "").strip(),
                },
                "token_usage": token_usage,
                "packed_evidence": packed_evidence,
                "processing_steps": processing_steps,
            }
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            logger.warning(
                "answer_writer_fallback %s",
                json.dumps(
                    {
                        "error_type": type(exc).__name__,
                        "answer_mode": str(answer_mode or "").strip(),
                        "question": str(question or "").strip()[:120],
                    },
                    ensure_ascii=False,
                ),
            )
            return {
                "answer_text": build_degraded_answer(
                    question=question,
                    answer_mode=answer_mode,
                    packed_evidence=packed_evidence,
                ),
                "writer_metadata": {
                    "writer_used": "fallback",
                    "writer_fallback_reason": type(exc).__name__,
                    "prompt_version": _PROMPT_VERSION,
                    "mode": str(answer_mode or "").strip(),
                },
                "token_usage": {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                },
                "packed_evidence": packed_evidence,
                "processing_steps": processing_steps,
            }


async def generate_final_answer_async(
    *,
    question: str,
    answer_mode: str,
    evidence_bundle: dict[str, Any],
    gate_result: dict[str, Any],
    coverage_map: dict[str, dict[str, Any]],
    resolved_query: dict[str, Any],
    processing_steps: list[str],
    llm_client: LLMClient | None = None,
) -> dict[str, Any]:
    writer = AnswerWriter(llm_client=llm_client)
    return await writer.generate(
        question=question,
        answer_mode=answer_mode,
        evidence_bundle=evidence_bundle,
        gate_result=gate_result,
        coverage_map=coverage_map,
        resolved_query=resolved_query,
        processing_steps=processing_steps,
    )
