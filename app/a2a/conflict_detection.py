"""融合 Evidence 的确定性冲突检测。"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict

from app.a2a.evidence_fusion import canonical_source_id
from app.a2a.models import Evidence, EvidenceConflict

_NUMERIC_PATTERN = re.compile(
    r"(?P<value>\d{2,3}\s*[/／]\s*\d{2,3}|\d+(?:\.\d+)?)\s*"
    r"(?P<unit>%|mmhg|mg|μg|ug|g|mmol/l|次/天)",
    flags=re.IGNORECASE,
)
_CLAIM_TERMS = (
    "风险下降",
    "风险增加",
    "收缩压",
    "舒张压",
    "血压目标",
    "目标值",
    "阈值",
    "剂量",
    "患病率",
    "发生率",
)
_APPLICABILITY_TERMS = (
    "孕妇",
    "妊娠",
    "儿童",
    "老年人",
    "高龄",
    "慢性肾病",
    "CKD",
    "糖尿病",
    "诊室血压",
    "家庭血压",
    "动态血压",
)


def _normalized(value: str) -> str:
    """生成用于分组的稳定文本。"""
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", value.casefold())


def _claim_key(sentence: str, unit: str) -> str:
    """按结论主题、适用条件和单位生成冲突分组键。"""
    lowered = sentence.casefold()
    claim_term = next((term for term in _CLAIM_TERMS if term.casefold() in lowered), "")
    conditions = sorted(
        term for term in _APPLICABILITY_TERMS if term.casefold() in lowered
    )
    if not claim_term:
        claim_term = _NUMERIC_PATTERN.sub("数值", sentence)
    return ":".join(
        (
            _normalized(claim_term),
            _normalized(unit),
            ",".join(_normalized(item) for item in conditions) or "general",
        )
    )


def _citation(evidence: Evidence) -> str:
    """生成冲突详情中的引用标签。"""
    if evidence.page is not None:
        return f"[{evidence.source_id} p{evidence.page}]"
    return f"[{evidence.source_id}]" if evidence.source_id else ""


def detect_evidence_conflicts(evidence_rows: list[Evidence]) -> list[EvidenceConflict]:
    """检测同一适用条件下由独立来源给出的不同数值。"""
    claims: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for evidence in evidence_rows:
        source = canonical_source_id(evidence)
        citation = _citation(evidence)
        sentences = re.split(r"[。！？!?；;，,\n]+", evidence.content)
        for sentence in sentences:
            for matched in _NUMERIC_PATTERN.finditer(sentence):
                value = re.sub(r"\s+", "", matched.group("value"))
                unit = matched.group("unit").lower()
                claims[_claim_key(sentence, unit)].append(
                    (f"{value}{unit}", source, citation)
                )

    conflicts: list[EvidenceConflict] = []
    for claim_key, records in sorted(claims.items()):
        values = sorted({value for value, _, _ in records})
        sources = sorted({source for _, source, _ in records})
        if len(values) < 2 or len(sources) < 2:
            continue
        citations = sorted({citation for _, _, citation in records if citation})
        digest = hashlib.sha256(
            f"{claim_key}|{'|'.join(values)}|{'|'.join(sources)}".encode("utf-8")
        ).hexdigest()[:12]
        conflicts.append(
            EvidenceConflict(
                conflict_id=f"conflict-{digest}",
                claim_key=claim_key,
                values=values,
                source_ids=sources,
                citations=citations,
            )
        )
    return conflicts
