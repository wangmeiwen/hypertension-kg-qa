"""汇总多个检索模式结果并生成确定性消融报告。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.evaluation.retrieval_metrics import (  # noqa: E402
    RETRIEVAL_ABLATION_MODES,
    RetrievalEvaluationCase,
    RetrievalGoldEvidence,
    RetrievalHit,
    RetrievalRunItem,
    evaluate_retrieval_ablation,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build deterministic retrieval ablation metrics."
    )
    parser.add_argument("--dataset", required=True)
    parser.add_argument(
        "--run",
        action="append",
        required=True,
        help="Mode and result path in mode=path form; repeat for each mode.",
    )
    parser.add_argument("--output", required=True)
    parser.add_argument("--k", type=int, default=5)
    return parser.parse_args()


def _resolve_path(raw_path: str) -> Path:
    path = Path(raw_path)
    return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def _load_dataset(path: Path) -> tuple[list[RetrievalEvaluationCase], str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        raw_cases = payload.get("cases")
        review_status = str(payload.get("review_status") or "untracked")
    else:
        raw_cases = payload
        review_status = "untracked"
    if not isinstance(raw_cases, list):
        raise ValueError("ablation_dataset_cases_must_be_list")
    cases: list[RetrievalEvaluationCase] = []
    for index, row in enumerate(raw_cases):
        if not isinstance(row, dict):
            continue
        case_id = str(row.get("case_id") or f"q{index + 1:03d}")
        relevant_evidence: list[RetrievalGoldEvidence] = []
        raw_relevant = row.get("relevant_evidence")
        if isinstance(raw_relevant, list):
            relevant_evidence.extend(
                item for item in raw_relevant if isinstance(item, dict)
            )
        contexts = row.get("contexts")
        if not relevant_evidence and isinstance(contexts, list):
            relevant_evidence.extend(
                {"text": str(item).strip()} for item in contexts if str(item).strip()
            )
        cases.append(
            {
                "case_id": case_id,
                "question": str(row.get("question") or "").strip(),
                "relevant_evidence": relevant_evidence,
            }
        )
    return cases, review_status


def _parse_run_spec(raw_spec: str) -> tuple[str, Path]:
    mode, separator, raw_path = raw_spec.partition("=")
    mode = mode.strip().lower()
    if separator != "=" or mode not in RETRIEVAL_ABLATION_MODES:
        raise ValueError(f"invalid_run_spec:{raw_spec}")
    return mode, _resolve_path(raw_path.strip())


def _normalize_hits(row: dict[str, Any]) -> list[RetrievalHit]:
    raw_hits = row.get("hits")
    if isinstance(raw_hits, list):
        return [
            {
                "evidence_id": str(
                    item.get("evidence_id")
                    or item.get("source_id")
                    or item.get("chunk_id")
                    or ""
                ),
                "text": str(item.get("text") or item.get("content") or ""),
                "score": float(item.get("score") or 0),
            }
            for item in raw_hits
            if isinstance(item, dict)
        ]
    contexts = row.get("retrieved_contexts")
    if isinstance(contexts, list):
        return [
            {"evidence_id": "", "text": str(item), "score": 0.0} for item in contexts
        ]
    return []


def _load_run(path: Path, mode: str) -> list[RetrievalRunItem]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_items = payload.get("items") if isinstance(payload, dict) else payload
    if not isinstance(raw_items, list):
        raise ValueError(f"ablation_run_items_must_be_list:{path}")
    items: list[RetrievalRunItem] = []
    for index, row in enumerate(raw_items):
        if not isinstance(row, dict):
            continue
        metadata = row.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        status = str(row.get("status") or "").strip().lower()
        items.append(
            {
                "case_id": str(row.get("case_id") or f"q{index + 1:03d}"),
                "mode": mode,
                "status": "succeeded" if status in {"done", "succeeded"} else "failed",
                "hits": _normalize_hits(row),
                "latency_ms": float(
                    row.get("latency_ms") or metadata.get("latency_ms") or 0
                ),
                "token_count": int(
                    row.get("token_count") or metadata.get("token_count") or 0
                ),
                "error": str(row.get("error") or ""),
            }
        )
    return items


def main() -> None:
    args = parse_args()
    dataset_path = _resolve_path(args.dataset)
    cases, review_status = _load_dataset(dataset_path)
    run_items: list[RetrievalRunItem] = []
    run_sources: dict[str, str] = {}
    for raw_spec in args.run:
        mode, run_path = _parse_run_spec(raw_spec)
        if mode in run_sources:
            raise ValueError(f"duplicate_run_mode:{mode}")
        run_items.extend(_load_run(run_path, mode))
        run_sources[mode] = str(run_path)
    report = evaluate_retrieval_ablation(cases, run_items, k=args.k)
    report.update(
        {
            "dataset": str(dataset_path),
            "dataset_review_status": review_status,
            "run_sources": run_sources,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    output_path = _resolve_path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"saved_to={output_path}")


if __name__ == "__main__":
    main()
