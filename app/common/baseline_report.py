"""
基线性能报告模块。

基于 logs/api/*.jsonl 生成接口耗时基线统计，输出：
- 总体与目标路径的 P50/P95/P99
- 错误率与样本量
- 最慢路径排名

"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Tuple


def _percentile(sorted_values: List[float], p: float) -> float:
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
    safe_days = max(1, min(int(days), 30))
    today = datetime.now().date()
    return {(today - timedelta(days=offset)).strftime("%Y-%m-%d") for offset in range(safe_days)}


def _collect_api_rows(days: int) -> List[Dict[str, Any]]:
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
                    except (ValueError, TypeError):
                        continue
                    if isinstance(row, dict):
                        rows.append(row)
        except OSError:
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
            duration_ms = float(row.get("duration_ms") or 0.0)
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


