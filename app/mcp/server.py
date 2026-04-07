"""MCP 工具调度服务（纯异步单版本）。"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class MCPTool:
    """描述单个 MCP 工具的元信息与调用处理器。"""

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], Any]
    server_name: str = "local"
    tool_name: str = ""


class MCPServer:
    """负责 MCP 工具注册、解析、重试与执行调度。"""

    def __init__(self):
        """初始化 MCP 工具调度服务。

        Returns:
            None: 无返回值。
        """
        self._tools: dict[str, MCPTool] = {}
        self._aliases: dict[str, str] = {}
        self._tool_timeout_seconds = self._resolve_timeout()
        self._tool_max_retries = self._resolve_max_retries()
        self._default_retry_policy = self._normalize_retry_policy(
            str(os.getenv("MCP_TOOL_RETRY_POLICY", "safe")),
            default="safe",
        )
        self._retry_policy_overrides = self._resolve_retry_policy_overrides()
        self._enable_circuit_breaker = self._resolve_bool_env("ENABLE_MCP_TOOL_CIRCUIT_BREAKER", True)
        self._circuit_timeout_threshold = self._resolve_int("MCP_CIRCUIT_TIMEOUT_THRESHOLD", 3, 1, 20)
        self._circuit_window_seconds = float(self._resolve_int("MCP_CIRCUIT_WINDOW_SECONDS", 60, 10, 600))
        self._circuit_cooldown_seconds = float(self._resolve_int("MCP_CIRCUIT_COOLDOWN_SECONDS", 30, 5, 300))
        self._retry_backoff_base_ms = self._resolve_int("MCP_TOOL_RETRY_BACKOFF_BASE_MS", 80, 0, 5000)
        self._retry_backoff_max_ms = self._resolve_int("MCP_TOOL_RETRY_BACKOFF_MAX_MS", 800, 0, 15000)
        self._tool_circuit_state: dict[str, dict[str, Any]] = {}
        self._circuit_lock = asyncio.Lock()

    @staticmethod
    def _resolve_bool_env(name: str, default: bool) -> bool:
        """读取布尔环境变量。

        Args:
            name: 环境变量名称。
            default: 默认值。

        Returns:
            bool: 解析后的布尔值。
        """
        raw = str(os.getenv(name, "1" if default else "0")).strip().lower()
        if raw in {"1", "true", "yes", "on"}:
            return True
        if raw in {"0", "false", "no", "off"}:
            return False
        return default

    @staticmethod
    def _resolve_int(env_name: str, default: int, min_value: int, max_value: int) -> int:
        """读取整型环境变量并限制取值。

        Args:
            env_name: 环境变量名。
            default: 默认值。
            min_value: 允许的最小值。
            max_value: 允许的最大值。

        Returns:
            int: 限幅后的整数值。
        """
        raw = str(os.getenv(env_name, str(default))).strip()
        try:
            value = int(raw)
        except ValueError:
            value = default
        if value < min_value:
            return min_value
        if value > max_value:
            return max_value
        return value

    @staticmethod
    def _resolve_timeout() -> float:
        """解析工具调用超时配置。

        Returns:
            float: 工具调用超时秒数。
        """
        raw = str(os.getenv("MCP_TOOL_TIMEOUT_SECONDS", "60")).strip()
        try:
            value = float(raw)
        except ValueError:
            value = 60.0
        return max(1.0, min(value, 300.0))

    @staticmethod
    def _resolve_max_retries() -> int:
        """解析最大重试次数配置。

        Returns:
            int: 最大重试次数。
        """
        raw = str(os.getenv("MCP_TOOL_MAX_RETRIES", "1")).strip()
        try:
            value = int(raw)
        except ValueError:
            value = 1
        return max(0, min(value, 3))

    @staticmethod
    def _normalize_retry_policy(value: str, default: str = "safe") -> str:
        """规范化重试策略名称。

        Args:
            value: 待规范化值。
            default: 默认值。

        Returns:
            str: 规范化后的策略名。
        """
        text = str(value or "").strip().lower()
        if text in {"none", "safe", "idempotent"}:
            return text
        return default

    def _resolve_retry_policy_overrides(self) -> dict[str, str]:
        """解析工具级重试策略覆盖配置。

        Returns:
            dict[str, str]: 工具重试策略覆盖映射。
        """
        raw = str(os.getenv("MCP_TOOL_RETRY_POLICY_MAP", "")).strip()
        if not raw:
            return {}
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        if not isinstance(payload, dict):
            return {}
        mapping: dict[str, str] = {}
        for key, value in payload.items():
            tool_name = str(key or "").strip()
            if not tool_name:
                continue
            mapping[tool_name] = self._normalize_retry_policy(str(value or ""), default=self._default_retry_policy)
        return mapping

    def _resolve_retry_policy(self, tool: MCPTool) -> str:
        """为工具解析最终重试策略。

        Args:
            tool: MCP 工具定义对象。

        Returns:
            str: 最终重试策略。
        """
        direct = self._retry_policy_overrides.get(tool.name)
        if direct:
            return direct
        canonical = f"{tool.server_name}::{tool.tool_name or tool.name}"
        override = self._retry_policy_overrides.get(canonical)
        if override:
            return override
        return self._default_retry_policy

    def register(self, tool: MCPTool) -> None:
        """注册 MCP 工具。

        Args:
            tool: MCP 工具定义对象。

        Returns:
            None: 无返回值。
        """
        name = str(tool.name or "").strip()
        if not name:
            raise ValueError("mcp_tool_name_required")
        self._tools[name] = tool
        canonical = f"{str(tool.server_name or 'local').strip()}::{str(tool.tool_name or name).strip()}"
        self._aliases[canonical] = name

    def list_tools(self) -> list[dict[str, Any]]:
        """列出已注册工具元数据。

        Returns:
            list[dict[str, Any]]: 工具元数据列表。
        """
        rows: list[dict[str, Any]] = []
        for _, tool in sorted(self._tools.items(), key=lambda item: item[0]):
            rows.append(
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.input_schema,
                    "server_name": tool.server_name,
                    "tool_name": tool.tool_name or tool.name,
                }
            )
        return rows

    def _resolve_tool(self, requested_name: str) -> MCPTool:
        """按名称或别名解析工具对象。

        Args:
            requested_name: 调用方请求的工具名或别名。

        Returns:
            MCPTool: 匹配到的工具定义。
        """
        normalized = str(requested_name or "").strip()
        if not normalized:
            raise ValueError("tool_name_required")
        direct = self._tools.get(normalized)
        if direct is not None:
            return direct
        alias = self._aliases.get(normalized)
        if alias and alias in self._tools:
            return self._tools[alias]
        if "::" in normalized:
            fallback = normalized.split("::", 1)[1]
            direct2 = self._tools.get(fallback)
            if direct2 is not None:
                return direct2
        raise KeyError(f"tool_not_found:{normalized}")

    async def _circuit_is_open(self, tool_key: str) -> bool:
        """判断工具熔断器是否开启。

        Args:
            tool_key: 工具唯一键。

        Returns:
            bool: 熔断器是否打开。
        """
        if not self._enable_circuit_breaker:
            return False
        now = time.time()
        async with self._circuit_lock:
            state = self._tool_circuit_state.get(tool_key)
            if not state:
                return False
            opened_at = float(state.get("opened_at") or 0.0)
            if opened_at <= 0:
                return False
            if now - opened_at >= self._circuit_cooldown_seconds:
                state["opened_at"] = 0.0
                state["timeouts"] = []
                return False
            return True

    async def _circuit_record_timeout(self, tool_key: str) -> None:
        """记录超时并更新熔断状态。

        Args:
            tool_key: 工具唯一键。

        Returns:
            None: 无返回值。
        """
        if not self._enable_circuit_breaker:
            return
        now = time.time()
        cutoff = now - self._circuit_window_seconds
        async with self._circuit_lock:
            state = self._tool_circuit_state.setdefault(tool_key, {"timeouts": [], "opened_at": 0.0})
            timeout_rows = [float(ts) for ts in state.get("timeouts", []) if float(ts) >= cutoff]
            timeout_rows.append(now)
            state["timeouts"] = timeout_rows
            if len(timeout_rows) >= int(self._circuit_timeout_threshold):
                state["opened_at"] = now

    async def _circuit_reset(self, tool_key: str) -> None:
        """重置工具熔断状态。

        Args:
            tool_key: 工具唯一键。

        Returns:
            None: 无返回值。
        """
        if not self._enable_circuit_breaker:
            return
        async with self._circuit_lock:
            state = self._tool_circuit_state.setdefault(tool_key, {"timeouts": [], "opened_at": 0.0})
            state["timeouts"] = []
            state["opened_at"] = 0.0

    async def _invoke_once(self, tool: MCPTool, payload: dict[str, Any]) -> dict[str, Any]:
        """执行一次工具调用。

        Args:
            tool: MCP 工具定义对象。
            payload: 请求负载字典。

        Returns:
            dict[str, Any]: 单次调用结果。
        """
        started = time.perf_counter()
        try:
            result_obj = tool.handler(payload)
            if inspect.isawaitable(result_obj):
                result = await asyncio.wait_for(result_obj, timeout=self._tool_timeout_seconds)
            else:
                # 只保留异步调用链；如果 handler 非 async，直接执行（应逐步清理）。
                result = result_obj
            duration_ms = round((time.perf_counter() - started) * 1000.0, 2)
            result_dict = result if isinstance(result, dict) else {"value": result}
            return {
                "ok": True,
                "result": result_dict,
                "meta": {"duration_ms": duration_ms, "tool_name": tool.name},
            }
        except asyncio.TimeoutError as exc:
            duration_ms = round((time.perf_counter() - started) * 1000.0, 2)
            raise TimeoutError(f"tool_timeout:{tool.name}:{duration_ms}ms") from exc

    async def ainvoke(self, tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        """异步调用单个工具并处理重试。

        Args:
            tool_name: 工具名称。
            payload: 请求负载字典。

        Returns:
            dict[str, Any]: 单工具调用结果。
        """
        tool = self._resolve_tool(tool_name)
        canonical_name = f"{tool.server_name}::{tool.tool_name or tool.name}"

        if await self._circuit_is_open(canonical_name):
            return {
                "ok": False,
                "error": "tool_circuit_open",
                "error_type": "circuit_open",
                "meta": {"tool_name": tool.name, "canonical_name": canonical_name},
            }

        retries = int(self._tool_max_retries)
        retry_policy = self._resolve_retry_policy(tool)
        attempts = retries + 1

        for attempt in range(1, attempts + 1):
            try:
                row = await self._invoke_once(tool, payload if isinstance(payload, dict) else {})
                await self._circuit_reset(canonical_name)
                row_meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
                row_meta.update(
                    {
                        "attempt": attempt,
                        "max_attempts": attempts,
                        "canonical_name": canonical_name,
                        "retry_policy": retry_policy,
                    }
                )
                row["meta"] = row_meta
                return row
            except TimeoutError as exc:
                await self._circuit_record_timeout(canonical_name)
                if attempt >= attempts or retry_policy == "none":
                    return {
                        "ok": False,
                        "error": str(exc),
                        "error_type": "timeout",
                        "meta": {
                            "attempt": attempt,
                            "max_attempts": attempts,
                            "canonical_name": canonical_name,
                            "retry_policy": retry_policy,
                        },
                    }
            except Exception as exc:
                if attempt >= attempts or retry_policy == "none":
                    return {
                        "ok": False,
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                        "meta": {
                            "attempt": attempt,
                            "max_attempts": attempts,
                            "canonical_name": canonical_name,
                            "retry_policy": retry_policy,
                        },
                    }

            backoff_ms = min(
                int(self._retry_backoff_base_ms * (2 ** (attempt - 1))),
                int(self._retry_backoff_max_ms),
            )
            if backoff_ms > 0:
                await asyncio.sleep(backoff_ms / 1000.0)

        return {
            "ok": False,
            "error": "tool_failed_after_retries",
            "error_type": "retry_exhausted",
            "meta": {"canonical_name": canonical_name},
        }

    async def ainvoke_batch(self, calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """并发调用多个工具。

        Args:
            calls: 批量调用请求列表。

        Returns:
            list[dict[str, Any]]: 批量调用结果。
        """
        safe_calls = calls if isinstance(calls, list) else []
        if not safe_calls:
            return []

        async def _run_one(row: dict[str, Any]) -> dict[str, Any]:
            """执行批量中的单条调用。

            Args:
                row: 单条调用请求。

            Returns:
                dict[str, Any]: 单条批量调用结果。
            """
            payload_obj = row.get("payload")
            payload = payload_obj if isinstance(payload_obj, dict) else {}
            tool_name = str(row.get("tool_name") or "").strip()
            if not tool_name:
                return {
                    "ok": False,
                    "error": "tool_name_required",
                    "error_type": "validation_error",
                    "meta": {},
                }
            return await self.ainvoke(tool_name, payload)

        return list(await asyncio.gather(*[_run_one(row if isinstance(row, dict) else {}) for row in safe_calls]))
