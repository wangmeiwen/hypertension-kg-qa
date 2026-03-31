"""
A2A agent/tool/token_config.py 模块。

本模块提供 token_config 相关实现。
"""

import os
from typing import Any, Dict


def _normalize_token_usage(metric_obj: Any) -> Dict[str, int]:
    """将 token 统计对象规范化为整数计数字段。"""
    metric = metric_obj if isinstance(metric_obj, dict) else {}

    prompt_tokens = int(metric.get("prompt_tokens") or 0)
    completion_tokens = int(metric.get("completion_tokens") or 0)
    total_tokens = int(metric.get("total_tokens") or 0)

    if total_tokens <= 0:
        total_tokens = prompt_tokens + completion_tokens

    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def _extract_token_usage(result: Dict[str, Any]) -> Dict[str, int]:
    """从工具结果中提取并规范化 token 使用量。"""
    if not isinstance(result, dict):
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    meta_obj = result.get("meta")
    meta = meta_obj if isinstance(meta_obj, dict) else {}
    metric_obj = meta.get("token_usage")
    metric = metric_obj if isinstance(metric_obj, dict) else {}
    return _normalize_token_usage(metric)


def _env_flag(name: str, default: str = "0") -> bool:
    """读取并解析布尔型环境变量。"""
    value = str(os.getenv(name, default)).strip().lower()
    return value in {"1", "true", "yes", "on"}
