"""确定性问题计划生成器。"""

from __future__ import annotations

import hashlib
import re

from app.a2a.medical_risk import MedicalRiskAssessment
from app.a2a.models import (
    AgentType,
    MinimumEvidencePolicy,
    MustCoverItem,
    QuestionPlan,
    RetrievalTask,
)

_CLAUSE_SPLIT_PATTERN = re.compile(r"[；;]|(?:，|,)(?:并且|并|同时|以及|还要|还需)")

_CONDITION_TERMS = (
    "孕妇",
    "妊娠",
    "儿童",
    "老年人",
    "高龄",
    "CKD",
    "慢性肾病",
    "糖尿病",
    "诊室血压",
    "家庭血压",
    "动态血压",
)
_AGE_CONDITION_PATTERN = re.compile(
    r"(?:≥|<=|>=|≤|＜|＞|<|>)?\s*\d{1,3}\s*岁(?:及?以上|及?以下)?"
)


def _stable_id(prefix: str, *parts: str) -> str:
    """根据稳定输入生成短 ID。"""
    payload = "\x1f".join(str(part or "").strip() for part in parts)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{digest}"


def _split_question(question: str) -> list[str]:
    """按明确的并列连接符拆分问题。"""
    text = str(question or "").strip()
    clauses = [item.strip(" ，,。？?") for item in _CLAUSE_SPLIT_PATTERN.split(text)]
    clauses = [item for item in clauses if item]
    return clauses or [text]


def _claim_type(question: str) -> str:
    """推断 must-cover 的结论类型。"""
    text = str(question or "")
    if any(term in text for term in ("比较", "分别", "差异")):
        return "comparison"
    if any(term in text for term in ("关系", "路径", "关联", "连接")):
        return "relation"
    if any(
        term in text
        for term in (
            "多少",
            "阈值",
            "目标",
            "剂量",
            "几天",
            "几次",
            "多久",
            "频次",
            "频率",
        )
    ):
        return "numeric"
    if any(term in text for term in ("指南", "来源", "依据", "出处")):
        return "source"
    return "fact"


def _applicability_conditions(question: str) -> list[str]:
    """提取问题中明确出现的适用条件。"""
    lowered = str(question or "").lower()
    conditions = [term for term in _CONDITION_TERMS if term.lower() in lowered]
    conditions.extend(
        re.sub(r"\s+", "", matched.group())
        for matched in _AGE_CONDITION_PATTERN.finditer(lowered)
    )
    return list(dict.fromkeys(conditions))


def _evidence_policy(
    *,
    claim_type: str,
    applicability_conditions: list[str],
    medical_risk: MedicalRiskAssessment,
) -> MinimumEvidencePolicy:
    """按 claim 类型和风险生成检索器与最低证据策略。"""
    required_retrievers: list[AgentType]
    optional_retrievers: list[AgentType]
    if claim_type == "relation":
        required_retrievers = ["kg"]
        optional_retrievers = ["rag"]
    elif claim_type == "source":
        required_retrievers = ["rag"]
        optional_retrievers = ["kg"]
    else:
        required_retrievers = []
        optional_retrievers = ["rag", "kg"]
    strict_applicability = medical_risk.risk_level == "high" or claim_type in {
        "numeric",
        "comparison",
    }
    return MinimumEvidencePolicy(
        minimum_supported_evidence=1,
        minimum_distinct_publications=1,
        required_applicability_fields=(
            list(applicability_conditions) if strict_applicability else []
        ),
        conflict_policy="block_unresolved",
        required_retrievers=required_retrievers,
        optional_retrievers=optional_retrievers,
    )


def build_question_plan(
    question: str, medical_risk: MedicalRiskAssessment
) -> QuestionPlan:
    """生成带稳定 must-cover 和检索任务的结构化计划。"""
    original_question = str(question or "").strip()
    plan_id = _stable_id("plan", original_question)
    must_cover: list[MustCoverItem] = []
    retrieval_tasks: list[RetrievalTask] = []

    for index, clause in enumerate(_split_question(original_question), start=1):
        item_id = _stable_id("mc", original_question, str(index), clause)
        claim_type = _claim_type(clause)
        applicability_conditions = _applicability_conditions(original_question)
        evidence_policy = _evidence_policy(
            claim_type=claim_type,
            applicability_conditions=applicability_conditions,
            medical_risk=medical_risk,
        )
        item = MustCoverItem(
            item_id=item_id,
            question=clause,
            claim_type=claim_type,
            evidence_policy=evidence_policy,
            applicability_conditions=applicability_conditions,
        )
        must_cover.append(item)
        for agent_type in [
            *evidence_policy.required_retrievers,
            *evidence_policy.optional_retrievers,
        ]:
            retrieval_tasks.append(
                RetrievalTask(
                    task_id=_stable_id("task", item_id, agent_type),
                    must_cover_id=item_id,
                    target_agent=agent_type,
                    retriever_requirement=(
                        "required"
                        if agent_type in evidence_policy.required_retrievers
                        else "optional"
                    ),
                    question=clause,
                )
            )

    return QuestionPlan(
        plan_id=plan_id,
        original_question=original_question,
        medical_risk_level=medical_risk.risk_level,
        must_cover=must_cover,
        retrieval_tasks=retrieval_tasks,
        answer_constraints=[
            "每个 must-cover 必须有直接 Evidence",
            "未解决冲突时不得输出确定性结论",
        ],
    )
