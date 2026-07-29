"""A2A 链路共享的 token 与耗时遥测契约。"""

from __future__ import annotations

from typing import Any


TOKEN_USAGE_FIELDS = ("prompt_tokens", "completion_tokens", "total_tokens")


def empty_token_usage() -> dict[str, int]:
    """返回字段完整且可直接累加的 token 计数。"""
    return {field: 0 for field in TOKEN_USAGE_FIELDS}


def normalize_token_usage(metric_obj: Any) -> dict[str, int]:
    """把外部模型或工具的 token 字段规范化为非负整数。"""
    metric = metric_obj if isinstance(metric_obj, dict) else {}
    prompt_tokens = max(0, int(metric.get("prompt_tokens") or 0))
    completion_tokens = max(0, int(metric.get("completion_tokens") or 0))
    total_tokens = max(0, int(metric.get("total_tokens") or 0))
    if total_tokens == 0:
        total_tokens = prompt_tokens + completion_tokens
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def merge_token_usage(
    target: dict[str, int],
    metric_obj: Any,
) -> dict[str, int]:
    """把单次调用 token 计数累加到上层聚合对象。"""
    metric = normalize_token_usage(metric_obj)
    for field in TOKEN_USAGE_FIELDS:
        target[field] += metric[field]
    return target
