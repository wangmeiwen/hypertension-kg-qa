"""终答写作器：证据打包 + LLM 生成 + 异常降级。"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from app.a2a.agent.llm_client import LLMClient
from app.common.config import Config


logger = logging.getLogger(__name__)

_PROMPT_VERSION = "clinician_v2"
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
    """执行dedup_keep_order流程并返回结果。
    
    Args:
        rows: 输入数据列表。
    
    Returns:
        list[str]: 处理后的列表结果。
    """
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
    """规范化输入并返回标准化结果。
    
    Args:
        raw: 函数输入参数。
    
    Returns:
        list[str]: 处理后的列表结果。
    """
    if not isinstance(raw, list):
        return []
    return [str(item or "").strip().lower() for item in raw if str(item or "").strip()]


def _contains_any(text: str, tokens: list[str]) -> bool:
    """执行contains_any流程并返回结果。
    
    Args:
        text: 输入文本。
        tokens: 函数输入参数。
    
    Returns:
        bool: 是否满足条件。
    """
    if not tokens:
        return False
    lowered = str(text or "").lower()
    return any(token and token in lowered for token in tokens)


def _extract_row_text(row: dict[str, Any]) -> str:
    """提取并返回关键信息。
    
    Args:
        row: 输入数据项。
    
    Returns:
        str: 文本结果。
    """
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
    """提取并返回关键信息。
    
    Args:
        row: 输入数据项。
    
    Returns:
        str: 文本结果。
    """
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
    """构建并返回目标数据。
    
    Args:
        text: 输入文本。
        source: 函数输入参数。
        must_cover_tokens: 函数输入参数。
        entity_tokens: 函数输入参数。
        missing_tokens: 函数输入参数。
    
    Returns:
        tuple[float, bool]: 执行结果。
    """
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
    """执行pack_final_answer_evidence流程并返回结果。
    
    Args:
        evidence_bundle: 融合后的证据集合。
        gate_result: 门控评估结果。
        coverage_map: 子任务覆盖度映射。
        resolved_query: 问题改写后的结构化数据。
        max_per_subtask: 函数输入参数。
    
    Returns:
        dict[str, Any]: 结构化结果数据。
    """
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
        """执行consume_rows流程并返回结果。
        
        Args:
            rows: 输入数据列表。
            source: 函数输入参数。
        
        Returns:
            None: 无返回值。
        """
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
    """构建并返回目标数据。
    
    Args:
        question: 用户输入问题文本。
        answer_mode: 函数输入参数。
        packed_evidence: 函数输入参数。
    
    Returns:
        list[dict[str, str]]: 处理后的列表结果。
    """
    system_prompt = (
        "你是一名循证临床解释者，用温和、自然、像医生说明情况的中文回答用户。"
        "检索结果用于限定事实边界和可追溯依据；你可以用通用医学知识帮助解释和给出稳妥建议。"
        "必须清楚区分“检索结果支持的内容”和“一般医学上通常会这样理解/处理的内容”。"
        "如果检索结果没有直接覆盖用户问的某一点，不要机械拒答；可以说明“我查到的资料没有直接覆盖这一点”，"
        "再用通用医学知识给出保守解释和下一步建议。"
        "不要输出系统内部语言，例如数据库、证据包、门控、coverage、comparator、outcome、头对头缺口等。"
        "不要使用固定审稿模板，不要写成“结论/证据支持点/不确定性与适用边界”三段。"
        "回答应先直接回应问题，再解释依据和不确定处，最后用“我的建议是”给出安全、非处方化建议。"
        "不能做个体化诊断或处方承诺；若出现胸痛、呼吸困难、肢体麻木、意识异常等危险信号，应建议及时就医。"
        "不要自行添加免责声明，系统会在最终答案末尾统一追加固定免责声明。"
    )

    user_payload = {
        "question": str(question or "").strip(),
        "answer_mode": str(answer_mode or "").strip(),
        "evidence_pack": packed_evidence,
        "mode_requirements": {
            "direct": "检索结果能较好支撑回答时，可以更明确地解释；仍需避免个体化诊断或处方。",
            "partial": "检索结果只覆盖一部分时，先回答能支持的部分，再用一般医学知识补充保守解释，并说明哪些点还不能确定。",
        },
        "style_requirements": [
            "使用自然段，可以少量列表，但不要像审稿表或系统报告。",
            "不要提到数据库、证据包、门控、coverage、comparator、outcome 等内部词。",
            "结尾给出面向用户的下一步建议。",
        ],
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
    """构建degraded_answer相关信息并返回结果。
    
    Args:
        question: 用户输入问题文本。
        answer_mode: 函数输入参数。
        packed_evidence: 函数输入参数。
    
    Returns:
        str: 文本结果。
    """
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
        opening = f"关于“{str(question or '').strip()}”，我查到的资料可以支持一个比较明确的回答：{supported_text}。"
    else:
        opening = f"关于“{str(question or '').strip()}”，我查到的资料能先支持这部分判断：{supported_text}。"

    context_parts = []
    if indirect_text:
        context_parts.append(f"还有一些内容更适合作为背景参考：{indirect_text}。")
    if missing_text:
        context_parts.append(f"不过，我查到的资料没有直接覆盖这些点：{missing_text}，所以这部分不能当成确定结论。")
    else:
        context_parts.append("如果要把这个结论用于具体个人，还需要结合年龄、既往病史、血压读数、用药和检查结果一起判断。")

    recommendation_parts = []
    if risk_text:
        recommendation_parts.append(f"需要特别留意：{risk_text}。")
    recommendation_parts.append("我的建议是：把你的具体血压数值、症状、正在用的药和基础疾病补充清楚，再结合专业医生意见判断下一步。")
    recommendation_parts.append("如果已经有明显不适，尤其是胸痛、呼吸困难、肢体麻木、意识异常等情况，应及时就医。")

    return "\n\n".join(
        [
            opening,
            " ".join(context_parts),
            " ".join(recommendation_parts),
        ]
    )


class AnswerWriter:
    """终答写作器。"""

    def __init__(self, llm_client: LLMClient | None = None, conf: Config | None = None) -> None:
        """初始化实例并注入运行依赖。
        
        Args:
            llm_client: LLM 客户端实例。
            conf: 配置对象。
        
        Returns:
            None: 无返回值。
        """
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
        """执行generate流程并返回结果。
        
        Args:
            question: 用户输入问题文本。
            answer_mode: 函数输入参数。
            evidence_bundle: 融合后的证据集合。
            gate_result: 门控评估结果。
            coverage_map: 子任务覆盖度映射。
            resolved_query: 问题改写后的结构化数据。
            processing_steps: 函数输入参数。
        
        Returns:
            dict[str, Any]: 结构化结果数据。
        """
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
        except (
            RuntimeError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            OSError,
            ImportError,
            httpx.HTTPError,
            TimeoutError,
        ) as exc:
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
    """执行generate_final_answer_async流程并返回结果。
    
    Args:
        question: 用户输入问题文本。
        answer_mode: 函数输入参数。
        evidence_bundle: 融合后的证据集合。
        gate_result: 门控评估结果。
        coverage_map: 子任务覆盖度映射。
        resolved_query: 问题改写后的结构化数据。
        processing_steps: 函数输入参数。
        llm_client: LLM 客户端实例。
    
    Returns:
        dict[str, Any]: 结构化结果数据。
    """
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
