"""带主体、时间和否定语境的医疗风险分级。"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MedicalRiskContext = Literal[
    "current_self_report",
    "historical_self_report",
    "negated_symptom",
    "knowledge_query",
    "third_party_case",
    "uncertain",
]


class MedicalRiskAssessment(BaseModel):
    """医疗风险 Gate 的确定性输出。"""

    model_config = ConfigDict(extra="forbid")

    risk_level: Literal["low", "high", "emergency"] = "low"
    action: Literal[
        "standard", "evidence_required", "urgent_redirect", "safety_caution"
    ] = "standard"
    context_type: MedicalRiskContext = "knowledge_query"
    matched_signals: list[str] = Field(default_factory=list)
    trigger_excerpts: list[str] = Field(default_factory=list)
    uncertainty_reasons: list[str] = Field(default_factory=list)
    rule_version: Literal["medical_risk.v2"] = "medical_risk.v2"
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

_SELF_MARKERS = ("我", "本人", "我的", "自己")
_CURRENT_MARKERS = ("现在", "目前", "此刻", "刚刚", "刚才", "正在", "今天")
_HISTORICAL_MARKERS = (
    "之前",
    "以前",
    "曾经",
    "过去",
    "当时",
    "昨日",
    "昨天",
    "上周",
    "病史",
)
_THIRD_PARTY_MARKERS = (
    "家人",
    "父亲",
    "母亲",
    "爸爸",
    "妈妈",
    "朋友",
    "他现在",
    "她现在",
    "他的",
    "她的",
    "病例",
    "案例",
)
_KNOWLEDGE_PATTERNS = (
    re.compile(r"(?:什么是|定义|是什么意思|如何定义|诊断标准|机制|有哪些|指南|文献)"),
)
_NEGATION_PREFIX = re.compile(
    r"(?:没有|并无|无|未出现|未见|否认|不伴|不觉得有|排除(?:了)?|已排除)"
    r"[^，。；;]{0,8}$"
)
_NEGATION_SUFFIX = re.compile(
    r"^[，,]?[^，。；;]{0,8}(?:现在|目前)?(?:已经|已)?"
    r"(?:没有|消失|缓解|排除|被排除)"
)
_BP_PAIR_PATTERN = re.compile(
    r"(?:血压[^\d]{0,8})?(?P<systolic>\d{2,3})\s*(?:/|／)\s*(?P<diastolic>\d{2,3})"
)
_SYSTOLIC_PATTERN = re.compile(
    r"(?:收缩压|高压)\s*(?:为|是|达到|达|有)?\s*(?P<value>\d{2,3})"
)
_DIASTOLIC_PATTERN = re.compile(
    r"(?:舒张压|低压)\s*(?:为|是|达到|达|有)?\s*(?P<value>\d{2,3})"
)


def _is_negated(text: str, start: int, end: int) -> bool:
    """检查信号前后局部否定和已缓解表述。"""
    prefix = text[max(0, start - 16) : start]
    suffix = text[end : min(len(text), end + 16)]
    return bool(_NEGATION_PREFIX.search(prefix) or _NEGATION_SUFFIX.search(suffix))


def _is_non_assertive_emergency_term(
    text: str, signal: str, start: int, end: int
) -> bool:
    """排除定义、引用和“按某诊断处理”中的急症名词。"""
    if signal != "高血压急症":
        return False
    prefix = text[max(0, start - 8) : start]
    suffix = text[end : min(len(text), end + 8)]
    return bool(
        re.search(r"(?:按|关于|了解)$", prefix)
        or re.match(r"(?:的)?(?:定义|是什么意思|处理吗)", suffix)
    )


def _signal_matches(
    text: str, signals: tuple[str, ...]
) -> tuple[list[str], list[str], list[str], list[str]]:
    """返回阳性信号、否定信号和触发片段。"""
    positive: list[str] = []
    negated: list[str] = []
    excerpts: list[str] = []
    negated_excerpts: list[str] = []
    for signal in signals:
        for matched in re.finditer(re.escape(signal), text):
            if _is_non_assertive_emergency_term(
                text, signal, matched.start(), matched.end()
            ):
                continue
            if _is_negated(text, matched.start(), matched.end()):
                negated.append(signal)
                negated_excerpts.append(
                    text[max(0, matched.start() - 8) : matched.end() + 8]
                )
                continue
            positive.append(signal)
            excerpts.append(text[max(0, matched.start() - 8) : matched.end() + 8])
    return (
        list(dict.fromkeys(positive)),
        list(dict.fromkeys(negated)),
        list(dict.fromkeys(excerpts)),
        list(dict.fromkeys(negated_excerpts)),
    )


def _blood_pressure_signals(text: str) -> tuple[list[str], list[str]]:
    """分别判断收缩压和舒张压紧急阈值。"""
    signals: list[str] = []
    excerpts: list[str] = []
    for matched in _BP_PAIR_PATTERN.finditer(text):
        if _is_negated(text, matched.start(), matched.end()):
            continue
        systolic = int(matched.group("systolic"))
        diastolic = int(matched.group("diastolic"))
        if systolic >= 180:
            signals.append("systolic_at_least_180")
        if diastolic >= 120:
            signals.append("diastolic_at_least_120")
        if systolic >= 180 or diastolic >= 120:
            excerpts.append(matched.group())
    for pattern, threshold, signal in (
        (_SYSTOLIC_PATTERN, 180, "systolic_at_least_180"),
        (_DIASTOLIC_PATTERN, 120, "diastolic_at_least_120"),
    ):
        for matched in pattern.finditer(text):
            if int(matched.group("value")) < threshold or _is_negated(
                text, matched.start(), matched.end()
            ):
                continue
            signals.append(signal)
            excerpts.append(matched.group())
    return list(dict.fromkeys(signals)), list(dict.fromkeys(excerpts))


def _has_marker(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def _is_knowledge_query(text: str) -> bool:
    return any(pattern.search(text) for pattern in _KNOWLEDGE_PATTERNS)


def _is_third_party_case(text: str) -> bool:
    return _has_marker(text, _THIRD_PARTY_MARKERS) or bool(
        re.search(r"(?:这名|该|一名)患者|患者(?:现在|目前|刚刚|血压)", text)
    )


def _classify_context(
    *,
    text: str,
    positive_emergency: list[str],
    negated_emergency: list[str],
    blood_pressure_signals: list[str],
) -> MedicalRiskContext:
    """按否定、主体、时间和问题类型确定语境。"""
    has_emergency = bool(positive_emergency or blood_pressure_signals)
    if negated_emergency and not has_emergency:
        return "negated_symptom"
    if _is_third_party_case(text) and "我是患者" not in text:
        return "third_party_case"
    if (
        has_emergency
        and _has_marker(text, _SELF_MARKERS)
        and _has_marker(text, _CURRENT_MARKERS)
    ):
        return "current_self_report"
    if _has_marker(text, _HISTORICAL_MARKERS):
        return "historical_self_report"
    if has_emergency and _has_marker(text, _SELF_MARKERS):
        if not _is_knowledge_query(text):
            return "current_self_report"
    if _is_knowledge_query(text):
        return "knowledge_query"
    if has_emergency:
        return "uncertain"
    if _has_marker(text, _SELF_MARKERS):
        return "current_self_report"
    return "knowledge_query"


def _high_risk_signals(text: str) -> tuple[list[str], list[str]]:
    signals: list[str] = []
    excerpts: list[str] = []
    for candidates in (
        _MEDICATION_ADJUSTMENT_SIGNALS,
        _SPECIAL_POPULATION_SIGNALS,
        _SEVERE_RENAL_SIGNALS,
    ):
        matched, _, matched_excerpts, _ = _signal_matches(text, candidates)
        signals.extend(matched)
        excerpts.extend(matched_excerpts)
    egfr = re.search(r"egfr\s*(?:<|≤|低于)\s*30", text, flags=re.IGNORECASE)
    if egfr is not None:
        signals.append("egfr_below_30")
        excerpts.append(egfr.group())
    return list(dict.fromkeys(signals)), list(dict.fromkeys(excerpts))


def assess_medical_risk(question: str) -> MedicalRiskAssessment:
    """根据主体、时间、否定和问题类型执行医疗风险分级。"""
    text = str(question or "").strip().casefold()
    (
        emergency,
        negated_emergency,
        emergency_excerpts,
        negated_emergency_excerpts,
    ) = _signal_matches(text, _EMERGENCY_SIGNALS)
    blood_pressure, blood_pressure_excerpts = _blood_pressure_signals(text)
    context = _classify_context(
        text=text,
        positive_emergency=emergency,
        negated_emergency=negated_emergency,
        blood_pressure_signals=blood_pressure,
    )
    emergency_signals = list(dict.fromkeys([*emergency, *blood_pressure]))
    emergency_trigger_excerpts = list(
        dict.fromkeys([*emergency_excerpts, *blood_pressure_excerpts])
    )
    if emergency_signals and context == "current_self_report":
        return MedicalRiskAssessment(
            risk_level="emergency",
            action="urgent_redirect",
            context_type=context,
            matched_signals=emergency_signals,
            trigger_excerpts=emergency_trigger_excerpts,
        )
    if emergency_signals and context in {"uncertain", "third_party_case"}:
        reason = (
            "third_party_case_requires_direct_confirmation"
            if context == "third_party_case"
            else "current_self_report_not_confirmed"
        )
        return MedicalRiskAssessment(
            risk_level="high",
            action="safety_caution",
            context_type=context,
            matched_signals=emergency_signals,
            trigger_excerpts=emergency_trigger_excerpts,
            uncertainty_reasons=[reason],
            minimum_distinct_publications=2,
        )

    high_risk, high_risk_excerpts = _high_risk_signals(text)
    if high_risk:
        return MedicalRiskAssessment(
            risk_level="high",
            action="evidence_required",
            context_type=context,
            matched_signals=list(dict.fromkeys(high_risk)),
            trigger_excerpts=list(dict.fromkeys(high_risk_excerpts)),
            minimum_distinct_publications=2,
        )

    reportable_emergency = (
        emergency_signals
        if context in {"knowledge_query", "historical_self_report"}
        else negated_emergency
    )
    reportable_excerpts = (
        emergency_trigger_excerpts
        if context in {"knowledge_query", "historical_self_report"}
        else negated_emergency_excerpts
    )
    return MedicalRiskAssessment(
        context_type=context,
        matched_signals=reportable_emergency,
        trigger_excerpts=reportable_excerpts,
    )
