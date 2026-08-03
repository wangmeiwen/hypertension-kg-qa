"""关系类问题的结构化解析。"""

from __future__ import annotations

import re
from typing import Literal

_RELATION_ENDPOINT_PATTERN = re.compile(
    r"^\s*(?P<start>.+?)(?:与|和|到)(?P<end>.+?)(?:之间)?"
    r"(?:有(?:什么|何)|的)?(?:两跳|二跳|三跳|多跳|\d+跳)?"
    r"(?:关系路径|关系|关联|路径)(?:是什么|有哪些|有什么|如何|怎么)?[？?。]*\s*$"
)
_RELATION_SPLIT_PATTERN = re.compile(
    r"(?:和|与|到|之间|有什么|有何|的关系|关系|关联|路径|多跳|如何|怎么|[？?，,。])"
)
_HOP_PATTERN = re.compile(r"(?P<count>\d+)\s*跳")
_DIRECTIONAL_VERB = r"(?:导致|引起|造成|诱发|促进|增加|降低|升高|减少|->|→)"
_FORWARD_ROLE = r"(?:病因|危险因素|风险因素|并发症|结果|结局|后果)"

RelationClaimDirection = Literal["forward", "reverse", "unknown"]


def relation_endpoints(question: str) -> list[str]:
    """提取关系问题的起止实体，不把年龄短语中的“及”当连接词。"""
    text = str(question or "").strip()
    matched = _RELATION_ENDPOINT_PATTERN.match(text)
    if matched:
        return [matched.group("start").strip(), matched.group("end").strip()]
    return [
        part.strip()
        for part in _RELATION_SPLIT_PATTERN.split(text)
        if len(part.strip()) >= 2
    ][:2]


def relation_hop_bounds(question: str) -> tuple[int, int] | None:
    """解析路径问题要求的跳数范围，并限制为可控的 1 到 3 跳。"""
    text = str(question or "").strip()
    if not any(term in text for term in ("路径", "多跳", "几跳")):
        return None
    if any(term in text for term in ("两跳", "二跳")):
        return (2, 2)
    if "三跳" in text:
        return (3, 3)
    matched = _HOP_PATTERN.search(text)
    if matched:
        hops = max(1, min(int(matched.group("count")), 3))
        return (hops, hops)
    return (2, 3)


def relation_direction(raw: dict[str, object]) -> tuple[str, str, str]:
    """从关系或路径 Evidence 原始载荷中提取有序起点、关系和终点。"""
    path_obj = raw.get("path")
    path = path_obj if isinstance(path_obj, list) else []
    edges = [edge for edge in path if isinstance(edge, dict)]
    if edges:
        source = str(edges[0].get("source_name") or "").strip()
        target = str(edges[-1].get("target_name") or "").strip()
        relation_types = [
            str(edge.get("relation_type") or "").strip()
            for edge in edges
            if str(edge.get("relation_type") or "").strip()
        ]
        return source, " -> ".join(relation_types), target
    return (
        str(raw.get("source_name") or "").strip(),
        str(raw.get("relation_type") or raw.get("rel_type") or "").strip(),
        str(raw.get("target_name") or "").strip(),
    )


def relation_claim_direction(
    text: str, *, source: str, target: str
) -> RelationClaimDirection:
    """识别答案陈述相对于 KG 有向边的方向，无法确认时返回 unknown。"""
    normalized = re.sub(r"\s+", "", str(text or ""))
    escaped_source = re.escape(source.strip())
    escaped_target = re.escape(target.strip())
    if not escaped_source or not escaped_target:
        return "unknown"
    if re.search(
        rf"{escaped_source}.{{0,30}}(?:由|被){escaped_target}.{{0,20}}{_DIRECTIONAL_VERB}",
        normalized,
    ):
        return "reverse"
    if re.search(
        rf"{escaped_target}.{{0,30}}(?:由|被){escaped_source}.{{0,20}}{_DIRECTIONAL_VERB}",
        normalized,
    ):
        return "forward"
    if re.search(
        rf"{escaped_source}.{{0,20}}是{escaped_target}的{_FORWARD_ROLE}", normalized
    ):
        return "reverse"
    if re.search(
        rf"{escaped_target}.{{0,20}}是{escaped_source}的(?:并发症|结果|结局|后果)",
        normalized,
    ):
        return "forward"
    if re.search(
        rf"{escaped_target}.{{0,20}}是{escaped_source}的(?:病因|危险因素|风险因素)",
        normalized,
    ):
        return "reverse"
    if re.search(
        rf"{escaped_source}.{{0,20}}是{escaped_target}的(?:病因|危险因素|风险因素)",
        normalized,
    ):
        return "forward"
    source_position = normalized.find(source.strip())
    target_position = normalized.find(target.strip())
    if source_position < 0 or target_position < 0:
        return "unknown"
    if source_position < target_position:
        between = normalized[source_position + len(source.strip()) : target_position]
        return "forward" if re.search(_DIRECTIONAL_VERB, between) else "unknown"
    between = normalized[target_position + len(target.strip()) : source_position]
    return "reverse" if re.search(_DIRECTIONAL_VERB, between) else "unknown"
