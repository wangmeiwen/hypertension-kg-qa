"""管理员可观测性服务。"""

from __future__ import annotations

from pathlib import Path
import json
from typing import Any, Dict


class AdminObservabilityService:
    """封装管理员可观测性与运行明细。"""

    def __init__(self, log_api_error: Any) -> None:
        self._log_api_error = log_api_error

    def _read_perf_event_logs(self, event_name: str, limit: int = 200) -> list[dict[str, Any]]:
        """从 logs/perf.log 读取性能事件。"""
        project_root = Path(__file__).resolve().parents[3]
        log_dir = project_root / "logs"
        candidate_paths = [
            log_dir / "perf.log",
            log_dir / "perf.log.1",
            log_dir / "perf.log.2",
            Path("logs") / "perf.log",
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

    @staticmethod
    def _split_error_types(event: dict[str, Any], strategy_error_keys: set[str]) -> tuple[dict[str, int], dict[str, int]]:
        """拆分策略与执行错误。"""
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

    @staticmethod
    def _to_float_metric(value: Any) -> float:
        """统一浮点指标转换。"""
        if isinstance(value, bool):
            return float(int(value))
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                return 0.0
        return 0.0

    @staticmethod
    def _to_int_metric(value: Any) -> int:
        """统一整型指标转换。"""
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            try:
                return int(float(value))
            except ValueError:
                return 0
        return 0

    async def admin_agent_observability_summary(self, limit: int = 200) -> dict[str, Any]:
        """获取 Agent 可观测性汇总。"""
        safe_limit = max(10, min(limit, 2000))
        events = self._read_perf_event_logs("iterative_agent_run", limit=safe_limit)
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
        """获取 Agent 近期运行明细。"""
        safe_limit = max(1, min(limit, 200))
        events = self._read_perf_event_logs("iterative_agent_run", limit=safe_limit)
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

        rows: list[dict[str, Any]] = []
        for index, event in enumerate(events):
            strategy_errors, execution_errors = self._split_error_types(event, strategy_error_keys)
            rows.append(
                {
                    "rank": index + 1,
                    "timestamp": str(event.get("timestamp") or ""),
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
                    "error_types": event.get("error_types") or {},
                    "strategy_error_types": strategy_errors,
                    "execution_error_types": execution_errors,
                    "tool_observations": event.get("tool_observations") if isinstance(event.get("tool_observations"), list) else [],
                    "reflection_events": event.get("reflection_events") if isinstance(event.get("reflection_events"), list) else [],
                    "turn_metrics": event.get("turn_metrics") if isinstance(event.get("turn_metrics"), list) else [],
                    "processing_steps": event.get("processing_steps") if isinstance(event.get("processing_steps"), list) else [],
                }
            )

        return {"ok": True, "data": rows, "count": len(rows)}
