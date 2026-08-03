"""医疗问题风险分级。"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class MedicalRiskAssessment(BaseModel):
    """医疗风险 Gate 的确定性输出。"""

    model_config = ConfigDict(extra="forbid")

    risk_level: Literal["low", "high", "emergency"] = "low"
    action: Literal["standard", "evidence_required", "urgent_redirect"] = "standard"
    matched_signals: list[str] = Field(default_factory=list)
    minimum_distinct_publications: int = Field(default=1, ge=1, le=3)


_EMERGENCY_SIGNALS = (
    "胸痛",
    "呼吸困难",
    "意识不清",
    "意识障碍",
    "昏厥",
    "晕厥",
    "抽搐",
    "偏瘫",
    "言语不清",
    "剧烈头痛",
    "高血压急症",
)

_MEDICATION_ADJUSTMENT_SIGNALS = (
    "加量",
    "减量",
    "停药",
    "换药",
    "调整剂量",
    "增加剂量",
    "减少剂量",
    "自行服用",
    "能不能停",
    "是否停药",
)

_SPECIAL_POPULATION_SIGNALS = (
    "孕妇",
    "妊娠",
    "怀孕",
    "哺乳",
    "儿童",
    "小儿",
    "婴儿",
    "未成年人",
)

_SEVERE_RENAL_SIGNALS = (
    "严重肾功能不全",
    "肾衰竭",
    "肾功能衰竭",
    "终末期肾病",
    "透析",
)

_NEGATIONS = ("无", "没有", "未出现", "不伴", "否认")


def _contains_non_negated(text: str, signal: str) -> bool:
    """判断信号是否出现且未被紧邻否定。"""
    for matched in re.finditer(re.escape(signal), text):
        prefix = text[max(0, matched.start() - 5) : matched.start()]
        if not any(prefix.endswith(negation) for negation in _NEGATIONS):
            return True
    return False


def _matched_signals(text: str, signals: tuple[str, ...]) -> list[str]:
    """返回去重后的非否定风险信号。"""
    return [signal for signal in signals if _contains_non_negated(text, signal)]


def _has_emergency_blood_pressure(text: str) -> bool:
    """识别明确描述的 180/120 mmHg 及以上血压。"""
    pattern = re.compile(
        r"(?:血压[^\d]{0,8})?(?P<systolic>\d{3})\s*(?:/|／)\s*(?P<diastolic>\d{2,3})"
    )
    for matched in pattern.finditer(text):
        if (
            int(matched.group("systolic")) >= 180
            and int(matched.group("diastolic")) >= 120
        ):
            return True
    return False


def assess_medical_risk(question: str) -> MedicalRiskAssessment:
    """根据问题文本执行确定性医疗风险分级。"""
    text = str(question or "").strip().lower()
    emergency = _matched_signals(text, _EMERGENCY_SIGNALS)
    if _has_emergency_blood_pressure(text):
        emergency.append("blood_pressure_at_least_180_120")
    if emergency:
        return MedicalRiskAssessment(
            risk_level="emergency",
            action="urgent_redirect",
            matched_signals=emergency,
            minimum_distinct_publications=1,
        )

    high_risk = [
        *_matched_signals(text, _MEDICATION_ADJUSTMENT_SIGNALS),
        *_matched_signals(text, _SPECIAL_POPULATION_SIGNALS),
        *_matched_signals(text, _SEVERE_RENAL_SIGNALS),
    ]
    if re.search(r"egfr\s*(?:<|≤|低于)\s*30", text, flags=re.IGNORECASE):
        high_risk.append("egfr_below_30")
    if high_risk:
        return MedicalRiskAssessment(
            risk_level="high",
            action="evidence_required",
            matched_signals=list(dict.fromkeys(high_risk)),
            minimum_distinct_publications=2,
        )

    return MedicalRiskAssessment()
