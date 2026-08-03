"""基于结构化 claim frame 的 Evidence 冲突检测。"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from decimal import Decimal
from typing import Literal

from app.a2a.answer_context import evidence_citation
from app.a2a.evidence_fusion import evidence_identity, publication_identity
from app.a2a.models import Evidence, EvidenceClaimFrame, EvidenceConflict
from app.a2a.numeric_claims import extract_structured_numeric_claims

RecommendationPolarity = Literal[
    "unknown", "recommend", "consider", "discourage", "contraindicated"
]
TreatmentPriority = Literal["", "first_line", "second_line", "third_line"]

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
_INTERVENTION_ALIASES = (
    ("acei", ("acei", "普利类")),
    ("arb", ("arb", "沙坦类")),
    ("ccb", ("ccb", "钙通道阻滞剂")),
    ("beta_blocker", ("β受体阻滞剂", "β阻滞剂", "beta受体阻滞剂")),
    ("diuretic", ("利尿剂",)),
    ("combination_therapy", ("联合治疗", "联合用药")),
    ("monotherapy", ("单药治疗", "单药用药")),
)
_POLARITY_PATTERNS: tuple[tuple[RecommendationPolarity, re.Pattern[str]], ...] = (
    ("contraindicated", re.compile(r"(?:禁忌|禁止|不得使用|应避免|避免使用)")),
    ("discourage", re.compile(r"(?:不推荐|不建议|不应使用)")),
    ("consider", re.compile(r"(?:可考虑|可以考虑)")),
    ("recommend", re.compile(r"(?:推荐|建议|应使用|首选)")),
)
_PRIORITY_PATTERNS: tuple[tuple[TreatmentPriority, re.Pattern[str]], ...] = (
    ("first_line", re.compile(r"(?:一线|首选)")),
    ("second_line", re.compile(r"(?:二线|备选)")),
    ("third_line", re.compile(r"三线")),
)
_EVIDENCE_GRADE_PATTERNS = (
    re.compile(r"证据(?:等级|级别)\s*[:：]?\s*([A-E])", flags=re.IGNORECASE),
    re.compile(r"([A-E])\s*级证据", flags=re.IGNORECASE),
)
_GUIDELINE_VERSION_PATTERN = re.compile(r"(?<!\d)((?:19|20)\d{2})(?:\s*年)?(?:\s*版)?")
_NORMALIZED_UNIT_BY_FAMILY = {
    "blood_pressure": "mmhg",
    "percentage": "percent",
    "dose_mass": "mg",
    "concentration": "mmol/l",
    "frequency": "times_per_day",
    "duration": "days",
    "age": "years",
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


def _recommendation_polarity(sentence: str) -> RecommendationPolarity:
    for polarity, pattern in _POLARITY_PATTERNS:
        if pattern.search(sentence):
            return polarity
    return "unknown"


def _treatment_priority(sentence: str) -> TreatmentPriority:
    for priority, pattern in _PRIORITY_PATTERNS:
        if pattern.search(sentence):
            return priority
    return ""


def _evidence_grade(sentence: str) -> str:
    for pattern in _EVIDENCE_GRADE_PATTERNS:
        matched = pattern.search(sentence)
        if matched is not None:
            return matched.group(1).upper()
    return ""


def _guideline_metadata(evidence: Evidence, sentence: str) -> tuple[str, str]:
    source_identity = ""
    for candidate in (evidence.origin_guideline_id, evidence.origin_document_id):
        if candidate.strip():
            source_identity = candidate.strip()
            break
    versions = _GUIDELINE_VERSION_PATTERN.findall(f"{source_identity} {sentence}")
    version = versions[0] if versions else ""
    if not source_identity:
        return "", version
    family = _GUIDELINE_VERSION_PATTERN.sub("", source_identity)
    family = re.sub(
        r"(?:version|版本|第\s*版|v)(?=\W|$)", "", family, flags=re.IGNORECASE
    )
    return _normalized(family), version


def _intervention(sentence: str) -> str:
    interventions = _canonical_terms(sentence, _INTERVENTION_ALIASES)
    if interventions:
        return interventions[0]
    matched = re.search(
        r"(?:禁忌|禁止|不得|不推荐|不建议|不应|可考虑|可以考虑|推荐|建议|首选)"
        r"(?:使用|采用)?\s*([A-Za-z][A-Za-z0-9-]{1,20})",
        sentence,
        flags=re.IGNORECASE,
    )
    return _normalized(matched.group(1)) if matched is not None else ""


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
            polarity = _recommendation_polarity(sentence)
            intervention = _intervention(sentence)
            treatment_priority = _treatment_priority(sentence)
            evidence_grade = _evidence_grade(sentence)
            guideline_family, guideline_version = _guideline_metadata(
                evidence, sentence
            )
            for index, numeric_claim in enumerate(
                extract_structured_numeric_claims(sentence)
            ):
                if numeric_claim.role in {"applicability", "unknown"}:
                    continue
                payload = (
                    f"{evidence_identity(evidence)}|{numeric_claim.local_text}|"
                    f"{index}|{numeric_claim.model_dump_json()}"
                )
                digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
                frames.append(
                    EvidenceClaimFrame(
                        frame_id=f"frame-{digest}",
                        evidence_id=evidence_identity(evidence),
                        publication_id=publication,
                        citation=citation,
                        sentence=numeric_claim.local_text,
                        topic=numeric_claim.subject,
                        subject=numeric_claim.subject,
                        predicate=numeric_claim.predicate,
                        comparator=numeric_claim.comparator,
                        numeric_role=numeric_claim.role,
                        numeric_value=numeric_claim.raw_value,
                        normalized_value=numeric_claim.normalized_value,
                        unit=numeric_claim.unit,
                        normalized_unit=_NORMALIZED_UNIT_BY_FAMILY[
                            numeric_claim.unit_family
                        ],
                        population=numeric_claim.population,
                        measurement_context=numeric_claim.measurement_context,
                        intervention=numeric_claim.intervention or intervention,
                        polarity=polarity,
                        treatment_priority=treatment_priority,
                        evidence_grade=evidence_grade,
                        guideline_family=guideline_family,
                        guideline_version=guideline_version,
                    )
                )
            if not intervention or (
                polarity == "unknown" and not treatment_priority and not evidence_grade
            ):
                continue
            payload = f"{evidence_identity(evidence)}|{sentence}|recommendation"
            digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
            frames.append(
                EvidenceClaimFrame(
                    frame_id=f"frame-{digest}",
                    evidence_id=evidence_identity(evidence),
                    publication_id=publication,
                    citation=citation,
                    sentence=sentence,
                    topic=f"intervention:{intervention}",
                    population=population,
                    measurement_context=measurement_context,
                    intervention=intervention,
                    polarity=polarity,
                    treatment_priority=treatment_priority,
                    evidence_grade=evidence_grade,
                    guideline_family=guideline_family,
                    guideline_version=guideline_version,
                )
            )
    return frames


def _applicability_key(frame: EvidenceClaimFrame) -> str:
    population = ",".join(sorted(frame.population)) or "general"
    measurement = ",".join(sorted(frame.measurement_context)) or "general"
    topic = (
        f"{frame.subject}:{frame.predicate}:role={frame.numeric_role}"
        if frame.numeric_value
        else frame.topic
    )
    intervention = frame.intervention or "general"
    return (
        f"{topic}:population={population}:measurement={measurement}:"
        f"intervention={intervention}"
    )


def _numeric_parts(value: str) -> list[Decimal]:
    return [Decimal(part) for part in re.split(r"[/\-]", value)]


def _values_equivalent(frames: list[EvidenceClaimFrame]) -> bool:
    reference = _numeric_parts(frames[0].normalized_value)
    tolerance = (
        Decimal("1") if frames[0].normalized_unit == "mmhg" else Decimal("0.0001")
    )
    for frame in frames[1:]:
        if frame.comparator != frames[0].comparator:
            return False
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
    conflict_type: Literal[
        "numeric",
        "unit",
        "population",
        "measurement_context",
        "recommendation",
        "contraindication",
        "treatment_priority",
        "evidence_grade",
        "guideline_version",
    ],
    claim_key: str,
    frames: list[EvidenceClaimFrame],
    values: list[str] | None = None,
) -> EvidenceConflict:
    ordered_frames = sorted(frames, key=lambda item: item.frame_id)
    if values is None:
        values = sorted(
            {
                (
                    f"{'' if item.comparator == 'eq' else item.comparator}"
                    f"{item.numeric_value}{item.unit}"
                    if item.numeric_value
                    else item.polarity
                )
                for item in ordered_frames
            }
        )
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


def _conclusion_signature(frame: EvidenceClaimFrame) -> str:
    if frame.numeric_value:
        return (
            f"numeric:{frame.comparator}:{frame.normalized_value}:"
            f"{frame.normalized_unit}"
        )
    return (
        f"semantic:{frame.polarity}:{frame.treatment_priority}:"
        f"{frame.evidence_grade}"
    )


def detect_evidence_conflicts(evidence_rows: list[Evidence]) -> list[EvidenceConflict]:
    """只在主题和适用条件对齐后检测跨出版来源数值与单位冲突。"""
    grouped: dict[str, list[EvidenceClaimFrame]] = defaultdict(list)
    extracted_frames = extract_claim_frames(evidence_rows)
    for frame in extracted_frames:
        if frame.numeric_value:
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
    recommendation_groups: dict[str, list[EvidenceClaimFrame]] = defaultdict(list)
    for frame in extracted_frames:
        if frame.polarity != "unknown" and frame.intervention:
            recommendation_groups[_applicability_key(frame)].append(frame)
    for applicability_key, frames in sorted(recommendation_groups.items()):
        if len({item.publication_id for item in frames}) < 2:
            continue
        positive = {
            item.polarity
            for item in frames
            if item.polarity in {"recommend", "consider"}
        }
        negative = {
            item.polarity
            for item in frames
            if item.polarity in {"discourage", "contraindicated"}
        }
        if not positive or not negative:
            continue
        conflict_type: Literal["recommendation", "contraindication"] = (
            "contraindication" if "contraindicated" in negative else "recommendation"
        )
        conflicts.append(
            _build_conflict(
                conflict_type=conflict_type,
                claim_key=applicability_key,
                frames=frames,
            )
        )
    priority_groups: dict[str, list[EvidenceClaimFrame]] = defaultdict(list)
    grade_groups: dict[str, list[EvidenceClaimFrame]] = defaultdict(list)
    version_groups: dict[str, list[EvidenceClaimFrame]] = defaultdict(list)
    for frame in extracted_frames:
        applicability_key = _applicability_key(frame)
        if frame.intervention and frame.treatment_priority:
            priority_groups[applicability_key].append(frame)
        if frame.intervention and frame.evidence_grade:
            grade_groups[applicability_key].append(frame)
        if frame.guideline_family and frame.guideline_version:
            version_groups[f"{frame.guideline_family}:{applicability_key}"].append(
                frame
            )
    for applicability_key, frames in sorted(priority_groups.items()):
        priorities = sorted({str(item.treatment_priority) for item in frames})
        if len({item.publication_id for item in frames}) < 2 or len(priorities) < 2:
            continue
        conflicts.append(
            _build_conflict(
                conflict_type="treatment_priority",
                claim_key=applicability_key,
                frames=frames,
                values=priorities,
            )
        )
    for applicability_key, frames in sorted(grade_groups.items()):
        grades = sorted({item.evidence_grade for item in frames})
        if len({item.publication_id for item in frames}) < 2 or len(grades) < 2:
            continue
        conflicts.append(
            _build_conflict(
                conflict_type="evidence_grade",
                claim_key=applicability_key,
                frames=frames,
                values=grades,
            )
        )
    for applicability_key, frames in sorted(version_groups.items()):
        versions = {item.guideline_version for item in frames}
        signatures = {_conclusion_signature(item) for item in frames}
        if (
            len({item.publication_id for item in frames}) < 2
            or len(versions) < 2
            or len(signatures) < 2
        ):
            continue
        conflicts.append(
            _build_conflict(
                conflict_type="guideline_version",
                claim_key=applicability_key,
                frames=frames,
                values=sorted(
                    {
                        f"{item.guideline_version}:{_conclusion_signature(item)}"
                        for item in frames
                    }
                ),
            )
        )
    return sorted(conflicts, key=lambda item: item.conflict_id)
