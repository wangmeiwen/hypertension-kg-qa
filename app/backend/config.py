"""backend 运行时配置。"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class BackendConfig:
    """后端聚合配置。"""

    allowed_origins: list[str]
    upload_dir: Path
    preload_kg_entity_relation_embedder: bool
    rate_limit_window_seconds: float
    rate_limit_max_keys: int
    heavy_endpoint_limits: dict[str, dict[str, int]]


def env_bool(name: str, default: bool) -> bool:
    """读取布尔环境变量。"""
    raw = str(os.getenv(name, "1" if default else "0")).strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def read_int_env(name: str, default: int, min_value: int, max_value: int) -> int:
    """读取整型环境变量并限制取值范围。"""
    raw = str(os.getenv(name, str(default))).strip()
    try:
        value = int(raw)
    except ValueError:
        value = default
    if value < min_value:
        return min_value
    if value > max_value:
        return max_value
    return value


def read_float_env(name: str, default: float, min_value: float, max_value: float) -> float:
    """读取浮点环境变量并限制取值范围。"""
    raw = str(os.getenv(name, str(default))).strip()
    try:
        value = float(raw)
    except ValueError:
        value = default
    if value < min_value:
        return min_value
    if value > max_value:
        return max_value
    return value


def build_backend_config() -> BackendConfig:
    """构建后端配置对象。"""
    raw_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:5173,http://localhost:3000")
    allowed_origins = [origin.strip() for origin in raw_origins.split(",") if origin.strip()]

    heavy_endpoint_limits: dict[str, dict[str, int]] = {
        "/agent/run": {
            "user_limit": read_int_env("RATE_LIMIT_AGENT_RUN_USER", 12, 1, 5000),
            "session_limit": read_int_env("RATE_LIMIT_AGENT_RUN_SESSION", 18, 1, 5000),
        },
        "/process_with_session": {
            "user_limit": read_int_env("RATE_LIMIT_PROCESS_SESSION_USER", 20, 1, 5000),
            "session_limit": read_int_env("RATE_LIMIT_PROCESS_SESSION_SESSION", 30, 1, 5000),
        },
        "/mcp/invoke": {
            "user_limit": read_int_env("RATE_LIMIT_MCP_INVOKE_USER", 60, 1, 20000),
            "session_limit": read_int_env("RATE_LIMIT_MCP_INVOKE_SESSION", 80, 1, 20000),
        },
    }

    return BackendConfig(
        allowed_origins=allowed_origins,
        upload_dir=Path("uploads"),
        preload_kg_entity_relation_embedder=env_bool("KG_ENTITY_RELATION_PRELOAD_EMBEDDER", True),
        rate_limit_window_seconds=read_float_env(
            "HEAVY_API_RATE_LIMIT_WINDOW_SECONDS",
            60.0,
            1.0,
            3600.0,
        ),
        rate_limit_max_keys=read_int_env(
            "HEAVY_API_RATE_LIMIT_MAX_KEYS",
            20000,
            1000,
            200000,
        ),
        heavy_endpoint_limits=heavy_endpoint_limits,
    )
