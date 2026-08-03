"""建议类结论的极性提取与冲突判断。"""

from __future__ import annotations

import re
from typing import Literal

RecommendationPolarity = Literal[
    "unknown", "recommend", "consider", "discourage", "contraindicated"
]

_POLARITY_PATTERNS: tuple[tuple[RecommendationPolarity, re.Pattern[str]], ...] = (
    ("contraindicated", re.compile(r"(?:禁忌|禁止|不得使用|应避免|避免使用)")),
    (
        "discourage",
        re.compile(
            r"(?:不推荐|不建议|不应使用|无益|未见获益|没有获益|不利于|有害|"
            r"不能改善|增加[^。；，,]{0,20}风险|恶化)"
        ),
    ),
    ("consider", re.compile(r"(?:可考虑|可以考虑)")),
    ("recommend", re.compile(r"(?:推荐|建议|应使用|首选)")),
    (
        "recommend",
        re.compile(
            r"(?:有助于|有益于?|获益|改善|提升|提高|可用于|"
            r"降低[^。；，,]{0,20}风险|减少[^。；，,]{0,20}风险)"
        ),
    ),
)
_POSITIVE_POLARITIES = {"recommend", "consider"}
_NEGATIVE_POLARITIES = {"discourage", "contraindicated"}


def recommendation_polarity(text: str) -> RecommendationPolarity:
    """提取建议结论的方向，优先识别否定和禁忌。"""
    for polarity, pattern in _POLARITY_PATTERNS:
        if pattern.search(text):
            return polarity
    return "unknown"


def recommendation_polarities_conflict(
    left: RecommendationPolarity, right: RecommendationPolarity
) -> bool:
    """判断两个明确建议是否一个正向、一个负向。"""
    return (left in _POSITIVE_POLARITIES and right in _NEGATIVE_POLARITIES) or (
        left in _NEGATIVE_POLARITIES and right in _POSITIVE_POLARITIES
    )
