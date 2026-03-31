"""A2A HTTP benchmark script.

Usage:
    python tests/a2a_http_benchmark.py --base-url http://127.0.0.1:8000 --requests 20 --concurrency 5
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Tuple
from urllib import error, request


def _http_json(method: str, url: str, payload: Dict[str, Any], timeout: float) -> Dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        url=url,
        data=data,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    with request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        body = resp.read().decode("utf-8")
        return json.loads(body) if body else {}


def _rpc_call(base_url: str, rpc_path: str, rpc_payload: Dict[str, Any], timeout: float) -> Dict[str, Any]:
    url = f"{base_url.rstrip('/')}{rpc_path}"
    return _http_json("POST", url, rpc_payload, timeout=timeout)


def _wait_task_terminal(
    base_url: str,
    rpc_path: str,
    task_id: str,
    timeout_seconds: float,
    poll_ms: int,
    request_timeout: float,
) -> Dict[str, Any]:
    started = time.perf_counter()
    while True:
        resp = _rpc_call(
            base_url=base_url,
            rpc_path=rpc_path,
            rpc_payload={
                "jsonrpc": "2.0",
                "id": "task-get",
                "method": "tasks/get",
                "params": {"taskId": task_id},
            },
            timeout=request_timeout,
        )
        result = resp.get("result") or {}
        task = result.get("task") or {}
        state = str(((task.get("status") or {}).get("state") or "")).strip().lower()
        if state in {"completed", "failed", "canceled"}:
            return {"state": state, "task": task}
        if (time.perf_counter() - started) >= timeout_seconds:
            return {"state": "timeout", "task": task}
        time.sleep(max(0.05, poll_ms / 1000.0))


def _percentile(values: List[float], ratio: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    rank = max(0.0, min(1.0, ratio)) * (len(values) - 1)
    low = int(rank)
    high = min(low + 1, len(values) - 1)
    weight = rank - low
    return values[low] * (1.0 - weight) + values[high] * weight


def _run_one(index: int, args: argparse.Namespace) -> Dict[str, Any]:
    begin = time.perf_counter()
    try:
        send = _rpc_call(
            base_url=args.base_url,
            rpc_path=args.rpc_path,
            timeout=args.request_timeout,
            rpc_payload={
                "jsonrpc": "2.0",
                "id": f"send-{index}",
                "method": args.send_method,
                "params": {
                    "message": {
                        "role": "user",
                        "parts": [{"kind": "text", "text": f"{args.prompt} #{index}"}],
                        "metadata": {
                            "session_id": f"{args.session_prefix}-{index}",
                            "user_id": args.user_id,
                            "strategy": args.strategy,
                        },
                    }
                },
            },
        )
        send_result = send.get("result") or {}
        task = send_result.get("task") or {}
        task_id = str(task.get("id") or "").strip()
        if not task_id:
            return {
                "ok": False,
                "error": f"missing_task_id:{send}",
                "duration_ms": (time.perf_counter() - begin) * 1000.0,
            }

        terminal = _wait_task_terminal(
            base_url=args.base_url,
            rpc_path=args.rpc_path,
            task_id=task_id,
            timeout_seconds=args.task_timeout,
            poll_ms=args.poll_ms,
            request_timeout=args.request_timeout,
        )
        state = str(terminal.get("state") or "")
        duration_ms = (time.perf_counter() - begin) * 1000.0
        return {
            "ok": state == "completed",
            "state": state,
            "task_id": task_id,
            "duration_ms": duration_ms,
        }
    except (TimeoutError, ValueError, error.URLError, error.HTTPError) as exc:
        return {
            "ok": False,
            "error": str(exc),
            "duration_ms": (time.perf_counter() - begin) * 1000.0,
        }


def run_benchmark(args: argparse.Namespace) -> None:
    print(
        json.dumps(
            {
                "base_url": args.base_url,
                "rpc_path": args.rpc_path,
                "requests": args.requests,
                "concurrency": args.concurrency,
                "send_method": args.send_method,
            },
            ensure_ascii=False,
        )
    )
    started = time.perf_counter()
    rows: List[Dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as executor:
        futures = [executor.submit(_run_one, idx, args) for idx in range(1, args.requests + 1)]
        for future in as_completed(futures):
            rows.append(future.result())

    elapsed_s = max(0.0001, time.perf_counter() - started)
    total = len(rows)
    ok_rows = [row for row in rows if bool(row.get("ok"))]
    fail_rows = [row for row in rows if not bool(row.get("ok"))]
    durations = sorted(float(row.get("duration_ms") or 0.0) for row in rows)

    state_counter: Dict[str, int] = {}
    for row in rows:
        state = str(row.get("state") or "error")
        state_counter[state] = int(state_counter.get(state, 0)) + 1

    summary = {
        "total": total,
        "ok": len(ok_rows),
        "failed": len(fail_rows),
        "success_rate": round(len(ok_rows) / max(1, total), 4),
        "throughput_rps": round(total / elapsed_s, 3),
        "latency_ms": {
            "avg": round(statistics.mean(durations), 2) if durations else 0.0,
            "p50": round(_percentile(durations, 0.50), 2) if durations else 0.0,
            "p95": round(_percentile(durations, 0.95), 2) if durations else 0.0,
            "p99": round(_percentile(durations, 0.99), 2) if durations else 0.0,
            "max": round(max(durations), 2) if durations else 0.0,
        },
        "states": state_counter,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if fail_rows:
        sample_errors: List[Tuple[str, str]] = []
        for row in fail_rows[:5]:
            sample_errors.append((str(row.get("state") or "error"), str(row.get("error") or "")))
        print(json.dumps({"sample_failures": sample_errors}, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="A2A HTTP benchmark")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Target service base URL")
    parser.add_argument("--rpc-path", default="/a2a/rpc", help="A2A JSON-RPC path")
    parser.add_argument("--requests", type=int, default=20, help="Total request count")
    parser.add_argument("--concurrency", type=int, default=5, help="Worker concurrency")
    parser.add_argument("--request-timeout", type=float, default=20.0, help="Single HTTP request timeout seconds")
    parser.add_argument("--task-timeout", type=float, default=90.0, help="Task terminal wait timeout seconds")
    parser.add_argument("--poll-ms", type=int, default=300, help="Task polling interval milliseconds")
    parser.add_argument("--send-method", default="message/send", choices=["message/send", "message/stream"])
    parser.add_argument("--prompt", default="请给出高血压诊疗建议摘要", help="Prompt prefix")
    parser.add_argument("--session-prefix", default="a2a-bench", help="Session id prefix")
    parser.add_argument("--user-id", default="a2a-benchmark", help="User id")
    parser.add_argument("--strategy", default="iterative", help="strategy metadata")
    return parser


if __name__ == "__main__":
    run_benchmark(build_parser().parse_args())

