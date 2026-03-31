"""
MCP 工具调度服务模块。

本模块提供统一的 MCP 工具注册与调用能力，支持：
- 工具注册与别名映射
- 同步/异步调用
- 批量并发调用
- 超时与重试策略
- 统一返回结构与 Token 用量归一化

主要结构：
- MCPTool: 工具描述数据结构
- MCPServer: MCP 调度服务核心实现

"""

import asyncio
import contextvars
import inspect
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import os
import threading
import time
from typing import Any, Callable

try:
    from typing_extensions import deprecated as _deprecated
except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError):
    try:
        from typing import deprecated as _deprecated  # type: ignore[attr-defined]
    except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError):
        def _deprecated(reason: str):
            def _decorator(obj):
                setattr(obj, "__deprecated__", reason)
                return obj
            return _decorator


class _HandlerExecutionError(Exception):
    """包装 handler 执行异常并附带运行指标。"""

    def __init__(self, exc: Exception, metrics: dict[str, Any]):
        super().__init__(str(exc))
        self.original = exc
        self.metrics = metrics


@dataclass
class MCPTool:
    """MCP 工具注册信息。

    Attributes:
        name: 工具显示名称
        description: 工具描述
        input_schema: 输入参数结构定义
        handler: 工具处理函数
        server_name: 工具所在服务名
        tool_name: 服务内部工具名
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], dict[str, Any]]
    server_name: str = "local"
    tool_name: str = ""


class MCPServer:
    """MCP 工具调度服务。

    负责管理工具注册、别名解析、调用执行策略与统一返回结构。
    """

    def __init__(self):
        """初始化 MCP 服务实例。"""
        self._tools: dict[str, MCPTool] = {}
        self._aliases: dict[str, str] = {}
        self._tool_timeout_seconds = self._resolve_tool_timeout_seconds()
        self._tool_max_retries = self._resolve_tool_max_retries()
        self._default_retry_policy = self._resolve_default_retry_policy()
        self._retry_policy_overrides = self._resolve_retry_policy_overrides()
        self._executor_kind_overrides = self._resolve_executor_kind_overrides()
        self._enable_circuit_breaker = self._resolve_bool_env("ENABLE_MCP_TOOL_CIRCUIT_BREAKER", True)
        self._circuit_timeout_threshold = self._resolve_executor_workers(
            "MCP_CIRCUIT_TIMEOUT_THRESHOLD",
            3,
            1,
            20,
        )
        self._circuit_window_seconds = float(
            self._resolve_executor_workers("MCP_CIRCUIT_WINDOW_SECONDS", 60, 10, 600)
        )
        self._circuit_cooldown_seconds = float(
            self._resolve_executor_workers("MCP_CIRCUIT_COOLDOWN_SECONDS", 30, 5, 300)
        )
        self._retry_backoff_base_ms = self._resolve_executor_workers(
            "MCP_TOOL_RETRY_BACKOFF_BASE_MS",
            80,
            0,
            5000,
        )
        self._retry_backoff_max_ms = self._resolve_executor_workers(
            "MCP_TOOL_RETRY_BACKOFF_MAX_MS",
            800,
            0,
            15000,
        )
        self._circuit_lock = threading.Lock()
        self._tool_circuit_state: dict[str, dict[str, Any]] = {}
        self._io_executor = ThreadPoolExecutor(
            max_workers=self._resolve_executor_workers("MCP_IO_MAX_WORKERS", 8, 1, 64),
            thread_name_prefix="mcp-io",
        )
        self._cpu_executor = ThreadPoolExecutor(
            max_workers=self._resolve_executor_workers("MCP_CPU_MAX_WORKERS", 2, 1, 32),
            thread_name_prefix="mcp-cpu",
        )

    @staticmethod
    def _resolve_bool_env(name: str, default: bool) -> bool:
        """从环境变量解析布尔值。

        支持的格式：1/true/yes/on 表示 True；0/false/no/off 表示 False。
        解析失败时返回默认值。

        Args:
            name: 环境变量名。
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
    def _resolve_trace_id() -> str:
        """获取当前请求的追踪 ID。

        从会话日志模块获取请求 ID，用于链路追踪。
        如果获取失败则返回空字符串。

        Returns:
            str: 追踪 ID 或空字符串。
        """
        try:
            from app.common.session_logger import get_request_id

            return str(get_request_id() or "")
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError):
            return ""

    @staticmethod
    def _resolve_tool_timeout_seconds() -> float:
        """解析工具超时配置。

        从环境变量 MCP_TOOL_TIMEOUT_SECONDS 读取，
        并将结果限制在 [1, 300] 秒。

        Returns:
            float: 生效的超时秒数
        """
        raw = str(os.getenv("MCP_TOOL_TIMEOUT_SECONDS", "60")).strip()
        try:
            value = float(raw)
        except ValueError:
            value = 60.0
        if value < 1:
            return 1.0
        if value > 300:
            return 300.0
        return value

    @staticmethod
    def _resolve_tool_max_retries() -> int:
        """解析工具重试次数配置。

        从环境变量 MCP_TOOL_MAX_RETRIES 读取，
        并将结果限制在 [0, 3]。

        Returns:
            int: 生效的最大重试次数
        """
        raw = str(os.getenv("MCP_TOOL_MAX_RETRIES", "1")).strip()
        try:
            value = int(raw)
        except ValueError:
            value = 1
        if value < 0:
            return 0
        if value > 3:
            return 3
        return value

    @staticmethod
    def _resolve_executor_workers(
        env_name: str, default: int, min_value: int, max_value: int
    ) -> int:
        """从环境变量解析线程池工作线程数。

        将值限制在 [min_value, max_value] 范围内。

        Args:
            env_name: 环境变量名。
            default: 默认值。
            min_value: 最小值限制。
            max_value: 最大值限制。

        Returns:
            int: 解析后的工作线程数。
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
    def _normalize_retry_policy(value: str, default: str = "safe") -> str:
        """规范化重试策略值。

        Args:
            value: 输入值。
            default: 默认值。

        Returns:
            str: 规范化后的策略：none/safe/idempotent。
        """
        text = str(value or "").strip().lower()
        if text in {"none", "safe", "idempotent"}:
            return text
        return default

    def _resolve_default_retry_policy(self) -> str:
        """解析默认重试策略配置。"""
        return self._normalize_retry_policy(
            str(os.getenv("MCP_TOOL_RETRY_POLICY", "safe")),
            default="safe",
        )

    def _resolve_retry_policy_overrides(self) -> dict[str, str]:
        """解析按工具覆盖的重试策略配置。

        从环境变量 MCP_TOOL_RETRY_POLICY_MAP 读取 JSON 配置，
        格式如 {"tool_name": "safe", "other_tool": "none"}。

        Returns:
            dict[str, str]: 工具名到重试策略的映射。
        """
        raw = str(os.getenv("MCP_TOOL_RETRY_POLICY_MAP", "")).strip()
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            return {}
        if not isinstance(parsed, dict):
            return {}
        overrides: dict[str, str] = {}
        for key, value in parsed.items():
            tool_key = str(key or "").strip()
            if not tool_key:
                continue
            overrides[tool_key] = self._normalize_retry_policy(
                str(value or ""),
                default=self._default_retry_policy,
            )
        return overrides

    @staticmethod
    def _normalize_executor_kind(value: str, default: str = "io") -> str:
        """规范化执行器类型值。

        Args:
            value: 输入值。
            default: 默认值。

        Returns:
            str: 规范化后的类型：io/cpu。
        """
        text = str(value or "").strip().lower()
        if text in {"io", "cpu"}:
            return text
        return default

    def _resolve_executor_kind_overrides(self) -> dict[str, str]:
        """解析按工具指定的执行器类型配置。

        从环境变量 MCP_TOOL_EXECUTOR_MAP 读取 JSON 配置，
        格式如 {"heavy_tool": "cpu", "io_tool": "io"}。

        Returns:
            dict[str, str]: 工具名到执行器类型的映射。
        """
        raw = str(os.getenv("MCP_TOOL_EXECUTOR_MAP", "")).strip()
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            return {}
        if not isinstance(parsed, dict):
            return {}
        overrides: dict[str, str] = {}
        for key, value in parsed.items():
            tool_key = str(key or "").strip()
            if not tool_key:
                continue
            overrides[tool_key] = self._normalize_executor_kind(str(value or ""), default="io")
        return overrides

    @staticmethod
    def _classify_error(exc: Exception) -> str:
        """将异常映射为统一错误类型。

        Args:
            exc: 捕获到的异常对象

        Returns:
            str: 统一错误类型标识
        """
        if isinstance(exc, TimeoutError):
            return "timeout"
        if isinstance(exc, KeyError):
            return "invalid_payload"
        if isinstance(exc, ValueError):
            return "invalid_arguments"
        if isinstance(exc, OSError):
            return "system_io"
        return "runtime_error"

    @staticmethod
    def _normalize_token_usage(metric_obj: Any) -> dict[str, int]:
        """归一化 Token 用量字段。

        Args:
            metric_obj: 原始 token_usage 对象

        Returns:
            dict[str, int]: 统一 token_usage 结构
        """
        metric = metric_obj if isinstance(metric_obj, dict) else {}
        prompt_tokens = int(
            metric.get("prompt_tokens")
            or metric.get("input_tokens")
            or metric.get("promptTokens")
            or 0
        )
        completion_tokens = int(
            metric.get("completion_tokens")
            or metric.get("output_tokens")
            or metric.get("completionTokens")
            or 0
        )
        total_tokens = int(
            metric.get("total_tokens")
            or metric.get("totalTokens")
            or 0
        )
        if total_tokens <= 0:
            total_tokens = prompt_tokens + completion_tokens
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }

    @staticmethod
    def _parse_json_object(value: str) -> dict[str, Any]:
        """将 JSON 字符串解析为对象字典。

        Args:
            value: JSON 字符串

        Returns:
            dict[str, Any]: 解析后的对象字典，解析失败返回空字典
        """
        text = str(value or "").strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except (ValueError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _parse_json_array(value: str) -> list[dict[str, Any]]:
        """将 JSON 字符串解析为对象数组。

        Args:
            value: JSON 字符串

        Returns:
            list[dict[str, Any]]: 解析后的对象数组，解析失败返回空数组
        """
        text = str(value or "").strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except (ValueError, TypeError):
            return []
        if not isinstance(parsed, list):
            return []
        rows: list[dict[str, Any]] = []
        for item in parsed:
            if isinstance(item, dict):
                rows.append(item)
        return rows

    def _run_handler_with_metrics(
        self,
        handler: Callable[[dict[str, Any]], Any],
        payload: dict[str, Any],
        submitted_perf: float,
        exec_context: contextvars.Context | None = None,
    ) -> dict[str, Any]:
        """执行工具处理器并收集性能指标。

        计算队列等待时间和实际执行时间，用于性能监控和重试决策。

        Args:
            handler: 工具处理器函数。
            payload: 工具调用参数。
            submitted_perf: 提交到线程池的时间戳。

        Returns:
            dict[str, Any]: 包含返回值和性能指标的字典。

        Raises:
            _HandlerExecutionError: 执行失败时抛出，附带性能指标。
        """
        started_perf = time.perf_counter()
        queue_wait_ms = max(0.0, (started_perf - float(submitted_perf)) * 1000.0)
        worker_id = f"{threading.current_thread().name}:{threading.get_ident()}"
        try:
            if exec_context is None:
                value = self._run_handler(handler, payload)
            else:
                value = exec_context.run(self._run_handler, handler, payload)
            finished_perf = time.perf_counter()
            return {
                "value": value,
                "metrics": {
                    "queue_wait_ms": round(queue_wait_ms, 2),
                    "run_ms": round(max(0.0, (finished_perf - started_perf) * 1000.0), 2),
                    "worker_id": worker_id,
                },
            }
        except Exception as exc:
            finished_perf = time.perf_counter()
            raise _HandlerExecutionError(
                exc,
                {
                    "queue_wait_ms": round(queue_wait_ms, 2),
                    "run_ms": round(max(0.0, (finished_perf - started_perf) * 1000.0), 2),
                    "worker_id": worker_id,
                },
            ) from exc

    @staticmethod
    def _extract_last_attempt_metrics(attempt_metrics: list[dict[str, Any]] | None) -> dict[str, Any]:
        """提取最后一次尝试的性能指标。

        Args:
            attempt_metrics: 多次尝试的指标列表。

        Returns:
            dict[str, Any]: 最后一次尝试的指标，空列表返回零值。
        """
        metrics = attempt_metrics if isinstance(attempt_metrics, list) else []
        if not metrics:
            return {"queue_wait_ms": 0.0, "run_ms": 0.0, "worker_id": ""}
        last = metrics[-1] if isinstance(metrics[-1], dict) else {}
        return {
            "queue_wait_ms": float(last.get("queue_wait_ms") or 0.0),
            "run_ms": float(last.get("run_ms") or 0.0),
            "worker_id": str(last.get("worker_id") or ""),
        }

    @staticmethod
    def _build_attempt_metric(
        attempt: int,
        queue_wait_ms: float = 0.0,
        run_ms: float = 0.0,
        worker_id: str = "",
        timed_out: bool = False,
    ) -> dict[str, Any]:
        """构建单次尝试的指标记录。

        Args:
            attempt: 尝试次数（从1开始）。
            queue_wait_ms: 队列等待时间（毫秒）。
            run_ms: 实际执行时间（毫秒）。
            worker_id: 执行工作线程标识。
            timed_out: 是否超时。

        Returns:
            dict[str, Any]: 尝试指标字典。
        """
        return {
            "attempt": int(attempt),
            "queue_wait_ms": round(max(0.0, float(queue_wait_ms)), 2),
            "run_ms": round(max(0.0, float(run_ms)), 2),
            "worker_id": str(worker_id or ""),
            "timeout": bool(timed_out),
        }

    def _compute_retry_backoff_seconds(self, next_attempt: int) -> float:
        """计算重试退避时间（指数退避）。

        公式：base_ms * 2^(next_attempt-2)，不超过 max_ms。

        Args:
            next_attempt: 下一次尝试的次数。

        Returns:
            float: 退避时间（秒）。
        """
        if self._retry_backoff_base_ms <= 0:
            return 0.0
        pow_index = max(0, int(next_attempt) - 2)
        raw_ms = float(self._retry_backoff_base_ms) * float(2 ** pow_index)
        capped_ms = min(raw_ms, float(self._retry_backoff_max_ms))
        return max(0.0, capped_ms / 1000.0)

    def _schedule_retry(self, state: dict[str, Any], next_attempt: int) -> dict[str, Any]:
        """安排一次重试任务。

        计算退避时间并更新重试状态。

        Args:
            state: 当前任务状态。
            next_attempt: 下一次尝试次数。

        Returns:
            dict[str, Any]: 更新后的重试状态。
        """
        backoff_seconds = self._compute_retry_backoff_seconds(next_attempt)
        retry_state = dict(state)
        retry_state["attempt"] = int(next_attempt)
        retry_state["future"] = None
        retry_state["ready_at_perf"] = time.perf_counter() + backoff_seconds
        retry_state["started_perf"] = 0.0
        retry_state["started_at_ms"] = 0
        retry_state["retry_backoff_ms"] = round(backoff_seconds * 1000.0, 2)
        return retry_state

    def _prune_timeout_points(self, points: list[float], now_ts: float) -> list[float]:
        """清理超出熔断窗口的超时记录。

        Args:
            points: 超时时间点列表。
            now_ts: 当前时间戳。

        Returns:
            list[float]: 窗口内的超时记录。
        """
        floor_ts = now_ts - float(self._circuit_window_seconds)
        return [item for item in points if item >= floor_ts]

    def _is_circuit_open(self, tool_key: str) -> tuple[bool, float]:
        """检查工具是否处于熔断状态。

        Args:
            tool_key: 工具标识。

        Returns:
            tuple[bool, float]: (是否熔断, 剩余冷却时间秒数)。
        """
        if not self._enable_circuit_breaker:
            return False, 0.0
        now_ts = time.time()
        with self._circuit_lock:
            state = self._tool_circuit_state.get(tool_key)
            if not state:
                return False, 0.0
            open_until = float(state.get("open_until") or 0.0)
            if open_until <= now_ts:
                state["open_until"] = 0.0
                state["timeouts"] = self._prune_timeout_points(
                    state.get("timeouts") if isinstance(state.get("timeouts"), list) else [],
                    now_ts,
                )
                return False, 0.0
            return True, max(0.0, open_until - now_ts)

    def _record_timeout_failure(self, tool_key: str) -> None:
        """记录工具超时失败，可能触发熔断。

        当窗口内超时次数达到阈值时，打开熔断器。

        Args:
            tool_key: 工具标识。
        """
        if not self._enable_circuit_breaker:
            return
        now_ts = time.time()
        with self._circuit_lock:
            state = self._tool_circuit_state.setdefault(tool_key, {"timeouts": [], "open_until": 0.0})
            points = state.get("timeouts") if isinstance(state.get("timeouts"), list) else []
            points = self._prune_timeout_points([float(item) for item in points], now_ts)
            points.append(now_ts)
            state["timeouts"] = points
            if len(points) >= int(self._circuit_timeout_threshold):
                state["open_until"] = now_ts + float(self._circuit_cooldown_seconds)

    def _record_call_success(self, tool_key: str) -> None:
        """记录工具调用成功，重置熔断状态。

        成功调用后清空超时记录，恢复工具可用状态。

        Args:
            tool_key: 工具标识。
        """
        if not self._enable_circuit_breaker:
            return
        now_ts = time.time()
        with self._circuit_lock:
            state = self._tool_circuit_state.get(tool_key)
            if not state:
                return
            state["timeouts"] = []
            if float(state.get("open_until") or 0.0) <= now_ts:
                state["open_until"] = 0.0


    def _extract_token_usage(self, result_obj: Any) -> dict[str, int]:
        """从工具返回内容中提取并归一化 Token 用量。

        优先读取标准路径 `meta.token_usage`，
        同时兼容迁移期旧字段结构。

        Args:
            result_obj: 工具原始返回对象

        Returns:
            dict[str, int]: 统一 token_usage 结构
        """
        result = result_obj if isinstance(result_obj, dict) else {}

        # 优先读取标准位置，兼容旧工具字段以完成统一迁移。
        meta_obj = result.get("meta")
        meta = meta_obj if isinstance(meta_obj, dict) else {}
        meta_token_usage = meta.get("token_usage")
        if isinstance(meta_token_usage, dict):
            normalized = self._normalize_token_usage(meta_token_usage)
            if normalized["total_tokens"] > 0:
                return normalized

        for key in ["token_usage", "usage"]:
            metric = result.get(key)
            if isinstance(metric, dict):
                normalized = self._normalize_token_usage(metric)
                if normalized["total_tokens"] > 0:
                    return normalized

        payload_obj = result.get("result")
        payload = payload_obj if isinstance(payload_obj, dict) else {}
        for key in ["token_usage", "usage"]:
            metric = payload.get(key)
            if isinstance(metric, dict):
                normalized = self._normalize_token_usage(metric)
                if normalized["total_tokens"] > 0:
                    return normalized

        return self._normalize_token_usage(result)

    def _build_meta(
        self,
        result_obj: Any,
        attempts: int,
        tool: MCPTool,
        retry_policy: str,
        timed_out_attempts: int = 0,
        cancel_requested: bool = False,
        attempt_metrics: list[dict[str, Any]] | None = None,
        trace_id: str = "",
    ) -> dict[str, Any]:
        """构建统一 meta 信息。

        Args:
            result_obj: 工具原始返回对象
            attempts: 当前调用尝试次数
            tool: 工具定义
            retry_policy: 重试策略
            timed_out_attempts: 超时尝试次数
            cancel_requested: 是否发起过 cancel 请求
            attempt_metrics: 各次尝试的运行指标
            trace_id: 链路追踪 ID

        Returns:
            dict[str, Any]: 统一 meta 字段
        """
        result = result_obj if isinstance(result_obj, dict) else {}
        existing_meta_obj = result.get("meta")
        existing_meta = existing_meta_obj if isinstance(existing_meta_obj, dict) else {}
        token_usage = self._extract_token_usage(result)
        normalized_metrics = attempt_metrics if isinstance(attempt_metrics, list) else []
        last_metrics = self._extract_last_attempt_metrics(normalized_metrics)

        return {
            "attempts": attempts,
            "retries": max(0, int(attempts) - 1),
            "retry_policy": retry_policy,
            "timeout_seconds": self._tool_timeout_seconds,
            "timeout": int(timed_out_attempts) > 0,
            "token_usage": token_usage,
            "server_name": str(existing_meta.get("server_name") or tool.server_name or "local"),
            "tool_name": str(existing_meta.get("tool_name") or tool.tool_name or tool.name or ""),
            "timed_out_attempts": int(timed_out_attempts),
            "cancel_requested": bool(cancel_requested),
            "queue_wait_ms": round(float(last_metrics.get("queue_wait_ms") or 0.0), 2),
            "run_ms": round(float(last_metrics.get("run_ms") or 0.0), 2),
            "worker_id": str(last_metrics.get("worker_id") or ""),
            "attempt_metrics": normalized_metrics,
            "trace_id": str(trace_id or self._resolve_trace_id()),
            # 运行中的线程无法被强制终止，cancel 仅代表取消请求已发出。
            "cancel_effect_guaranteed": False,
        }

    @staticmethod
    def _run_handler(handler: Callable[[dict[str, Any]], Any], payload: dict[str, Any]) -> Any:
        """执行工具 handler，兼容同步与异步实现。

        Args:
            handler: 工具处理函数
            payload: 工具调用参数

        Returns:
            Any: handler 返回结果
        """
        value = handler(payload or {})
        if inspect.isawaitable(value):
            return asyncio.run(value)
        return value

    def _resolve_retry_policy_for_tool(self, tool: MCPTool, requested_tool_name: str) -> str:
        """确定工具的重试策略。

        优先级：工具覆盖配置 > 副作用检测 > 默认策略。
        检测到写入类操作（create/update/delete等）时强制不重试。

        Args:
            tool: MCP工具对象。
            requested_tool_name: 请求的工具名。

        Returns:
            str: 重试策略：none/safe/idempotent。
        """
        def _is_potential_side_effect_tool(tool_obj: MCPTool) -> bool:
            text = " ".join(
                [
                    str(tool_obj.name or ""),
                    str(tool_obj.tool_name or ""),
                    str(requested_tool_name or ""),
                    str(tool_obj.description or ""),
                ]
            ).lower()
            keywords = {
                "create",
                "update",
                "delete",
                "remove",
                "insert",
                "upsert",
                "import",
                "write",
                "embed",
                "extract",
                "rollback",
                "clear",
                "删除",
                "清空",
                "写入",
                "导入",
                "创建",
                "更新",
            }
            return any(keyword in text for keyword in keywords)

        canonical = self._canonical_name(tool.server_name, tool.tool_name)
        for key in [canonical, str(tool.tool_name or ""), str(tool.name or ""), str(requested_tool_name or "")]:
            if key and key in self._retry_policy_overrides:
                return self._retry_policy_overrides[key]
        if _is_potential_side_effect_tool(tool):
            return "none"
        return self._default_retry_policy

    def _resolve_max_attempts(self, retry_policy: str) -> int:
        """根据重试策略确定最大尝试次数。

        Args:
            retry_policy: 重试策略。

        Returns:
            int: 最大尝试次数。
        """
        policy = self._normalize_retry_policy(retry_policy, default=self._default_retry_policy)
        if policy == "none":
            return 1
        if policy == "idempotent":
            return min(self._tool_max_retries + 2, 5)
        return self._tool_max_retries + 1

    def _resolve_executor_for_tool(self, tool: MCPTool, requested_tool_name: str) -> ThreadPoolExecutor:
        """确定工具使用的线程池执行器。

        Args:
            tool: MCP工具对象。
            requested_tool_name: 请求的工具名。

        Returns:
            ThreadPoolExecutor: IO或CPU线程池。
        """
        canonical = self._canonical_name(tool.server_name, tool.tool_name)
        kind = "io"
        for key in [canonical, str(tool.tool_name or ""), str(tool.name or ""), str(requested_tool_name or "")]:
            if key and key in self._executor_kind_overrides:
                kind = self._executor_kind_overrides[key]
                break
        if kind == "cpu":
            return self._cpu_executor
        return self._io_executor

    def _build_tool_not_found_response(
        self,
        tool_name: str,
        server_name: str = "",
        trace_id: str = "",
    ) -> dict[str, Any]:
        """构建工具未找到的错误响应。

        Args:
            tool_name: 工具名。
            server_name: 服务器名。
            trace_id: 追踪ID。

        Returns:
            dict[str, Any]: 标准错误响应结构。
        """
        return {
            "ok": False,
            "error": f"tool_not_found:{tool_name}",
            "error_type": "tool_not_found",
            "meta": {
                "attempts": 0,
                "retries": 0,
                "retry_policy": "none",
                "timeout_seconds": self._tool_timeout_seconds,
                "timeout": False,
                "token_usage": {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                },
                "server_name": str(server_name or ""),
                "tool_name": str(tool_name or ""),
                "timed_out_attempts": 0,
                "cancel_requested": False,
                "queue_wait_ms": 0.0,
                "run_ms": 0.0,
                "worker_id": "",
                "attempt_metrics": [],
                "trace_id": str(trace_id or self._resolve_trace_id()),
                "cancel_effect_guaranteed": False,
            },
        }

    def _build_circuit_open_response(
        self,
        tool: MCPTool,
        retry_policy: str,
        cool_down_seconds: float,
        trace_id: str = "",
    ) -> dict[str, Any]:
        """构建熔断打开的错误响应。

        Args:
            tool: MCP工具对象。
            retry_policy: 重试策略。
            cool_down_seconds: 剩余冷却时间。
            trace_id: 追踪ID。

        Returns:
            dict[str, Any]: 标准错误响应结构，包含熔断信息。
        """
        return {
            "ok": False,
            "error": f"tool_circuit_open:{tool.tool_name}",
            "error_type": "circuit_open",
            "meta": {
                "attempts": 0,
                "retries": 0,
                "retry_policy": str(retry_policy or "none"),
                "timeout_seconds": self._tool_timeout_seconds,
                "timeout": True,
                "token_usage": {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                },
                "server_name": str(tool.server_name or ""),
                "tool_name": str(tool.tool_name or tool.name or ""),
                "timed_out_attempts": 0,
                "cancel_requested": False,
                "queue_wait_ms": 0.0,
                "run_ms": 0.0,
                "worker_id": "",
                "attempt_metrics": [],
                "circuit_open": True,
                "circuit_cooldown_seconds": round(max(0.0, float(cool_down_seconds)), 2),
                "trace_id": str(trace_id or self._resolve_trace_id()),
                "cancel_effect_guaranteed": False,
            },
        }

    def _attach_batch_timing(
        self,
        result: dict[str, Any],
        started_perf: float,
        started_at_ms: int,
        finished_perf: float,
        finished_at_ms: int,
    ) -> dict[str, Any]:
        """为批量调用结果附加时间戳信息。

        Args:
            result: 工具调用结果。
            started_perf: 性能计数器开始时间。
            started_at_ms: 毫秒级开始时间戳。
            finished_perf: 性能计数器结束时间。
            finished_at_ms: 毫秒级结束时间戳。

        Returns:
            dict[str, Any]: 附加时间信息后的结果。
        """
        row = result if isinstance(result, dict) else {
            "ok": False,
            "error": "invalid_result",
            "error_type": "runtime_error",
            "meta": {},
        }
        meta_obj = row.get("meta")
        meta = meta_obj if isinstance(meta_obj, dict) else {}
        meta["batch_item_started_at_ms"] = int(started_at_ms)
        meta["batch_item_finished_at_ms"] = int(finished_at_ms)
        meta["batch_item_latency_ms"] = round(max(0.0, (finished_perf - started_perf) * 1000.0), 2)
        row["meta"] = meta
        return row

    @staticmethod
    def _canonical_name(server_name: str, tool_name: str) -> str:
        """生成工具规范名称。

        Args:
            server_name: 服务名
            tool_name: 工具名

        Returns:
            str: 规范名称，格式为 server::tool
        """
        return f"{server_name}::{tool_name}"

    def register(self, tool: MCPTool) -> None:
        """注册工具并建立别名映射。

        Args:
            tool: 待注册工具

        Returns:
            None
        """
        real_tool_name = tool.tool_name or tool.name
        canonical_name = self._canonical_name(tool.server_name, real_tool_name)
        self._tools[canonical_name] = MCPTool(
            name=tool.name,
            description=tool.description,
            input_schema=tool.input_schema,
            handler=tool.handler,
            server_name=tool.server_name,
            tool_name=real_tool_name,
        )

        # 兼容旧接口：允许通过别名直接调用
        self._aliases[tool.name] = canonical_name
        self._aliases[real_tool_name] = canonical_name
        self._aliases[canonical_name] = canonical_name

    def get_tool(self, tool_name: str) -> MCPTool | None:
        """根据名称或别名获取工具定义。

        Args:
            tool_name: 工具名、别名或规范名

        Returns:
            MCPTool | None: 工具定义，不存在则返回 None
        """
        canonical = self._aliases.get(tool_name)
        if not canonical:
            return None
        return self._tools.get(canonical)

    def list_tools(self) -> list[dict[str, Any]]:
        """列出所有已注册工具。

        Returns:
            list[dict[str, Any]]: 工具列表
        """
        return [
            {
                "name": tool.name,
                "canonical_name": self._canonical_name(tool.server_name, tool.tool_name),
                "server_name": tool.server_name,
                "tool_name": tool.tool_name,
                "description": tool.description,
                "input_schema": tool.input_schema,
            }
            for tool in self._tools.values()
        ]

    @_deprecated("Use invoke_batch_json() for all tool calls, including single-item calls.")
    def invoke(self, tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        """同步调用工具。

        Args:
            tool_name: 工具名、别名或规范名
            payload: 工具参数

        Returns:
            dict[str, Any]: 统一封装的工具调用结果
        """
        # 统一走批量入口，单请求同样采用并发调用通道。
        rows = self.invoke_batch([
            {
                "tool_name": tool_name,
                "payload": payload if isinstance(payload, dict) else {},
            }
        ])
        if rows:
            return rows[0]
        trace_id = self._resolve_trace_id()
        return {
            "ok": False,
            "error": "batch_not_executed",
            "error_type": "runtime_error",
            "meta": {
                "attempts": 0,
                "retries": 0,
                "retry_policy": "none",
                "timeout_seconds": self._tool_timeout_seconds,
                "timeout": False,
                "token_usage": {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                },
                "server_name": "",
                "tool_name": str(tool_name or ""),
                "timed_out_attempts": 0,
                "cancel_requested": False,
                "queue_wait_ms": 0.0,
                "run_ms": 0.0,
                "worker_id": "",
                "attempt_metrics": [],
                "trace_id": trace_id,
                "cancel_effect_guaranteed": False,
            },
        }

    @_deprecated("Use invoke_batch_json() for all tool calls, including single-item calls.")
    async def invoke_async(self, tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        """异步调用工具。

        Args:
            tool_name: 工具名、别名或规范名
            payload: 工具参数

        Returns:
            dict[str, Any]: 统一封装的工具调用结果
        """
        return await asyncio.to_thread(self.invoke, tool_name, payload)

    def invoke_batch(self, calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """批量并发调用工具。

        Args:
            calls: 调用请求列表，每项包含 tool_name 与 payload

        Returns:
            list[dict[str, Any]]: 与输入顺序一致的调用结果列表
        """
        ordered_results: list[dict[str, Any]] = [
            {"ok": False, "error": "batch_not_executed"} for _ in calls
        ]
        pending_states: list[dict[str, Any]] = []
        trace_id = self._resolve_trace_id()

        for index, row in enumerate(calls):
            requested_tool_name = str(row.get("tool_name") or "")
            payload_obj = row.get("payload")
            payload = payload_obj if isinstance(payload_obj, dict) else {}

            tool = self.get_tool(requested_tool_name)
            if not tool:
                ordered_results[index] = self._build_tool_not_found_response(
                    requested_tool_name,
                    trace_id=trace_id,
                )
                continue

            retry_policy = self._resolve_retry_policy_for_tool(tool, requested_tool_name)
            max_attempts = self._resolve_max_attempts(retry_policy)
            executor = self._resolve_executor_for_tool(tool, requested_tool_name)
            circuit_key = self._canonical_name(tool.server_name, tool.tool_name)
            circuit_open, cool_down_seconds = self._is_circuit_open(circuit_key)
            if circuit_open:
                started_perf = time.perf_counter()
                started_at_ms = int(time.time() * 1000)
                ordered_results[index] = self._attach_batch_timing(
                    self._build_circuit_open_response(
                        tool=tool,
                        retry_policy=retry_policy,
                        cool_down_seconds=cool_down_seconds,
                        trace_id=trace_id,
                    ),
                    started_perf=started_perf,
                    started_at_ms=started_at_ms,
                    finished_perf=started_perf,
                    finished_at_ms=started_at_ms,
                )
                continue

            started_perf = time.perf_counter()
            started_at_ms = int(time.time() * 1000)
            run_context = contextvars.copy_context()
            future = executor.submit(
                self._run_handler_with_metrics,
                tool.handler,
                payload,
                started_perf,
                run_context,
            )
            pending_states.append(
                {
                    "index": index,
                    "requested_tool_name": requested_tool_name,
                    "tool": tool,
                    "payload": payload,
                    "retry_policy": retry_policy,
                    "max_attempts": max_attempts,
                    "attempt": 1,
                    "timed_out_attempts": 0,
                    "cancel_requested": False,
                    "executor": executor,
                    "future": future,
                    "ready_at_perf": 0.0,
                    "retry_backoff_ms": 0.0,
                    "started_perf": started_perf,
                    "started_at_ms": started_at_ms,
                    "run_context": run_context,
                    "attempt_metrics": [],
                    "circuit_key": circuit_key,
                    "trace_id": trace_id,
                }
            )

        while pending_states:
            now_perf = time.perf_counter()
            progressed = False
            next_pending: list[dict[str, Any]] = []

            for state in pending_states:
                tool = state["tool"]
                future = state.get("future")

                if future is None:
                    ready_at = float(state.get("ready_at_perf") or 0.0)
                    if now_perf < ready_at:
                        next_pending.append(state)
                        continue
                    submit_perf = time.perf_counter()
                    state["started_perf"] = submit_perf
                    state["started_at_ms"] = int(time.time() * 1000)
                    state["future"] = state["executor"].submit(
                        self._run_handler_with_metrics,
                        tool.handler,
                        state["payload"],
                        submit_perf,
                        state.get("run_context"),
                    )
                    progressed = True
                    next_pending.append(state)
                    continue

                if future.done():
                    progressed = True
                    finished_perf = time.perf_counter()
                    finished_at_ms = int(time.time() * 1000)
                    try:
                        wrapped_result = future.result()
                        if isinstance(wrapped_result, dict):
                            raw_result = wrapped_result.get("value")
                            metrics_obj = wrapped_result.get("metrics")
                            metrics = metrics_obj if isinstance(metrics_obj, dict) else {}
                        else:
                            raw_result = wrapped_result
                            metrics = {}
                        attempt_metrics = list(state.get("attempt_metrics") or [])
                        attempt_metrics.append(
                            self._build_attempt_metric(
                                attempt=int(state["attempt"]),
                                queue_wait_ms=float(metrics.get("queue_wait_ms") or 0.0),
                                run_ms=float(metrics.get("run_ms") or 0.0),
                                worker_id=str(metrics.get("worker_id") or ""),
                                timed_out=False,
                            )
                        )
                        state["attempt_metrics"] = attempt_metrics
                        self._record_call_success(str(state.get("circuit_key") or ""))
                        meta = self._build_meta(
                            raw_result,
                            attempts=int(state["attempt"]),
                            tool=tool,
                            retry_policy=str(state["retry_policy"]),
                            timed_out_attempts=int(state["timed_out_attempts"]),
                            cancel_requested=bool(state["cancel_requested"]),
                            attempt_metrics=attempt_metrics,
                            trace_id=str(state.get("trace_id") or trace_id),
                        )
                        if isinstance(raw_result, dict):
                            result = {"ok": True, "result": raw_result, "meta": meta}
                        else:
                            result = {"ok": True, "result": {"value": raw_result}, "meta": meta}
                        ordered_results[int(state["index"])] = self._attach_batch_timing(
                            result,
                            started_perf=float(state["started_perf"]),
                            started_at_ms=int(state["started_at_ms"]),
                            finished_perf=finished_perf,
                            finished_at_ms=finished_at_ms,
                        )
                    except _HandlerExecutionError as wrapped_exc:
                        exc = wrapped_exc.original
                        metrics = wrapped_exc.metrics if isinstance(wrapped_exc.metrics, dict) else {}
                        attempt_metrics = list(state.get("attempt_metrics") or [])
                        attempt_metrics.append(
                            self._build_attempt_metric(
                                attempt=int(state["attempt"]),
                                queue_wait_ms=float(metrics.get("queue_wait_ms") or 0.0),
                                run_ms=float(metrics.get("run_ms") or 0.0),
                                worker_id=str(metrics.get("worker_id") or ""),
                                timed_out=False,
                            )
                        )
                        state["attempt_metrics"] = attempt_metrics
                        if int(state["attempt"]) < int(state["max_attempts"]):
                            next_pending.append(
                                self._schedule_retry(state, next_attempt=int(state["attempt"]) + 1)
                            )
                        else:
                            error_meta = self._build_meta(
                                {},
                                attempts=int(state["attempt"]),
                                tool=tool,
                                retry_policy=str(state["retry_policy"]),
                                timed_out_attempts=int(state["timed_out_attempts"]),
                                cancel_requested=bool(state["cancel_requested"]),
                                attempt_metrics=attempt_metrics,
                                trace_id=str(state.get("trace_id") or trace_id),
                            )
                            error_result = {
                                "ok": False,
                                "error": str(exc),
                                "error_type": self._classify_error(exc),
                                "meta": error_meta,
                            }
                            ordered_results[int(state["index"])] = self._attach_batch_timing(
                                error_result,
                                started_perf=float(state["started_perf"]),
                                started_at_ms=int(state["started_at_ms"]),
                                finished_perf=finished_perf,
                                finished_at_ms=finished_at_ms,
                            )
                    except (
                        RuntimeError,
                        ValueError,
                        TypeError,
                        KeyError,
                        AttributeError,
                        OSError,
                        ImportError,
                    ) as exc:
                        if int(state["attempt"]) < int(state["max_attempts"]):
                            next_pending.append(
                                self._schedule_retry(state, next_attempt=int(state["attempt"]) + 1)
                            )
                        else:
                            error_meta = self._build_meta(
                                {},
                                attempts=int(state["attempt"]),
                                tool=tool,
                                retry_policy=str(state["retry_policy"]),
                                timed_out_attempts=int(state["timed_out_attempts"]),
                                cancel_requested=bool(state["cancel_requested"]),
                                attempt_metrics=list(state.get("attempt_metrics") or []),
                                trace_id=str(state.get("trace_id") or trace_id),
                            )
                            error_result = {
                                "ok": False,
                                "error": str(exc),
                                "error_type": self._classify_error(exc),
                                "meta": error_meta,
                            }
                            ordered_results[int(state["index"])] = self._attach_batch_timing(
                                error_result,
                                started_perf=float(state["started_perf"]),
                                started_at_ms=int(state["started_at_ms"]),
                                finished_perf=finished_perf,
                                finished_at_ms=finished_at_ms,
                            )
                    continue

                elapsed = now_perf - float(state["started_perf"])
                if elapsed >= self._tool_timeout_seconds:
                    progressed = True
                    state["cancel_requested"] = True
                    state["timed_out_attempts"] = int(state["timed_out_attempts"]) + 1
                    attempt_metrics = list(state.get("attempt_metrics") or [])
                    attempt_metrics.append(
                        self._build_attempt_metric(
                            attempt=int(state["attempt"]),
                            run_ms=elapsed * 1000.0,
                            timed_out=True,
                        )
                    )
                    state["attempt_metrics"] = attempt_metrics
                    future.cancel()
                    if int(state["attempt"]) < int(state["max_attempts"]):
                        next_pending.append(
                            self._schedule_retry(state, next_attempt=int(state["attempt"]) + 1)
                        )
                    else:
                        finished_perf = time.perf_counter()
                        finished_at_ms = int(time.time() * 1000)
                        timeout_exc = TimeoutError(
                            f"tool_timeout:{tool.tool_name}:{self._tool_timeout_seconds}s"
                        )
                        timeout_meta = self._build_meta(
                            {},
                            attempts=int(state["attempt"]),
                            tool=tool,
                            retry_policy=str(state["retry_policy"]),
                            timed_out_attempts=int(state["timed_out_attempts"]),
                            cancel_requested=True,
                            attempt_metrics=attempt_metrics,
                            trace_id=str(state.get("trace_id") or trace_id),
                        )
                        timeout_result = {
                            "ok": False,
                            "error": str(timeout_exc),
                            "error_type": "timeout",
                            "meta": timeout_meta,
                        }
                        self._record_timeout_failure(str(state.get("circuit_key") or ""))
                        ordered_results[int(state["index"])] = self._attach_batch_timing(
                            timeout_result,
                            started_perf=float(state["started_perf"]),
                            started_at_ms=int(state["started_at_ms"]),
                            finished_perf=finished_perf,
                            finished_at_ms=finished_at_ms,
                        )
                else:
                    next_pending.append(state)

            pending_states = next_pending
            if pending_states and not progressed:
                time.sleep(0.005)

        return ordered_results

    def invoke_batch_json(self, calls_json: str) -> list[dict[str, Any]]:
        """使用 JSON 字符串批量调用工具。

        Args:
            calls_json: JSON 数组，每项包含 tool_name 与 payload

        Returns:
            list[dict[str, Any]]: 与输入顺序一致的调用结果列表
        """
        calls = self._parse_json_array(calls_json)
        return self.invoke_batch(calls)

    def call_tool(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """按服务名与工具名调用工具。

        Args:
            server_name: 服务名
            tool_name: 工具名
            arguments: 工具参数

        Returns:
            dict[str, Any]: 统一封装的工具调用结果
        """
        canonical = self._canonical_name(server_name, tool_name)
        rows = self.invoke_batch(
            [
                {
                    "tool_name": canonical,
                    "payload": arguments if isinstance(arguments, dict) else {},
                }
            ]
        )
        if rows:
            return rows[0]
        return self._build_tool_not_found_response(
            tool_name,
            server_name=server_name,
            trace_id=self._resolve_trace_id(),
        )

    async def call_tool_async(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """按服务名与工具名异步调用工具。

        Args:
            server_name: 服务名
            tool_name: 工具名
            arguments: 工具参数

        Returns:
            dict[str, Any]: 统一封装的工具调用结果
        """
        return await asyncio.to_thread(self.call_tool, server_name, tool_name, arguments)
