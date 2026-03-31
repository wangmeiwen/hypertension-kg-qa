"""
运行时特性开关模块。

用于统一管理分阶段改造的 feature flags，支持：
- 布尔开关解析
- 默认值管理
- 运行时快照输出

"""

import os
from typing import Dict


def _to_bool(value: str, default: bool = False) -> bool:
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def get_feature_flag(name: str, default: bool = False) -> bool:
    """
    获取布尔型 feature flag。

    Args:
        name: 环境变量名
        default: 默认值

    Returns:
        bool: 开关值
    """
    return _to_bool(os.getenv(name, ""), default=default)


def get_runtime_flag_snapshot() -> Dict[str, bool]:
    """
    返回当前关键改造开关快照。

    Returns:
        Dict[str, bool]: 开关名到当前值映射
    """
    return {
        "ENABLE_A2A_ORCHESTRATION": get_feature_flag("ENABLE_A2A_ORCHESTRATION", False),
        "ENABLE_ASYNC_DB_ADAPTER": get_feature_flag("ENABLE_ASYNC_DB_ADAPTER", True),
        "ENABLE_MCP_V2_DISPATCH": get_feature_flag("ENABLE_MCP_V2_DISPATCH", True),
        "ENABLE_LLM_ASYNC_CLIENT": get_feature_flag("ENABLE_LLM_ASYNC_CLIENT", True),
        "ENABLE_HEAVY_API_RATE_LIMIT": get_feature_flag("ENABLE_HEAVY_API_RATE_LIMIT", True),
        "ENABLE_MCP_TOOL_CIRCUIT_BREAKER": get_feature_flag("ENABLE_MCP_TOOL_CIRCUIT_BREAKER", True),
    }
