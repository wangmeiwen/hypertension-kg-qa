"""适用人群与测量场景的确定性归一化。"""

from __future__ import annotations

import re
from collections.abc import Iterable


def canonical_applicability_condition(value: str) -> str:
    """把计划和 Evidence 中的常见同义条件统一为稳定标签。"""
    normalized = re.sub(r"\s+", "", str(value or "").strip())
    if any(term in normalized for term in ("妊娠", "怀孕", "孕期", "孕妇")):
        return "妊娠"
    return normalized


def canonical_applicability_conditions(values: Iterable[str]) -> list[str]:
    """按出现顺序归一化并去重适用条件。"""
    return list(
        dict.fromkeys(
            condition
            for value in values
            if (condition := canonical_applicability_condition(value))
        )
    )


def applicability_condition_present(condition: str, text: str) -> bool:
    """判断答案是否使用规范标签或等价同义词明确写出适用条件。"""
    canonical = canonical_applicability_condition(condition)
    normalized_text = re.sub(r"\s+", "", str(text or "").casefold())
    if canonical == "妊娠":
        return any(term in normalized_text for term in ("妊娠", "怀孕", "孕期", "孕妇"))
    return canonical.casefold() in normalized_text
