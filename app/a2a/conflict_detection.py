"""基于结构化 claim frame 的 Evidence 冲突检测。"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Literal

from app.a2a.answer_context import evidence_citation
from app.a2a.evidence_fusion import evidence_identity, publication_identity
from app.a2a.models import Evidence, EvidenceClaimFrame, EvidenceConflict

_NUMERIC_PATTERN = re.compile(
    r"(?P<value>\d{2,3}\s*[/／]\s*\d{2,3}|\d+(?:\.\d+)?)\s*"
    r"(?P<unit>%|mm\s*hg|kpa|mg|μg|ug|g|mmol/l|次/天)",
    flags=re.IGNORECASE,
)
_TOPIC_ALIASES = (
    ("systolic_blood_pressure", ("收缩压",)),
    ("diastolic_blood_pressure", ("舒张压",)),
    ("blood_pressure_target", ("血压目标", "目标血压", "目标值", "阈值")),
    ("risk_reduction", ("风险下降", "风险降低")),
    ("risk_increase", ("风险增加", "风险升高")),
    ("dose", ("剂量", "用量")),
    ("prevalence", ("患病率",)),
    ("incidence", ("发生率",)),
)
_POPULATION_ALIASES = (
    ("pregnancy", ("孕妇", "妊娠", "怀孕")),
    ("older_adults", ("老年人", "高龄", "老年患者")),
    ("children", ("儿童", "小儿", "未成年人")),
    ("ckd", ("慢性肾病", "ckd")),
    ("diabetes", ("糖尿病",)),
)
_MEASUREMENT_ALIASES = (
    ("clinic", ("诊室血压", "诊所血压")),
    ("home", ("家庭血压", "居家血压")),
    ("ambulatory", ("动态血压", "24小时血压")),
)
_UNIT_DEFINITIONS = {
    "mmhg": ("mmhg", Decimal("1")),
    "kpa": ("mmhg", Decimal("7.50062")),
    "mg": ("mg", Decimal("1")),
    "g": ("mg", Decimal("1000")),
    "ug": ("mg", Decimal("0.001")),
    "%": ("percent", Decimal("1")),
    "mmol/l": ("mmol/l", Decimal("1")),
    "次/天": ("times_per_day", Decimal("1")),
}


def _normalized(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", value.casefold())


def _canonical_terms(
    sentence: str, aliases: tuple[tuple[str, tuple[str, ...]], ...]
) -> list[str]:
    lowered = sentence.casefold()
    return [
        canonical
        for canonical, terms in aliases
        if any(term.casefold() in lowered for term in terms)
    ]


def _topic(sentence: str) -> str:
    topics = _canonical_terms(sentence, _TOPIC_ALIASES)
    if topics:
        return topics[0]
    replaced = _NUMERIC_PATTERN.sub("numeric_value", sentence)
    return _normalized(replaced) or "unknown"


def _canonical_unit(raw_unit: str) -> tuple[str, Decimal]:
    unit = re.sub(r"\s+", "", raw_unit.casefold()).replace("μ", "u")
    return _UNIT_DEFINITIONS[unit]


def _format_decimal(value: Decimal) -> str:
    rounded = value.quantize(Decimal("0.0001"))
    return format(rounded.normalize(), "f")


def _normalized_numeric_value(raw_value: str, *, factor: Decimal) -> str | None:
    parts = re.split(r"[/／]", raw_value)
    try:
        normalized = [_format_decimal(Decimal(part.strip()) * factor) for part in parts]
    except InvalidOperation:
        return None
    return "/".join(normalized)


def extract_claim_frames(evidence_rows: list[Evidence]) -> list[EvidenceClaimFrame]:
    """从具有出版来源的 Evidence 中提取可比较数值 claim frame。"""
    frames: list[EvidenceClaimFrame] = []
    for evidence in evidence_rows:
        publication = publication_identity(evidence)
        if publication is None:
            continue
        citation = evidence_citation(evidence)
        for sentence in re.split(r"[。！？!?；;\n]+", evidence.content):
            sentence = sentence.strip(" ，,")
            if not sentence:
                continue
            population = _canonical_terms(sentence, _POPULATION_ALIASES)
            measurement_context = _canonical_terms(sentence, _MEASUREMENT_ALIASES)
            for matched in _NUMERIC_PATTERN.finditer(sentence):
                unit, factor = _canonical_unit(matched.group("unit"))
                raw_value = re.sub(r"\s+", "", matched.group("value"))
                normalized_value = _normalized_numeric_value(raw_value, factor=factor)
                if normalized_value is None:
                    continue
                payload = (
                    f"{evidence_identity(evidence)}|{sentence}|{matched.start()}|"
                    f"{matched.end()}"
                )
                digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
                frames.append(
                    EvidenceClaimFrame(
                        frame_id=f"frame-{digest}",
                        evidence_id=evidence_identity(evidence),
                        publication_id=publication,
                        citation=citation,
                        sentence=sentence,
                        topic=_topic(sentence),
                        numeric_value=raw_value,
                        normalized_value=normalized_value,
                        unit=re.sub(r"\s+", "", matched.group("unit")).casefold(),
                        normalized_unit=unit,
                        population=population,
                        measurement_context=measurement_context,
                    )
                )
    return frames


def _applicability_key(frame: EvidenceClaimFrame) -> str:
    population = ",".join(sorted(frame.population)) or "general"
    measurement = ",".join(sorted(frame.measurement_context)) or "general"
    return f"{frame.topic}:population={population}:measurement={measurement}"


def _numeric_parts(value: str) -> list[Decimal]:
    return [Decimal(part) for part in value.split("/")]


def _values_equivalent(frames: list[EvidenceClaimFrame]) -> bool:
    reference = _numeric_parts(frames[0].normalized_value)
    tolerance = (
        Decimal("1") if frames[0].normalized_unit == "mmhg" else Decimal("0.0001")
    )
    for frame in frames[1:]:
        candidate = _numeric_parts(frame.normalized_value)
        if len(candidate) != len(reference):
            return False
        if any(
            abs(left - right) > tolerance for left, right in zip(reference, candidate)
        ):
            return False
    return True


def _build_conflict(
    *,
    conflict_type: Literal["numeric", "unit", "population", "measurement_context"],
    claim_key: str,
    frames: list[EvidenceClaimFrame],
) -> EvidenceConflict:
    ordered_frames = sorted(frames, key=lambda item: item.frame_id)
    values = sorted({f"{item.numeric_value}{item.unit}" for item in ordered_frames})
    sources = sorted({item.publication_id for item in ordered_frames})
    citations = sorted({item.citation for item in ordered_frames if item.citation})
    digest = hashlib.sha256(
        f"{conflict_type}|{claim_key}|{'|'.join(values)}|{'|'.join(sources)}".encode(
            "utf-8"
        )
    ).hexdigest()[:12]
    return EvidenceConflict(
        conflict_id=f"conflict-{digest}",
        conflict_type=conflict_type,
        claim_key=claim_key,
        values=values,
        source_ids=sources,
        citations=citations,
        claim_frames=ordered_frames,
    )


def detect_evidence_conflicts(evidence_rows: list[Evidence]) -> list[EvidenceConflict]:
    """只在主题和适用条件对齐后检测跨出版来源数值与单位冲突。"""
    grouped: dict[str, list[EvidenceClaimFrame]] = defaultdict(list)
    for frame in extract_claim_frames(evidence_rows):
        grouped[_applicability_key(frame)].append(frame)

    conflicts: list[EvidenceConflict] = []
    for applicability_key, frames in sorted(grouped.items()):
        if len({item.publication_id for item in frames}) < 2:
            continue
        unit_groups: dict[str, list[EvidenceClaimFrame]] = defaultdict(list)
        for frame in frames:
            unit_groups[frame.normalized_unit].append(frame)
        if len(unit_groups) > 1:
            conflicts.append(
                _build_conflict(
                    conflict_type="unit",
                    claim_key=applicability_key,
                    frames=frames,
                )
            )
            continue
        comparable_frames = next(iter(unit_groups.values()))
        if len({item.publication_id for item in comparable_frames}) < 2:
            continue
        if not _values_equivalent(comparable_frames):
            conflicts.append(
                _build_conflict(
                    conflict_type="numeric",
                    claim_key=(
                        f"{applicability_key}:unit="
                        f"{comparable_frames[0].normalized_unit}"
                    ),
                    frames=comparable_frames,
                )
            )
    return sorted(conflicts, key=lambda item: item.conflict_id)
