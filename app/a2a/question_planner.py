"""确定性问题计划生成器。"""

from __future__ import annotations

import hashlib
import re

from app.a2a.medical_risk import MedicalRiskAssessment
from app.a2a.models import (
    AgentType,
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


def _required_evidence_types(question: str) -> list[AgentType]:
    """根据问题类型选择需要的检索来源。"""
    text = str(question or "").lower()
    requires_kg = any(
        term in text for term in ("关系", "路径", "关联", "实体", "多跳", "连接")
    )
    requires_rag = any(
        term in text for term in ("指南", "来源", "依据", "原文", "推荐", "为什么")
    )
    if requires_kg and not requires_rag:
        return ["kg"]
    if requires_rag and not requires_kg:
        return ["rag"]
    return ["kg", "rag"]


def _claim_type(question: str) -> str:
    """推断 must-cover 的结论类型。"""
    text = str(question or "")
    if any(term in text for term in ("比较", "分别", "差异")):
        return "comparison"
    if any(term in text for term in ("关系", "路径", "关联", "连接")):
        return "relation"
    if any(term in text for term in ("多少", "阈值", "目标", "剂量")):
        return "numeric"
    if any(term in text for term in ("指南", "来源", "依据", "出处")):
        return "source"
    return "fact"


def _applicability_conditions(question: str) -> list[str]:
    """提取问题中明确出现的适用条件。"""
    lowered = str(question or "").lower()
    return [term for term in _CONDITION_TERMS if term.lower() in lowered]


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
        evidence_types = _required_evidence_types(clause)
        item = MustCoverItem(
            item_id=item_id,
            question=clause,
            claim_type=_claim_type(clause),
            required_evidence_types=evidence_types,
            applicability_conditions=_applicability_conditions(original_question),
        )
        must_cover.append(item)
        for agent_type in evidence_types:
            retrieval_tasks.append(
                RetrievalTask(
                    task_id=_stable_id("task", item_id, agent_type),
                    must_cover_id=item_id,
                    target_agent=agent_type,
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
