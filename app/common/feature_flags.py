"""运行时特性开关模块。"""

from __future__ import annotations

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
    return _to_bool(os.getenv(name, ""), default=default)


def get_runtime_flag_snapshot() -> Dict[str, bool]:
    return {
        "ENABLE_A2A_ORCHESTRATION": get_feature_flag("ENABLE_A2A_ORCHESTRATION", True),
        "ENABLE_MCP_V2_DISPATCH": get_feature_flag("ENABLE_MCP_V2_DISPATCH", True),
        "ENABLE_HEAVY_API_RATE_LIMIT": get_feature_flag("ENABLE_HEAVY_API_RATE_LIMIT", True),
        "ENABLE_MCP_TOOL_CIRCUIT_BREAKER": get_feature_flag("ENABLE_MCP_TOOL_CIRCUIT_BREAKER", True),
    }
