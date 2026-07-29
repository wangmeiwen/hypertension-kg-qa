"""提供 A2A 工具调用链路中 token 统计和开关配置的解析工具。"""

import os
from typing import Any, Dict

from app.a2a.telemetry import normalize_token_usage


def _normalize_token_usage(metric_obj: Any) -> Dict[str, int]:
    """将模型或工具返回的 token 统计对象规范化为统一的整数计数字段。"""
    return normalize_token_usage(metric_obj)


def _extract_token_usage(result: Dict[str, Any]) -> Dict[str, int]:
    """从标准工具调用结果的 meta.token_usage 中提取 token 使用量。"""
    if not isinstance(result, dict):
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    meta_obj = result.get("meta")
    meta = meta_obj if isinstance(meta_obj, dict) else {}
    metric_obj = meta.get("token_usage")
    metric = metric_obj if isinstance(metric_obj, dict) else {}
    return _normalize_token_usage(metric)


def _env_flag(name: str, default: str = "0") -> bool:
    """按常见真假值约定读取布尔型环境变量。"""
    value = str(os.getenv(name, default)).strip().lower()
    return value in {"1", "true", "yes", "on"}
