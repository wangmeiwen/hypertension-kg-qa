"""关系类问题的结构化解析。"""

from __future__ import annotations

import re

_RELATION_ENDPOINT_PATTERN = re.compile(
    r"^\s*(?P<start>.+?)(?:与|和|到)(?P<end>.+?)(?:之间)?"
    r"(?:有(?:什么|何)|的)?(?:两跳|二跳|三跳|多跳|\d+跳)?"
    r"(?:关系|关联|路径)(?:是什么|有哪些|有什么|如何|怎么)?[？?。]*\s*$"
)
_RELATION_SPLIT_PATTERN = re.compile(
    r"(?:和|与|到|之间|有什么|有何|的关系|关系|关联|路径|多跳|如何|怎么|[？?，,。])"
)
_HOP_PATTERN = re.compile(r"(?P<count>\d+)\s*跳")


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
