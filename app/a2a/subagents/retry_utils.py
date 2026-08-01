"""子代理多轮检索辅助函数。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

MAX_SUBAGENT_TOOL_CALLS = 3

_STOP_WORDS = (
    "哪些",
    "什么",
    "如何",
    "怎么",
    "是否",
    "以及",
    "这些",
    "这个",
    "那个",
    "时候",
    "优先考虑",
    "代表",
    "类别",
    "药物类别",
    "有哪些",
)


def normalize_query_terms(query_terms: Any) -> list[str]:
    """标准化查询词列表。"""
    if not isinstance(query_terms, list):
        return []
    seen: set[str] = set()
    terms: list[str] = []
    for item in query_terms:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        terms.append(text)
    return terms


def compact_query_text(text: str) -> str:
    """把自然语言问题压成更适合检索的关键词串。"""
    value = str(text or "").strip()
    if not value:
        return ""
    for word in _STOP_WORDS:
        value = value.replace(word, " ")
    value = re.sub(r"[，。！？；：、,!?;:/\\|（）()\[\]{}<>\"'“”‘’]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    value = re.sub(r"(合并|伴有|伴|且有|且|并有|时|的|和|与|及|或)", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def unique_texts(rows: list[str]) -> list[str]:
    """去掉空字符串和重复文本，保持原顺序。"""
    seen: set[str] = set()
    result: list[str] = []
    for row in rows:
        text = str(row or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def build_query_variants(
    *,
    question: str,
    arguments: dict[str, Any],
    query_terms: list[str],
    evidence_terms: list[str] | None = None,
) -> list[str]:
    """按尝试次数构造检索语句候选。"""
    args = arguments if isinstance(arguments, dict) else {}
    terms = normalize_query_terms(query_terms)
    evidence = normalize_query_terms(
        evidence_terms if isinstance(evidence_terms, list) else []
    )
    base = str(args.get("query") or args.get("keyword") or question or "").strip()
    compact_base = compact_query_text(base)
    compact_question = compact_query_text(question)
    term_query = " ".join(terms).strip()
    seed = compact_base or compact_question or base
    evidence_query = " ".join([compact_question, *evidence[:4]]).strip()
    guideline_query = " ".join([seed, "指南 推荐 证据"]).strip()
    treatment_query = " ".join([seed, "治疗 用药 标准"]).strip()
    return unique_texts(
        [
            base,
            term_query,
            compact_base,
            compact_question,
            evidence_query,
            guideline_query,
            treatment_query,
        ]
    )


def query_variant_for_attempt(variants: list[str], attempt_index: int) -> str:
    """根据第几次尝试选择查询文本。"""
    if not variants:
        return ""
    index = max(0, min(int(attempt_index) - 1, len(variants) - 1))
    return variants[index]


def should_continue_querying(state: Mapping[str, Any]) -> bool:
    """判断子代理是否还应继续下一次检索。"""
    constraints_obj = state.get("constraints")
    constraints = constraints_obj if isinstance(constraints_obj, dict) else {}
    max_tool_calls = int(constraints.get("max_tool_calls") or MAX_SUBAGENT_TOOL_CALLS)
    max_tool_calls = max(1, min(max_tool_calls, MAX_SUBAGENT_TOOL_CALLS))
    tool_results_obj = state.get("tool_results")
    tool_results = tool_results_obj if isinstance(tool_results_obj, list) else []
    evidence_obj = state.get("evidence")
    evidence = evidence_obj if isinstance(evidence_obj, list) else []
    if len(tool_results) >= max_tool_calls:
        return False
    return len(evidence) < 3
