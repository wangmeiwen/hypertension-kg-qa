"""批量执行评测数据集并保存结果文件。"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import uuid
from typing import Any, Iterable

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET = (
    PROJECT_ROOT
    / "data"
    / "ragas_eval_30_hypertension_guideline_2024_userlike_ground_truth.json"
)
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "tests" / "ragas" / "eval_runs"
TEXT_KEYS = {
    "text",
    "content",
    "snippet",
    "chunk_text",
    "passage",
    "parent_content",
    "parent_text",
    "result_content",
}


def parse_args() -> argparse.Namespace:
    """解析批量评测脚本命令行参数。"""
    parser = argparse.ArgumentParser(
        description="Run the local eval dataset against the QA service."
    )
    parser.add_argument(
        "--base-url", default="http://127.0.0.1:8000", help="Backend base URL."
    )
    parser.add_argument(
        "--endpoint",
        default="/process",
        choices=["/process", "/agent/run"],
        help="QA endpoint.",
    )
    parser.add_argument(
        "--dataset", default=str(DEFAULT_DATASET), help="Dataset JSON path."
    )
    parser.add_argument("--output", default="", help="Optional output JSON path.")
    parser.add_argument(
        "--limit", type=int, default=0, help="Only run the first N rows when > 0."
    )
    parser.add_argument(
        "--timeout", type=float, default=180.0, help="Per-request timeout in seconds."
    )
    return parser.parse_args()


def load_dataset(path: Path) -> list[dict[str, Any]]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError("dataset_must_be_a_list")
    items: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        items.append(
            {
                "index": index,
                "question": str(row.get("question") or ""),
                "contexts": (
                    row.get("contexts") if isinstance(row.get("contexts"), list) else []
                ),
                "ground_truth": str(row.get("ground_truth") or ""),
            }
        )
    return items


def iter_json_lines(response: requests.Response) -> Iterable[dict[str, Any]]:
    """迭代后端流式响应中的 JSON 行，非 JSON 行降级为 msg 事件。"""
    for line in response.iter_lines(decode_unicode=True):
        if not line:
            continue
        text = str(line).strip()
        if not text:
            continue
        try:
            payload = json.loads(text)
        except (TypeError, ValueError):
            yield {"type": "msg", "msg": text}
            continue
        if isinstance(payload, dict):
            yield payload


def collect_candidate_texts(value: Any, results: list[str]) -> None:
    if isinstance(value, str):
        text = value.strip()
        append_unique_text(text, results)
        return
    if isinstance(value, list):
        for item in value:
            collect_candidate_texts(item, results)
        return
    if not isinstance(value, dict):
        return
    for key, nested in value.items():
        key_text = str(key or "").strip().lower()
        if key_text in TEXT_KEYS and isinstance(nested, str):
            collect_candidate_texts(nested, results)
            continue
        if key_text in {
            "tool_input",
            "tool_output",
            "metadata",
            "payload",
            "result",
            "evidence",
            "items",
            "records",
            "query_records",
        }:
            collect_candidate_texts(nested, results)


def append_unique_text(text: str, results: list[str]) -> None:
    value = str(text or "").strip()
    if len(value) < 12:
        return
    for existing in list(results):
        if value == existing or value in existing:
            return
        if existing in value:
            results.remove(existing)
    results.append(value)


def collect_supported_points(payload: dict[str, Any], results: list[str]) -> None:
    summary = payload.get("evidence_summary")
    if not isinstance(summary, dict):
        return
    supported_points = summary.get("supported_points")
    if not isinstance(supported_points, list):
        return
    for item in supported_points:
        append_unique_text(str(item or "").strip(), results)


def collect_query_record_texts(payload: dict[str, Any], results: list[str]) -> None:
    observations = payload.get("tool_observations")
    if not isinstance(observations, list):
        return
    for obs in observations:
        if not isinstance(obs, dict):
            continue
        for container in (
            obs,
            obs.get("result"),
            (
                obs.get("result", {}).get("result")
                if isinstance(obs.get("result"), dict)
                else None
            ),
        ):
            if not isinstance(container, dict):
                continue
            records = container.get("query_records")
            if not isinstance(records, list):
                continue
            for record in records:
                if not isinstance(record, dict):
                    continue
                append_unique_text(
                    str(record.get("result_content") or "").strip(), results
                )


def extract_retrieved_contexts(payload: dict[str, Any]) -> list[str]:
    """从完成事件或 JSON 响应中提取 Ragas 可用的检索上下文。"""
    candidates: list[str] = []
    collect_supported_points(payload, candidates)
    collect_query_record_texts(payload, candidates)
    for key in ("tool_observations", "evidence_summary", "telemetry"):
        collect_candidate_texts(payload.get(key), candidates)
    return candidates[:8]


def run_stream_request(
    base_url: str, endpoint: str, question: str, timeout: float
) -> dict[str, Any]:
    user_id = f"eval_{uuid.uuid4().hex[:10]}"
    response = requests.post(
        f"{base_url.rstrip('/')}{endpoint}",
        json={"input": question, "user_id": user_id},
        timeout=timeout,
        stream=True,
    )
    response.raise_for_status()

    final_reply = ""
    done_event: dict[str, Any] = {}
    error_event: dict[str, Any] = {}
    traces: list[dict[str, Any]] = []
    task_id = ""

    for event in iter_json_lines(response):
        event_type = str(event.get("type") or "").strip().lower()
        if event_type == "job":
            task_id = str(event.get("task_id") or event.get("job_id") or "")
        elif event_type == "reply":
            final_reply += str(event.get("delta") or "")
        elif event_type == "done":
            done_event = event
            traces = (
                event.get("traces") if isinstance(event.get("traces"), list) else traces
            )
        elif event_type == "error":
            error_event = event

    status = str(done_event.get("status") or "")
    if error_event:
        status = "failed"
    return {
        "status": status,
        "answer": final_reply.strip(),
        "error": str(error_event.get("message") or ""),
        "traces": traces,
        "tool_observations": (
            done_event.get("tool_observations")
            if isinstance(done_event.get("tool_observations"), list)
            else []
        ),
        "evidence_summary": (
            done_event.get("evidence_summary")
            if isinstance(done_event.get("evidence_summary"), dict)
            else {}
        ),
        "metadata": {"task_id": task_id, "request_type": "stream"},
    }


def run_json_request(
    base_url: str, endpoint: str, question: str, timeout: float
) -> dict[str, Any]:
    user_id = f"eval_{uuid.uuid4().hex[:10]}"
    response = requests.post(
        f"{base_url.rstrip('/')}{endpoint}",
        json={"input": question, "user_id": user_id, "strategy": "iterative"},
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("invalid_agent_run_response")
    return {
        "status": "done" if bool(payload.get("ok")) else "failed",
        "answer": str(payload.get("answer") or ""),
        "error": str(payload.get("error") or ""),
        "traces": (
            payload.get("traces") if isinstance(payload.get("traces"), list) else []
        ),
        "tool_observations": (
            payload.get("tool_observations")
            if isinstance(payload.get("tool_observations"), list)
            else []
        ),
        "evidence_summary": (
            payload.get("evidence_summary")
            if isinstance(payload.get("evidence_summary"), dict)
            else {}
        ),
        "metadata": {
            "trace_id": str(payload.get("trace_id") or ""),
            "a2a_task_id": str(payload.get("a2a_task_id") or ""),
            "confidence": payload.get("confidence"),
            "request_type": "json",
        },
    }


def build_output_path(dataset_path: Path, output_arg: str) -> Path:
    if output_arg:
        return Path(output_arg)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return DEFAULT_OUTPUT_DIR / f"{dataset_path.stem}__run_{timestamp}.json"


def main() -> None:
    """执行数据集请求、结果收集和输出文件保存。"""
    args = parse_args()
    dataset_path = Path(args.dataset)
    rows = load_dataset(dataset_path)
    if args.limit and args.limit > 0:
        rows = rows[: args.limit]

    output_path = build_output_path(dataset_path, args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    request_fn = (
        run_json_request if args.endpoint == "/agent/run" else run_stream_request
    )

    items: list[dict[str, Any]] = []
    for row in rows:
        print(f"[{row['index'] + 1}/{len(rows)}] {row['question']}")
        try:
            result = request_fn(
                args.base_url, args.endpoint, row["question"], args.timeout
            )
        except requests.RequestException as exc:
            result = {
                "status": "failed",
                "answer": "",
                "error": f"request_error:{type(exc).__name__}:{exc}",
                "traces": [],
                "tool_observations": [],
                "evidence_summary": {},
                "metadata": {"request_type": "request_error"},
            }
        except Exception as exc:  # noqa: BLE001
            result = {
                "status": "failed",
                "answer": "",
                "error": f"runtime_error:{type(exc).__name__}:{exc}",
                "traces": [],
                "tool_observations": [],
                "evidence_summary": {},
                "metadata": {"request_type": "runtime_error"},
            }

        items.append(
            {
                **row,
                "answer": result["answer"],
                "status": result["status"],
                "error": result["error"],
                "traces": result["traces"],
                "tool_observations": result["tool_observations"],
                "evidence_summary": result["evidence_summary"],
                "retrieved_contexts": extract_retrieved_contexts(result),
                "metadata": result["metadata"],
            }
        )

    payload = {
        "dataset_name": dataset_path.name,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "base_url": args.base_url.rstrip("/"),
        "endpoint": args.endpoint,
        "item_count": len(items),
        "items": items,
    }
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"saved_to={output_path}")


if __name__ == "__main__":
    main()
