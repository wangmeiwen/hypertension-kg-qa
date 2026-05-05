"""管理员可观测性服务。"""

from __future__ import annotations

from pathlib import Path
import json
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


class AdminObservabilityService:
    """封装管理员可观测性与运行明细。"""

    def __init__(self, log_api_error: Any) -> None:
        """初始化实例并注入运行依赖。

        Args:
            log_api_error: 统一记录管理员可观测性接口错误的回调。

        Returns:
            None: 无返回值。
        """
        self._log_api_error = log_api_error

    @staticmethod
    def _project_root() -> Path:
        """定位项目根目录。

        Args:
            无。

        Returns:
            Path: 当前仓库根目录路径。
        """
        return Path(__file__).resolve().parents[3]

    def _read_perf_event_logs(self, event_name: str, limit: int = 200) -> list[dict[str, Any]]:
        """从性能日志文件中读取指定事件的最近记录。

        Args:
            event_name: 需要匹配的性能事件名称。
            limit: 最多返回的事件数量。

        Returns:
            list[dict[str, Any]]: 反向时间顺序读取到的事件 payload 列表。
        """
        log_dir = self._project_root() / "logs"
        candidate_paths = [
            log_dir / "perf.log",
            log_dir / "perf.log.1",
            log_dir / "perf.log.2",
        ]

        lines: list[str] = []
        for path in candidate_paths:
            if not path.exists():
                continue
            try:
                lines.extend(path.read_text(encoding="utf-8", errors="ignore").splitlines())
            except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
                self._log_api_error("_read_perf_event_logs", "read_file", exc, path=str(path))
                continue

        if not lines:
            return []

        events: list[dict[str, Any]] = []
        marker = "[性能事件]"
        for line in reversed(lines):
            if marker not in line:
                continue
            marker_index = line.find(marker)
            if marker_index == -1:
                continue
            payload_text = line[marker_index + len(marker) :].strip()
            json_start = payload_text.find("{")
            if json_start == -1:
                continue
            payload_text = payload_text[json_start:]
            try:
                payload = json.loads(payload_text)
            except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
                self._log_api_error("_read_perf_event_logs", "json_parse", exc)
                continue
            if payload.get("event") != event_name:
                continue
            events.append(payload)
            if len(events) >= limit:
                break
        return events

    def _read_turn_logs(self, limit: int = 200) -> list[dict[str, Any]]:
        """读取会话 turn 级观测日志并合并记忆更新记录。

        Args:
            limit: 最多返回的 turn 记录数量。

        Returns:
            list[dict[str, Any]]: 按完成时间倒序排列的 turn 观测记录。
        """
        turns_root = self._project_root() / "logs" / "sessions"
        if not turns_root.exists():
            return []
        turn_rows: dict[str, dict[str, Any]] = {}
        memory_updates: dict[str, dict[str, Any]] = {}
        rows: list[dict[str, Any]] = []
        for turn_file in turns_root.glob("*/turns/*.jsonl"):
            try:
                for line in turn_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                    if not line.strip():
                        continue
                    payload = json.loads(line)
                    if not isinstance(payload, dict):
                        continue
                    record_type = str(payload.get("record_type") or "")
                    if record_type == "turn_observability":
                        turn_id = str(payload.get("turn_id") or "")
                        if turn_id:
                            turn_rows[turn_id] = payload
                        else:
                            rows.append(payload)
                    elif record_type == "turn_memory_update":
                        turn_id = str(payload.get("turn_id") or "")
                        if turn_id:
                            memory_updates[turn_id] = payload
            except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
                self._log_api_error("_read_turn_logs", "read_file", exc, path=str(turn_file))
                continue
        for turn_id, payload in turn_rows.items():
            memory_update = memory_updates.get(turn_id) if isinstance(memory_updates.get(turn_id), dict) else {}
            if memory_update:
                payload = {
                    **payload,
                    "llm_calls": max(int(payload.get("llm_calls") or 0), int(memory_update.get("llm_calls") or 0)),
                    "total_tokens": max(int(payload.get("total_tokens") or 0), int(memory_update.get("total_tokens") or 0)),
                    "error_types": (
                        {
                            **(payload.get("error_types") if isinstance(payload.get("error_types"), dict) else {}),
                            **(memory_update.get("error_types") if isinstance(memory_update.get("error_types"), dict) else {}),
                        }
                    ),
                }
            rows.append(payload)
        rows.sort(key=lambda item: str(item.get("finished_at") or item.get("timestamp") or ""), reverse=True)
        return rows[:limit]

    @staticmethod
    def _to_float_metric(value: Any) -> float:
        """将日志中的宽松指标值转换为浮点数。

        Args:
            value: 待处理输入值。

        Returns:
            float: 数值结果。
        """
        if isinstance(value, bool):
            return float(int(value))
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError as exc:
                logger.debug(
                    "admin_observability_float_parse_failed: raw=%s error_type=%s error=%s",
                    value,
                    type(exc).__name__,
                    exc,
                )
                return 0.0
        return 0.0

    @staticmethod
    def _to_int_metric(value: Any) -> int:
        """将日志中的宽松指标值转换为整数。

        Args:
            value: 待处理输入值。

        Returns:
            int: 数值结果。
        """
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            try:
                return int(float(value))
            except ValueError as exc:
                logger.debug(
                    "admin_observability_int_parse_failed: raw=%s error_type=%s error=%s",
                    value,
                    type(exc).__name__,
                    exc,
                )
                return 0
        return 0

    @staticmethod
    def _split_error_types(event: dict[str, Any], strategy_error_keys: set[str]) -> tuple[dict[str, int], dict[str, int]]:
        """拆分策略层错误和执行层错误计数。

        Args:
            event: 单条观测事件。
            strategy_error_keys: 归类为策略层错误的错误键集合。

        Returns:
            tuple[dict[str, int], dict[str, int]]: 策略层错误计数和执行层错误计数。
        """
        strategy_errors = event.get("strategy_error_types") or {}
        execution_errors = event.get("execution_error_types") or {}
        if isinstance(strategy_errors, dict) and isinstance(execution_errors, dict):
            return (
                {str(k): int(v or 0) for k, v in strategy_errors.items()},
                {str(k): int(v or 0) for k, v in execution_errors.items()},
            )

        raw_errors = event.get("error_types") or {}
        strategy_fallback: dict[str, int] = {}
        execution_fallback: dict[str, int] = {}
        if isinstance(raw_errors, dict):
            for key, value in raw_errors.items():
                error_key = str(key)
                error_count = int(value or 0)
                if error_key in strategy_error_keys:
                    strategy_fallback[error_key] = strategy_fallback.get(error_key, 0) + error_count
                else:
                    execution_fallback[error_key] = execution_fallback.get(error_key, 0) + error_count
        return strategy_fallback, execution_fallback

    def _normalize_run_row(self, event: dict[str, Any], rank: int) -> dict[str, Any]:
        """将原始观测事件规范化为管理员运行明细行。

        Args:
            event: 原始 turn 或性能事件。
            rank: 当前结果行排序序号。

        Returns:
            dict[str, Any]: 前端可直接展示的结构化运行明细。
        """
        strategy_error_keys = {
            "planner_error",
            "planner_upstream_error",
            "tool_not_allowed",
            "tool_temporarily_avoided",
            "duplicate_tool_call_blocked",
            "loop_guard_stop",
            "job_cancelled",
            "final_summarize_error",
        }
        strategy_errors, execution_errors = self._split_error_types(event, strategy_error_keys)
        timestamp = (
            str(event.get("finished_at") or "")
            or str(event.get("timestamp") or "")
            or str(event.get("started_at") or "")
        )
        return {
            "rank": rank,
            "timestamp": timestamp,
            "session_id": str(event.get("session_id") or ""),
            "user_id": str(event.get("user_id") or ""),
            "duration_ms": self._to_float_metric(event.get("duration_ms")),
            "llm_calls": self._to_int_metric(event.get("llm_calls")),
            "attempted_tool_actions": self._to_int_metric(event.get("attempted_tool_actions") if event.get("attempted_tool_actions") is not None else event.get("tool_calls")),
            "blocked_tool_actions": self._to_int_metric(event.get("blocked_tool_actions")),
            "tool_calls": self._to_int_metric(event.get("tool_calls")),
            "execution_tool_success_rate": self._to_float_metric(event.get("execution_tool_success_rate") if event.get("execution_tool_success_rate") is not None else event.get("tool_success_rate")),
            "attempted_action_success_rate": self._to_float_metric(event.get("attempted_action_success_rate") if event.get("attempted_action_success_rate") is not None else event.get("tool_success_rate")),
            "tool_success_rate": self._to_float_metric(event.get("tool_success_rate")),
            "total_tokens": self._to_int_metric(event.get("total_tokens")),
            "error_types": event.get("error_types") if isinstance(event.get("error_types"), dict) else {},
            "strategy_error_types": strategy_errors,
            "execution_error_types": execution_errors,
            "tool_observations": event.get("tool_observations") if isinstance(event.get("tool_observations"), list) else [],
            "reflection_events": event.get("reflection_events") if isinstance(event.get("reflection_events"), list) else [],
            "turn_metrics": event.get("turn_metrics") if isinstance(event.get("turn_metrics"), list) else [],
            "processing_steps": event.get("processing_steps") if isinstance(event.get("processing_steps"), list) else [],
        }

    async def admin_agent_observability_summary(self, limit: int = 200) -> dict[str, Any]:
        """获取 Agent 可观测性汇总指标。

        Args:
            limit: 参与汇总的最近记录数量。

        Returns:
            dict[str, Any]: 总运行数、平均耗时、工具成功率、token 和错误类型汇总。
        """
        safe_limit = max(10, min(limit, 2000))
        primary_events = self._read_turn_logs(limit=safe_limit)
        events = primary_events or self._read_perf_event_logs("iterative_agent_run", limit=safe_limit)
        if not events:
            return {
                "ok": True,
                "data": {
                    "total_runs": 0,
                    "avg_duration_ms": 0.0,
                    "avg_execution_tool_success_rate": 0.0,
                    "avg_attempted_action_success_rate": 0.0,
                    "avg_tool_success_rate": 0.0,
                    "total_tokens": 0,
                    "total_llm_calls": 0,
                    "total_attempted_tool_actions": 0,
                    "total_blocked_tool_actions": 0,
                    "total_tool_calls": 0,
                    "error_types": {},
                    "strategy_error_types": {},
                    "execution_error_types": {},
                },
            }

        total_runs = len(events)
        total_duration = 0.0
        total_success_rate = 0.0
        total_execution_success_rate = 0.0
        total_attempted_success_rate = 0.0
        total_tokens = 0
        total_llm_calls = 0
        total_attempted_tool_actions = 0
        total_blocked_tool_actions = 0
        total_tool_calls = 0
        error_counter: dict[str, int] = {}
        strategy_error_counter: dict[str, int] = {}
        execution_error_counter: dict[str, int] = {}
        strategy_error_keys = {
            "planner_error",
            "planner_upstream_error",
            "tool_not_allowed",
            "tool_temporarily_avoided",
            "duplicate_tool_call_blocked",
            "loop_guard_stop",
            "job_cancelled",
            "final_summarize_error",
        }

        for event in events:
            execution_rate_value = event.get("execution_tool_success_rate") if event.get("execution_tool_success_rate") is not None else event.get("tool_success_rate")
            attempted_rate_value = event.get("attempted_action_success_rate") if event.get("attempted_action_success_rate") is not None else event.get("tool_success_rate")
            attempted_actions_value = event.get("attempted_tool_actions") if event.get("attempted_tool_actions") is not None else event.get("tool_calls")
            total_duration += self._to_float_metric(event.get("duration_ms"))
            total_success_rate += self._to_float_metric(event.get("tool_success_rate"))
            total_execution_success_rate += self._to_float_metric(execution_rate_value)
            total_attempted_success_rate += self._to_float_metric(attempted_rate_value)
            total_tokens += self._to_int_metric(event.get("total_tokens"))
            total_llm_calls += self._to_int_metric(event.get("llm_calls"))
            total_attempted_tool_actions += self._to_int_metric(attempted_actions_value)
            total_blocked_tool_actions += self._to_int_metric(event.get("blocked_tool_actions"))
            total_tool_calls += self._to_int_metric(event.get("tool_calls"))

            event_errors = event.get("error_types") or {}
            if isinstance(event_errors, dict):
                for key, value in event_errors.items():
                    safe_key = str(key)
                    error_counter[safe_key] = int(error_counter.get(safe_key) or 0) + int(value or 0)

            strategy_errors, execution_errors = self._split_error_types(event, strategy_error_keys)
            for key, value in strategy_errors.items():
                strategy_error_counter[key] = int(strategy_error_counter.get(key) or 0) + int(value or 0)
            for key, value in execution_errors.items():
                execution_error_counter[key] = int(execution_error_counter.get(key) or 0) + int(value or 0)

        return {
            "ok": True,
            "data": {
                "total_runs": total_runs,
                "avg_duration_ms": round(total_duration / total_runs, 2),
                "avg_execution_tool_success_rate": round(total_execution_success_rate / total_runs, 4),
                "avg_attempted_action_success_rate": round(total_attempted_success_rate / total_runs, 4),
                "avg_tool_success_rate": round(total_success_rate / total_runs, 4),
                "total_tokens": total_tokens,
                "total_llm_calls": total_llm_calls,
                "total_attempted_tool_actions": total_attempted_tool_actions,
                "total_blocked_tool_actions": total_blocked_tool_actions,
                "total_tool_calls": total_tool_calls,
                "error_types": error_counter,
                "strategy_error_types": strategy_error_counter,
                "execution_error_types": execution_error_counter,
            },
        }

    async def admin_agent_observability_runs(self, limit: int = 50) -> dict[str, Any]:
        """获取 Agent 近期运行明细。

        Args:
            limit: 返回的最近运行记录数量。

        Returns:
            dict[str, Any]: 运行明细列表和实际返回数量。
        """
        safe_limit = max(1, min(limit, 200))
        primary_events = self._read_turn_logs(limit=safe_limit)
        events = primary_events or self._read_perf_event_logs("iterative_agent_run", limit=safe_limit)
        rows = [self._normalize_run_row(event, index + 1) for index, event in enumerate(events)]
        return {"ok": True, "data": rows, "count": len(rows)}
