"""
基线性能报告模块。

基于 logs/api/*.jsonl 生成接口耗时基线统计，输出：
- 总体与目标路径的 P50/P95/P99
- 错误率与样本量
- 最慢路径排名

"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)


def _percentile(sorted_values: List[float], p: float) -> float:
    """计算分位数（线性插值）。

    输入列表必须已按升序排列；函数对 `p` 自动裁剪到 [0, 1]。

    Args:
        sorted_values: 升序数值列表。
        p: 分位点，取值范围通常为 0.0 到 1.0。

    Returns:
        float: 对应分位数值；空列表时返回 0.0。
    """
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    rank = max(0.0, min(1.0, p)) * (len(sorted_values) - 1)
    low = int(rank)
    high = min(low + 1, len(sorted_values) - 1)
    weight = rank - low
    return float(sorted_values[low] * (1.0 - weight) + sorted_values[high] * weight)


def _build_stats(latencies: List[float], error_count: int) -> Dict[str, Any]:
    """根据耗时样本构建统计摘要。

    输出包含样本量、错误率、P50/P95/P99、均值与极值，
    便于直接用于性能看板或日报输出。

    Args:
        latencies: 请求耗时列表（毫秒）。
        error_count: 错误请求数量（通常是 5xx）。

    Returns:
        Dict[str, Any]: 结构化统计结果。
    """
    if not latencies:
        return {
            "count": 0,
            "error_count": int(error_count),
            "error_rate": 0.0,
            "p50_ms": 0.0,
            "p95_ms": 0.0,
            "p99_ms": 0.0,
            "avg_ms": 0.0,
            "max_ms": 0.0,
            "min_ms": 0.0,
        }
    values = sorted(float(item) for item in latencies)
    count = len(values)
    return {
        "count": count,
        "error_count": int(error_count),
        "error_rate": round(float(error_count) / float(count), 6),
        "p50_ms": round(_percentile(values, 0.50), 2),
        "p95_ms": round(_percentile(values, 0.95), 2),
        "p99_ms": round(_percentile(values, 0.99), 2),
        "avg_ms": round(sum(values) / float(count), 2),
        "max_ms": round(values[-1], 2),
        "min_ms": round(values[0], 2),
    }


def _target_dates(days: int) -> set[str]:
    """生成目标统计日期集合。

    会将输入天数限制在 1 到 30 之间，避免读取过多历史日志。

    Args:
        days: 希望统计的最近天数。

    Returns:
        set[str]: 形如 YYYY-MM-DD 的日期字符串集合。
    """
    safe_days = max(1, min(int(days), 30))
    today = datetime.now().date()
    return {(today - timedelta(days=offset)).strftime("%Y-%m-%d") for offset in range(safe_days)}


def _collect_api_rows(days: int) -> List[Dict[str, Any]]:
    """读取目标日期范围内的 API 日志行。

    从 `logs/api/*.jsonl` 读取并解析 JSON 行，自动跳过空行、损坏行与 IO 异常文件。

    Args:
        days: 最近统计天数。

    Returns:
        List[Dict[str, Any]]: 可用日志记录列表。
    """
    api_dir = Path(__file__).resolve().parents[2] / "logs" / "api"
    if not api_dir.exists():
        return []
    dates = _target_dates(days)
    rows: List[Dict[str, Any]] = []
    for path in sorted(api_dir.glob("*.jsonl")):
        if path.stem not in dates:
            continue
        try:
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    text = line.strip()
                    if not text:
                        continue
                    try:
                        row = json.loads(text)
                    except (ValueError, TypeError) as exc:
                        logger.debug(
                            "baseline_report_skip_invalid_json_line: file=%s error_type=%s error=%s",
                            str(path),
                            type(exc).__name__,
                            exc,
                        )
                        continue
                    if isinstance(row, dict):
                        rows.append(row)
        except OSError as exc:
            logger.warning(
                "baseline_report_read_file_failed: file=%s error_type=%s error=%s",
                str(path),
                type(exc).__name__,
                exc,
            )
            continue
    return rows


def generate_api_baseline_report(days: int = 1, target_paths: List[str] | None = None) -> Dict[str, Any]:
    """
    生成 API 基线性能报告。

    Args:
        days: 统计最近天数，默认 1 天
        target_paths: 重点路径列表，支持前缀匹配

    Returns:
        Dict[str, Any]: 基线报告
    """
    rows = _collect_api_rows(days=days)
    targets = target_paths or [
        "/agent/run",
        "/process_with_session",
        "/mcp/invoke",
        "/admin/graph/",
    ]

    overall_latencies: List[float] = []
    overall_error_count = 0
    per_path: Dict[str, List[float]] = {}
    per_path_errors: Dict[str, int] = {}

    for row in rows:
        path = str(row.get("path") or "").strip()
        if not path:
            continue
        try:
            duration_ms = float(
                row.get("stream_total_ms")
                if row.get("stream_total_ms") is not None
                else (row.get("duration_ms") if row.get("duration_ms") is not None else 0.0)
            )
        except (ValueError, TypeError):
            duration_ms = 0.0
        status_code = int(row.get("status_code") or 0)

        overall_latencies.append(duration_ms)
        if status_code >= 500:
            overall_error_count += 1

        per_path.setdefault(path, []).append(duration_ms)
        if status_code >= 500:
            per_path_errors[path] = int(per_path_errors.get(path, 0)) + 1

    target_stats: Dict[str, Dict[str, Any]] = {}
    for target in targets:
        combined: List[float] = []
        error_count = 0
        for path, latencies in per_path.items():
            if path == target or path.startswith(target):
                combined.extend(latencies)
                error_count += int(per_path_errors.get(path, 0))
        target_stats[target] = _build_stats(combined, error_count)

    top_slowest_paths: List[Tuple[str, float, int]] = []
    for path, latencies in per_path.items():
        if not latencies:
            continue
        avg_ms = sum(latencies) / float(len(latencies))
        top_slowest_paths.append((path, avg_ms, len(latencies)))
    top_slowest_paths.sort(key=lambda item: item[1], reverse=True)

    return {
        "window_days": max(1, min(int(days), 30)),
        "sample_count": len(overall_latencies),
        "overall": _build_stats(overall_latencies, overall_error_count),
        "targets": target_stats,
        "top_slowest_paths": [
            {"path": path, "avg_ms": round(avg_ms, 2), "count": count}
            for path, avg_ms, count in top_slowest_paths[:20]
        ],
    }


