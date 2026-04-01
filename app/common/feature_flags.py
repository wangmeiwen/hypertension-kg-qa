"""feature_flags 模块。

提供 feature_flags 相关能力与对外接口。
"""


from __future__ import annotations

import os
from typing import Dict


def _to_bool(value: str, default: bool = False) -> bool:
    """将字符串配置值解析为布尔值。

    支持常见开关写法：
    - 真值：1/true/yes/on
    - 假值：0/false/no/off
    若无法识别，返回调用方提供的默认值。

    Args:
        value: 待解析的原始字符串（通常来自环境变量）。
        default: 无法识别时使用的默认布尔值。

    Returns:
        bool: 解析后的布尔结果。
    """
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def get_feature_flag(name: str, default: bool = False) -> bool:
    """读取并解析单个运行时特性开关。

    该函数从环境变量读取指定开关，再通过 `_to_bool` 做布尔化处理。

    Args:
        name: 环境变量名，例如 ENABLE_A2A_ORCHESTRATION。
        default: 环境变量不存在或值非法时的默认值。

    Returns:
        bool: 该特性开关的最终启用状态。
    """
    return _to_bool(os.getenv(name, ""), default=default)


def get_runtime_flag_snapshot() -> Dict[str, bool]:
    """获取核心运行时特性开关快照。

    用于健康检查、诊断接口或日志中快速展示当前关键开关状态。

    Returns:
        Dict[str, bool]: 以开关名为键、启用状态为值的字典。
    """
    return {
        "ENABLE_A2A_ORCHESTRATION": get_feature_flag("ENABLE_A2A_ORCHESTRATION", True),
        "ENABLE_MCP_V2_DISPATCH": get_feature_flag("ENABLE_MCP_V2_DISPATCH", True),
        "ENABLE_HEAVY_API_RATE_LIMIT": get_feature_flag("ENABLE_HEAVY_API_RATE_LIMIT", True),
        "ENABLE_MCP_TOOL_CIRCUIT_BREAKER": get_feature_flag("ENABLE_MCP_TOOL_CIRCUIT_BREAKER", True),
    }
