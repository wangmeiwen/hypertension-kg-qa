"""统一的结构化数值 Claim 提取与匹配。"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Literal

from app.a2a.models import (
    NumericClaimRequirement,
    NumericMentionRole,
    StructuredNumericClaim,
)

_SENTENCE_SPLIT_PATTERN = re.compile(r"[。！？!?\n]+")
_LOCAL_CLAUSE_SPLIT_PATTERN = re.compile(r"[，,；;]+")
_NUMERIC_PATTERN = re.compile(
    r"(?P<comparator>≤|>=|≥|<=|＜|<|＞|>|低于|小于|不超过|以下|"
    r"高于|大于|至少|不低于|以上)?\s*"
    r"(?P<value>\d{2,3}\s*[/／]\s*\d{2,3}|"
    r"\d+(?:\.\d+)?\s*(?:-|—|~|～|至|到)\s*\d+(?:\.\d+)?|"
    r"\d+(?:\.\d+)?)\s*"
    r"(?P<unit>mm\s*hg|毫米汞柱|kpa|mmol/l|mg|μg|ug|g|%|岁|年|天|d|"
    r"次\s*[/／]\s*(?:日|天)|次|个读数)",
    flags=re.IGNORECASE,
)
_MEASUREMENT_ALIASES = (
    ("clinic", ("诊室血压", "诊所血压")),
    ("home", ("家庭血压", "居家血压", "家庭测血压", "hbpm")),
    ("ambulatory", ("动态血压", "24小时血压", "夜间血压", "abpm")),
)
_POPULATION_ALIASES = (
    ("older_adults", ("老年人", "高龄", "老年患者")),
    ("pregnancy", ("孕妇", "妊娠", "怀孕")),
    ("children", ("儿童", "小儿", "未成年人")),
    ("ckd", ("慢性肾病", "ckd")),
    ("diabetes", ("糖尿病",)),
)
_INTERVENTION_PATTERN = re.compile(
    r"([A-Za-z][A-Za-z0-9-]{1,30}|[\u4e00-\u9fff]{2,16})(?:的)?(?:剂量|用量)",
    flags=re.IGNORECASE,
)
_INTERVENTION_STOP_WORDS = {"推荐", "起始", "常用", "每日", "药物", "用药"}
_UNIT_DEFINITIONS = {
    "mmhg": ("blood_pressure", Decimal("1")),
    "毫米汞柱": ("blood_pressure", Decimal("1")),
    "kpa": ("blood_pressure", Decimal("7.50062")),
    "%": ("percentage", Decimal("1")),
    "mg": ("dose_mass", Decimal("1")),
    "g": ("dose_mass", Decimal("1000")),
    "ug": ("dose_mass", Decimal("0.001")),
    "mmol/l": ("concentration", Decimal("1")),
    "岁": ("age", Decimal("1")),
    "年": ("duration", Decimal("365")),
    "天": ("duration", Decimal("1")),
    "d": ("duration", Decimal("1")),
    "次/日": ("frequency", Decimal("1")),
    "次/天": ("frequency", Decimal("1")),
    "次": ("frequency", Decimal("1")),
    "个读数": ("frequency", Decimal("1")),
}


def normalize_numeric_ocr_text(text: str) -> str:
    """仅归一化数值和单位内部的 OCR 空格，不改写普通自然语言。"""
    normalized = str(text or "")
    normalized = re.sub(r"(?<=\d)\s+(?=\d)", "", normalized)
    normalized = re.sub(r"(?<=\d)\s*([/／])\s*(?=\d)", r"\1", normalized)
    normalized = re.sub(r"m\s*m\s*h\s*g", "mmHg", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"k\s*p\s*a", "kPa", normalized, flags=re.IGNORECASE)
    normalized = re.sub(
        r"m\s*m\s*o\s*l\s*/\s*l", "mmol/l", normalized, flags=re.IGNORECASE
    )
    return normalized


def _canonical_terms(
    text: str, aliases: tuple[tuple[str, tuple[str, ...]], ...]
) -> list[str]:
    lowered = text.casefold()
    return [
        canonical
        for canonical, terms in aliases
        if any(term.casefold() in lowered for term in terms)
    ]


def _canonical_unit(raw_unit: str) -> tuple[str, Decimal, str]:
    unit = re.sub(r"\s+", "", raw_unit.casefold()).replace("μ", "u").replace("／", "/")
    family, factor = _UNIT_DEFINITIONS[unit]
    return family, factor, unit


def _format_decimal(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.0001")).normalize(), "f")


def _normalize_value(raw_value: str, factor: Decimal) -> str | None:
    parts = re.split(r"[/／]|\s*(?:-|—|~|～|至|到)\s*", raw_value)
    try:
        normalized = [_format_decimal(Decimal(part.strip()) * factor) for part in parts]
    except InvalidOperation:
        return None
    separator = "/" if re.search(r"[/／]", raw_value) else "-"
    return separator.join(normalized)


def _comparator(
    raw_comparator: str, raw_value: str
) -> Literal["eq", "lt", "lte", "gt", "gte", "range"]:
    if re.search(r"(?:-|—|~|～|至|到)", raw_value):
        return "range"
    if raw_comparator in {"＜", "<", "低于", "小于"}:
        return "lt"
    if raw_comparator in {"≤", "<=", "不超过", "以下"}:
        return "lte"
    if raw_comparator in {"＞", ">", "高于", "大于"}:
        return "gt"
    if raw_comparator in {"≥", ">=", "至少", "不低于", "以上"}:
        return "gte"
    return "eq"


def _subject_and_predicate(
    text: str, unit_family: str, predicate_context: str = ""
) -> tuple[str, str]:
    if unit_family == "frequency":
        return "blood_pressure_monitoring", "frequency"
    if unit_family == "duration":
        return "blood_pressure_monitoring", "duration"
    if any(term in text for term in ("风险下降", "风险降低")):
        return "risk_reduction", "change"
    if any(term in text for term in ("风险增加", "风险升高")):
        return "risk_increase", "change"
    if any(term in text for term in ("患病率", "发生率")):
        subject = "prevalence" if "患病率" in text else "incidence"
        return subject, subject
    if any(term in text for term in ("剂量", "用量")):
        return "medication_dose", "dose"
    if "收缩压" in text:
        subject = "systolic_blood_pressure"
    elif "舒张压" in text:
        subject = "diastolic_blood_pressure"
    elif "血压" in text:
        subject = "blood_pressure"
    else:
        subject = ""
    predicate_text = text
    if not any(
        term in predicate_text
        for term in (
            "诊断",
            "判断",
            "界值",
            "诊断标准",
            "目标",
            "控制在",
            "降至",
            "定义",
            "急症",
        )
    ):
        predicate_text = predicate_context
    if (
        any(term in predicate_text for term in ("诊断", "判断", "界值", "诊断标准"))
        or "高血压的定义" in predicate_text
        and "急症" not in predicate_text
    ):
        predicate = "diagnostic_threshold"
    elif any(term in predicate_text for term in ("目标", "控制在", "降至")):
        predicate = "target"
    elif any(term in predicate_text for term in ("定义", "急症")):
        predicate = "definition_threshold"
    else:
        predicate = ""
    if not subject and unit_family == "blood_pressure" and predicate:
        subject = "blood_pressure"
    return subject, predicate


def _intervention(text: str) -> str:
    matched = _INTERVENTION_PATTERN.search(text)
    if matched is None:
        return ""
    candidate = matched.group(1).casefold()
    return "" if candidate in _INTERVENTION_STOP_WORDS else candidate


def _role(unit_family: str, subject: str, predicate: str) -> NumericMentionRole:
    if unit_family == "age":
        return "applicability"
    if unit_family == "duration":
        return "temporal"
    if unit_family == "frequency":
        return "frequency"
    if subject and predicate:
        return "claim_value"
    return "unknown"


def extract_structured_numeric_claims(text: str) -> list[StructuredNumericClaim]:
    """按局部子句提取数值，避免把年龄、年份和频次当作目标值。"""
    claims: list[StructuredNumericClaim] = []
    normalized_text = normalize_numeric_ocr_text(text)
    for sentence in _SENTENCE_SPLIT_PATTERN.split(normalized_text):
        sentence = sentence.strip()
        if not sentence:
            continue
        population = _canonical_terms(sentence, _POPULATION_ALIASES)
        measurement_context = _canonical_terms(sentence, _MEASUREMENT_ALIASES)
        for clause in _LOCAL_CLAUSE_SPLIT_PATTERN.split(sentence):
            clause = clause.strip()
            if not clause:
                continue
            for matched in _NUMERIC_PATTERN.finditer(clause):
                unit_family, factor, unit = _canonical_unit(matched.group("unit"))
                raw_value = re.sub(r"\s+", "", matched.group("value"))
                normalized_value = _normalize_value(raw_value, factor)
                if normalized_value is None:
                    continue
                subject, predicate = _subject_and_predicate(
                    clause, unit_family, sentence
                )
                role = _role(unit_family, subject, predicate)
                claim = StructuredNumericClaim(
                    subject=subject,
                    predicate=predicate,
                    raw_value=raw_value,
                    normalized_value=normalized_value,
                    comparator=_comparator(
                        matched.group("comparator") or "", raw_value
                    ),
                    unit=unit,
                    unit_family=unit_family,
                    population=population,
                    measurement_context=measurement_context,
                    intervention=_intervention(clause) or _intervention(sentence),
                    role=role,
                    local_text=clause,
                )
                if unit_family == "blood_pressure" and "/" in normalized_value:
                    systolic, diastolic = normalized_value.split("/", maxsplit=1)
                    claims.extend(
                        [
                            claim.model_copy(
                                update={
                                    "subject": "systolic_blood_pressure",
                                    "raw_value": re.split(r"[/／]", raw_value)[0],
                                    "normalized_value": systolic,
                                }
                            ),
                            claim.model_copy(
                                update={
                                    "subject": "diastolic_blood_pressure",
                                    "raw_value": re.split(r"[/／]", raw_value)[1],
                                    "normalized_value": diastolic,
                                }
                            ),
                        ]
                    )
                else:
                    claims.append(claim)
    return claims


def extract_numeric_requirements(question: str) -> list[NumericClaimRequirement]:
    """把问题拆成必须分别满足的数值语义槽位。"""
    text = str(question or "")
    measurement_context = _canonical_terms(text, _MEASUREMENT_ALIASES)
    requirements: list[NumericClaimRequirement] = []
    if any(term in text for term in ("几天", "多少天", "多久", "连续天数")):
        requirements.append(
            NumericClaimRequirement(
                subject="blood_pressure_monitoring",
                predicate="duration",
                unit_family="duration",
                role="temporal",
                measurement_context=measurement_context,
            )
        )
    if any(term in text for term in ("几次", "多少次", "频次", "频率")):
        requirements.append(
            NumericClaimRequirement(
                subject="blood_pressure_monitoring",
                predicate="frequency",
                unit_family="frequency",
                role="frequency",
                measurement_context=measurement_context,
            )
        )
    if any(term in text for term in ("患病率", "发生率")):
        requirements.append(
            NumericClaimRequirement(
                subject="prevalence",
                predicate="prevalence",
                unit_family="percentage",
                role="claim_value",
            )
        )
    if any(term in text for term in ("剂量", "用量")):
        requirements.append(
            NumericClaimRequirement(
                subject="medication_dose",
                predicate="dose",
                unit_family="dose_mass",
                role="claim_value",
                intervention=_intervention(text),
            )
        )
    if "血压" in text or "收缩压" in text or "舒张压" in text:
        predicate = (
            "diagnostic_threshold"
            if any(term in text for term in ("诊断", "判断", "界值", "诊断标准"))
            else (
                "definition_threshold"
                if any(term in text for term in ("定义", "急症"))
                else (
                    "target"
                    if any(term in text for term in ("目标", "控制", "降至"))
                    else ""
                )
            )
        )
        if predicate:
            subjects = []
            if "收缩压" in text:
                subjects.append("systolic_blood_pressure")
            if "舒张压" in text:
                subjects.append("diastolic_blood_pressure")
            if not subjects:
                subjects.append("blood_pressure")
            requirements.extend(
                NumericClaimRequirement(
                    subject=subject,
                    predicate=predicate,
                    unit_family="blood_pressure",
                    role="claim_value",
                    measurement_context=measurement_context,
                )
                for subject in subjects
            )
    return requirements


def numeric_claim_matches_requirement(
    claim: StructuredNumericClaim, requirement: NumericClaimRequirement
) -> bool:
    """判断局部数值 Claim 是否完整满足问题槽位。"""
    subject_matches = claim.subject == requirement.subject or (
        requirement.subject == "blood_pressure"
        and claim.subject
        in {
            "blood_pressure",
            "systolic_blood_pressure",
            "diastolic_blood_pressure",
        }
    )
    return (
        claim.role == requirement.role
        and claim.unit_family == requirement.unit_family
        and subject_matches
        and claim.predicate == requirement.predicate
        and set(requirement.measurement_context).issubset(claim.measurement_context)
        and (
            not requirement.intervention
            or claim.intervention == requirement.intervention
        )
    )
