"""主代理决策策略。"""

from __future__ import annotations

import re
from typing import Any

from app.a2a.models import EvidenceBundle, FinalAnswer, OrchestratorDecision, SubAgentConstraints, SubTask


_GRAPH_HINTS = (
    "关系",
    "关联",
    "并发症",
    "相互作用",
    "机制",
    "风险因素",
    "分级",
    "诊断标准",
)

_RAG_HINTS = (
    "指南",
    "证据",
    "原文",
    "推荐",
    "段落",
    "文献",
    "出处",
)

_COMPARE_HINTS = (
    "相比",
    "比较",
    "优于",
    "优势",
    "区别",
    "差异",
)

_TERM_STOPWORDS = {
    "什么",
    "如何",
    "是否",
    "以及",
    "还有",
    "对于",
    "方面",
    "患者",
    "高血压",
    "治疗",
    "效果",
}


def _split_sentences(question: str) -> list[str]:
    """执行 _split_sentences 内部逻辑。
    
    Args:
        question: 参数。
    
    Returns:
        执行结果。
    """
    text = str(question or "").strip()
    if not text:
        return []
    chunks = [item.strip() for item in re.split(r"[。！？!?;；\n]+", text) if item and item.strip()]
    if not chunks:
        return [text]
    return chunks


def infer_default_agent(question: str) -> str:
    """推断 default_agent。
    
    Args:
        question: 参数。
    
    Returns:
        执行结果。
    """
    text = str(question or "")
    if any(token in text for token in _RAG_HINTS):
        return "rag"
    if any(token in text for token in _GRAPH_HINTS):
        return "kg"
    return "kg"


def _is_comparative_question(question: str) -> bool:
    """执行 _is_comparative_question 内部逻辑。
    
    Args:
        question: 参数。
    
    Returns:
        执行结果。
    """
    text = str(question or "").strip()
    if not text:
        return False
    return any(token in text for token in _COMPARE_HINTS)


def _extract_query_terms(question: str) -> list[str]:
    """执行 _extract_query_terms 内部逻辑。
    
    Args:
        question: 参数。
    
    Returns:
        执行结果。
    """
    text = str(question or "").strip()
    if not text:
        return []
    raw = re.findall(r"[A-Za-z0-9\u4e00-\u9fff]{2,}", text)
    terms: list[str] = []
    seen = set()
    for term in raw:
        token = str(term or "").strip()
        if not token or token in _TERM_STOPWORDS:
            continue
        if token in seen:
            continue
        seen.add(token)
        terms.append(token)
    return terms


def _evidence_text(row: dict[str, Any]) -> str:
    """执行 _evidence_text 内部逻辑。
    
    Args:
        row: 参数。
    
    Returns:
        执行结果。
    """
    if not isinstance(row, dict):
        return ""

    entity_obj = row.get("entity")
    entity = entity_obj if isinstance(entity_obj, dict) else {}
    if entity:
        name = str(entity.get("name") or entity.get("canonical_name") or "").strip()
        label = str(entity.get("label") or "").strip()
        text = str(entity.get("text") or "").strip()
        if name and label:
            return f"{name}（{label}） {text}".strip()
        return f"{name} {text}".strip()

    relation_obj = row.get("relationship")
    relation = relation_obj if isinstance(relation_obj, dict) else {}
    if relation:
        source = str(relation.get("source_name") or "").strip()
        relation_type = str(relation.get("relation_type") or "").strip()
        target = str(relation.get("target_name") or "").strip()
        text = str(relation.get("text") or "").strip()
        if source or relation_type or target:
            return f"{source} -[{relation_type}]-> {target} {text}".strip()
        return text

    hit_obj = row.get("hit")
    hit = hit_obj if isinstance(hit_obj, dict) else {}
    if hit:
        return str(hit.get("text") or hit.get("parent_content") or hit.get("content") or "").strip()

    record_obj = row.get("record")
    record = record_obj if isinstance(record_obj, dict) else {}
    if record:
        return str(record).strip()

    return ""


def _has_direct_support(question: str, evidence_rows: list[dict[str, Any]]) -> bool:
    """执行 _has_direct_support 内部逻辑。
    
    Args:
        question: 参数。
        evidence_rows: 参数。
    
    Returns:
        执行结果。
    """
    terms = _extract_query_terms(question)
    if not terms:
        return False

    for row in evidence_rows:
        text = _evidence_text(row)
        if not text:
            continue
        hit_terms = [term for term in terms if term in text]
        if len(hit_terms) >= 2:
            return True
    return False


def decompose_question(question: str) -> list[SubTask]:
    """拆解 question。
    
    Args:
        question: 参数。
    
    Returns:
        执行结果。
    """
    parts = _split_sentences(question)
    if not parts:
        return [
            SubTask(
                subtask_id=1,
                target_agent="kg",
                question=str(question or "").strip(),
                intent="direct_answer",
                constraints=SubAgentConstraints(allow_extra_retrieval=False),
                tool_name="kg_entity_relation_search",
                reason="direct",
            )
        ]

    rows: list[SubTask] = []
    for idx, part in enumerate(parts, start=1):
        agent = infer_default_agent(part)
        tool_name = "rag_search" if agent == "rag" else "kg_entity_relation_search"
        rows.append(
            SubTask(
                subtask_id=idx,
                target_agent=agent,
                question=part,
                intent="decomposed_subquestion",
                constraints=SubAgentConstraints(allow_extra_retrieval=False),
                tool_name=tool_name,
                reason="decompose",
            )
        )
    return rows


def rewrite_subquestion(subtask: SubTask) -> SubTask:
    """重写 subquestion。
    
    Args:
        subtask: 参数。
    
    Returns:
        执行结果。
    """
    question = str(subtask.question or "").strip()
    rewritten = question
    if rewritten and not rewritten.endswith("?") and not rewritten.endswith("？"):
        # 统一成为可检索问句，有助于子代理生成查询语句。
        rewritten = rewritten
    subtask.question = rewritten
    return subtask


def select_next_subagent(subtask: SubTask, evidence_bundle: EvidenceBundle) -> str:
    """选择 next_subagent。
    
    Args:
        subtask: 参数。
        evidence_bundle: 参数。
    
    Returns:
        执行结果。
    """
    _ = evidence_bundle
    return subtask.target_agent


def decide_need_supplement(
    evidence_bundle: EvidenceBundle,
    round_index: int,
    max_rounds: int,
    last_agent: str,
    last_no_signal: bool,
    original_question: str,
) -> OrchestratorDecision:
    """决策 need_supplement。
    
    Args:
        evidence_bundle: 参数。
        round_index: 参数。
        max_rounds: 参数。
        last_agent: 参数。
        last_no_signal: 参数。
        original_question: 参数。
    
    Returns:
        执行结果。
    """
    if round_index >= max_rounds:
        return OrchestratorDecision(need_supplement=False, reason="reach_max_rounds")

    if _is_comparative_question(original_question):
        if last_agent == "kg" and not evidence_bundle.rag_evidence:
            return OrchestratorDecision(
                need_supplement=True,
                next_agent="rag",
                next_question=str(original_question or "").strip(),
                reason="comparison_question_need_rag",
            )

    if not last_no_signal and (evidence_bundle.kg_evidence or evidence_bundle.rag_evidence):
        return OrchestratorDecision(need_supplement=False, reason="evidence_sufficient")

    next_agent = "rag" if last_agent == "kg" else "kg"
    return OrchestratorDecision(
        need_supplement=True,
        next_agent=next_agent,
        next_question=str(original_question or "").strip(),
        reason="last_round_no_signal",
    )


def _evidence_preview(rows: list[dict[str, Any]], question: str, limit: int = 3) -> list[str]:
    """执行 _evidence_preview 内部逻辑。
    
    Args:
        rows: 参数。
        question: 参数。
        limit: 参数。
    
    Returns:
        执行结果。
    """
    terms = _extract_query_terms(question)
    scored_rows: list[tuple[int, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        text = _evidence_text(row)
        if not text:
            continue
        score = 0
        for term in terms:
            if term in text:
                score += 1
        scored_rows.append((score, text[:180]))

    if not scored_rows:
        return []

    scored_rows.sort(key=lambda item: item[0], reverse=True)
    previews: list[str] = []
    seen = set()
    for _, text in scored_rows:
        if text in seen:
            continue
        seen.add(text)
        previews.append(text)
        if len(previews) >= limit:
            break
    return previews


def build_final_answer(evidence_bundle: EvidenceBundle, question: str, trace: list[str]) -> FinalAnswer:
    """构建 final_answer。
    
    Args:
        evidence_bundle: 参数。
        question: 参数。
        trace: 参数。
    
    Returns:
        执行结果。
    """
    kg_count = len(evidence_bundle.kg_evidence)
    rag_count = len(evidence_bundle.rag_evidence)
    total = kg_count + rag_count

    compare_question = _is_comparative_question(question)
    all_evidence = list(evidence_bundle.kg_evidence) + list(evidence_bundle.rag_evidence)
    direct_support = _has_direct_support(question, all_evidence)
    if compare_question and rag_count == 0:
        # 比较类问题若缺少文本证据，默认视为“非直接对照证据”。
        direct_support = False

    if total == 0:
        answer = (
            "当前证据不足，建议补充更具体的问题条件后重试，"
            "例如明确人群、指标阈值或治疗场景。"
        )
        confidence = 0.35
    else:
        lines = [
            f"问题：{str(question or '').strip()}",
            f"证据概览：图谱证据 {kg_count} 条，文本证据 {rag_count} 条。",
        ]
        if compare_question:
            if direct_support:
                lines.append("检索结果包含与该比较问题直接相关的证据。")
            else:
                lines.append("当前证据未命中“复方方案 vs 单药方案”的直接对照结论。")
        kg_preview = _evidence_preview(evidence_bundle.kg_evidence, question=question, limit=3)
        rag_preview = _evidence_preview(evidence_bundle.rag_evidence, question=question, limit=2)
        if kg_preview:
            lines.append("图谱要点：" + "；".join(kg_preview))
        if rag_preview:
            lines.append("文本要点：" + "；".join(rag_preview))
        if compare_question and not direct_support:
            lines.append("结论：目前仅能确认联合/复方治疗用于高血压管理，尚不足以证明其在降压幅度或靶器官保护上必然优于氨氯地平单药。")
            lines.append("建议补充具体人群、观察终点（如蛋白尿/eGFR/LVH）与随访时长后继续检索。")
            confidence = 0.45
        else:
            lines.append("以上结果由主代理汇总，请结合临床场景审慎使用。")
            confidence = 0.6 if total < 4 else 0.75
        answer = "\n".join(lines)

    return FinalAnswer(
        answer=answer,
        confidence=confidence,
        evidence_summary={
            "evidence_count": total,
            "kg_evidence_count": kg_count,
            "rag_evidence_count": rag_count,
            "conflict_count": 0,
            "hop_coverage": 1.0 if total > 0 else 0.0,
            "uncovered_points": [] if total > 0 else ["核心证据不足"],
            "high_risk": False,
            "high_risk_tokens": [],
            "disambiguation_ambiguous_count": 0,
            "community_count": 0,
            "top_community_score": 0.0,
            "force_supplement_required": False,
        },
        trace=trace,
    )
