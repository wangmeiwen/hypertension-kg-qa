"""对已有离线测试结果执行 Ragas 自动评估。"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import math
import os
from pathlib import Path
import sys
from typing import Any

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]

METRIC_SPECS = {
    "faithfulness": {
        "actual_name": "faithfulness",
        "class_name": "Faithfulness",
    },
    "answer_relevancy": {
        "actual_name": "answer_relevancy",
        "class_name": "ResponseRelevancy",
    },
    "context_precision": {
        "actual_name": "llm_context_precision_with_reference",
        "class_name": "LLMContextPrecisionWithReference",
    },
    "context_recall": {
        "actual_name": "context_recall",
        "class_name": "LLMContextRecall",
    },
}

METRIC_ALIASES = {
    "faithfulness": "faithfulness",
    "answer_relevancy": "answer_relevancy",
    "response_relevancy": "answer_relevancy",
    "context_precision": "context_precision",
    "llm_context_precision_with_reference": "context_precision",
    "context_recall": "context_recall",
    "llm_context_recall": "context_recall",
    "factual_correctness": "factual_correctness",
    "factual": "factual_correctness",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Ragas scoring on an existing eval result file.")
    parser.add_argument("--input", required=True, help="Path to an eval result JSON file under tests/ragas/eval_runs.")
    parser.add_argument("--output", default="", help="Optional output file path. Defaults to overwrite input.")
    parser.add_argument("--model-name", default="", help="Optional evaluator model name. Defaults to MODEL_NAME.")
    parser.add_argument("--base-url", default="", help="Optional evaluator base URL. Defaults to MODEL_BASE_URL.")
    parser.add_argument("--api-key", default="", help="Optional evaluator API key. Defaults to MODEL_API_KEY.")
    parser.add_argument(
        "--embedding-model-path",
        default="",
        help="Optional local embedding model path. Defaults to EMBEDDING_MODEL_PATH from .env.",
    )
    parser.add_argument(
        "--embedding-device",
        default="cpu",
        help="Embedding device for local HuggingFace embeddings. Defaults to cpu.",
    )
    parser.add_argument("--timeout", type=float, default=180.0, help="LLM timeout in seconds.")
    parser.add_argument("--max-workers", type=int, default=1, help="Ragas max workers.")
    parser.add_argument("--max-retries", type=int, default=1, help="Retry count for each failed Ragas job.")
    parser.add_argument(
        "--metrics",
        default="faithfulness,answer_relevancy,context_precision,context_recall",
        help="Comma-separated metrics: faithfulness, answer_relevancy, context_precision, context_recall",
    )
    parser.add_argument("--offset", type=int, default=0, help="Start row position for this run.")
    parser.add_argument("--limit", type=int, default=0, help="Max rows to score after offset. 0 means all.")
    parser.add_argument(
        "--indices",
        default="",
        help="Comma-separated exact item.index values to score, for example: 0,1,2",
    )
    parser.add_argument(
        "--clear-indices",
        default="",
        help="Comma-separated item.index values whose ragas_scores/ragas_error should be cleared before scoring.",
    )
    parser.add_argument(
        "--clear-only",
        action="store_true",
        help="Only clear selected rows and rewrite the output file without running Ragas.",
    )
    parser.add_argument(
        "--only-unscored",
        action="store_true",
        help="Only score rows whose ragas_scores are empty or missing selected metrics.",
    )
    parser.add_argument(
        "--list-items",
        action="store_true",
        help="Print selectable rows and exit without scoring.",
    )
    return parser.parse_args()


def _load_ragas_modules() -> dict[str, Any]:
    try:
        from langchain_openai import ChatOpenAI
        from ragas import EvaluationDataset, evaluate
        from ragas.llms import LangchainLLMWrapper
        from ragas.run_config import RunConfig
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("ragas_not_installed; run: pip install ragas") from exc

    try:
        from ragas.metrics import (
            Faithfulness,
            LLMContextPrecisionWithReference,
            LLMContextRecall,
            ResponseRelevancy,
        )
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(f"ragas_metric_import_failed:{exc}") from exc
    try:
        from langchain_community.embeddings import HuggingFaceEmbeddings as LangchainHuggingFaceEmbeddings
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(f"langchain_embedding_import_failed:{exc}") from exc

    return {
        "ChatOpenAI": ChatOpenAI,
        "EvaluationDataset": EvaluationDataset,
        "evaluate": evaluate,
        "LangchainLLMWrapper": LangchainLLMWrapper,
        "Faithfulness": Faithfulness,
        "ResponseRelevancy": ResponseRelevancy,
        "LLMContextPrecisionWithReference": LLMContextPrecisionWithReference,
        "LLMContextRecall": LLMContextRecall,
        "LangchainHuggingFaceEmbeddings": LangchainHuggingFaceEmbeddings,
        "RunConfig": RunConfig,
    }


def _resolve_env(args: argparse.Namespace) -> tuple[str, str, str]:
    env_path = PROJECT_ROOT / ".env"
    load_dotenv(env_path, override=False)

    api_key = args.api_key or os.getenv("MODEL_API_KEY") or os.getenv("OPENAI_API_KEY") or ""
    base_url = args.base_url or os.getenv("MODEL_BASE_URL") or os.getenv("OPENAI_BASE_URL") or ""
    model_name = args.model_name or os.getenv("MODEL_NAME") or os.getenv("OPENAI_MODEL") or ""

    if not api_key or not model_name:
        raise RuntimeError("missing_model_env; need MODEL_API_KEY and MODEL_NAME or explicit args")
    return api_key, base_url, model_name


def _apply_openai_env(api_key: str, base_url: str) -> None:
    os.environ["OPENAI_API_KEY"] = api_key
    if base_url:
        os.environ["OPENAI_BASE_URL"] = base_url


def _resolve_embedding_path(args: argparse.Namespace) -> str:
    raw = args.embedding_model_path or os.getenv("EMBEDDING_MODEL_PATH") or ""
    if not raw:
        return ""
    path = Path(raw)
    if not path.is_absolute():
        path = (PROJECT_ROOT / path).resolve()
    return str(path)


def _sanitize_contexts(value: Any) -> list[str]:
    rows = value if isinstance(value, list) else []
    result: list[str] = []
    for item in rows:
        text = str(item or "").strip()
        if text:
            result.append(text)
    return result


def _safe_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number):
        return None
    return round(number, 4)


def _parse_metric_names(raw: str) -> list[str]:
    names: list[str] = []
    for piece in str(raw or "").split(","):
        key = METRIC_ALIASES.get(piece.strip().lower())
        if not key:
            continue
        if key == "factual_correctness":
            raise RuntimeError("unsupported_metric:factual_correctness; use faithfulness,answer_relevancy,context_precision,context_recall")
        if key not in names:
            names.append(key)
    if not names:
        raise RuntimeError("invalid_metrics; use faithfulness,answer_relevancy,context_precision,context_recall")
    return names


def _parse_indices(raw: str) -> set[int]:
    values: set[int] = set()
    for piece in str(raw or "").split(","):
        text = piece.strip()
        if not text:
            continue
        try:
            values.add(int(text))
        except ValueError as exc:
            raise RuntimeError(f"invalid_index:{text}") from exc
    return values


def _has_selected_scores(row: dict[str, Any], metric_names: list[str]) -> bool:
    scores = row.get("ragas_scores")
    if not isinstance(scores, dict):
        return False
    return all(_safe_number(scores.get(metric_name)) is not None for metric_name in metric_names)


def _build_metrics(modules: dict[str, Any], metric_names: list[str]) -> list[Any]:
    metrics: list[Any] = []
    for name in metric_names:
        class_name = METRIC_SPECS[name]["class_name"]
        if name == "answer_relevancy":
            metrics.append(modules[class_name](strictness=1))
        else:
            metrics.append(modules[class_name]())
    return metrics


def _build_embeddings(modules: dict[str, Any], embedding_model_path: str, embedding_device: str) -> Any | None:
    if not embedding_model_path:
        return None
    path = Path(embedding_model_path)
    if not path.exists():
        raise FileNotFoundError(f"embedding_model_not_found:{path}")
    return modules["LangchainHuggingFaceEmbeddings"](
        model_name=str(path),
        model_kwargs={"device": embedding_device or "cpu"},
        encode_kwargs={"normalize_embeddings": True},
        show_progress=False,
    )


def _recompute_summary(items: list[Any], metric_names: list[str] | None = None) -> tuple[dict[str, float | None], list[str]]:
    discovered: list[str] = []
    if metric_names:
        discovered.extend(metric_names)
    for row in items:
        if not isinstance(row, dict):
            continue
        scores = row.get("ragas_scores")
        if not isinstance(scores, dict):
            continue
        for key in scores.keys():
            text = str(key or "").strip()
            if text and text not in discovered:
                discovered.append(text)

    summary: dict[str, float | None] = {}
    for metric_name in discovered:
        values: list[float] = []
        for row in items:
            if not isinstance(row, dict):
                continue
            scores = row.get("ragas_scores")
            if not isinstance(scores, dict):
                continue
            value = _safe_number(scores.get(metric_name))
            if value is not None:
                values.append(value)
        summary[metric_name] = round(sum(values) / len(values), 4) if values else None
    return summary, discovered


def _clear_rows(items: list[Any], clear_indices: set[int]) -> int:
    cleared = 0
    for position, row in enumerate(items):
        if not isinstance(row, dict):
            continue
        row_index = int(row.get("index") or position)
        if row_index not in clear_indices:
            continue
        row["ragas_scores"] = {}
        row.pop("ragas_error", None)
        cleared += 1
    return cleared


def _save_payload(payload: dict[str, Any], output_path: Path, metric_names: list[str] | None = None) -> tuple[dict[str, float | None], list[str]]:
    summary, metrics = _recompute_summary(payload.get("items", []), metric_names)
    payload["ragas_summary"] = summary
    payload["ragas_metrics"] = metrics
    payload["ragas_scored_at"] = datetime.now().isoformat(timespec="seconds")
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary, metrics


def main() -> None:
    args = parse_args()

    input_path = Path(args.input)
    if not input_path.is_absolute():
        input_path = (PROJECT_ROOT / input_path).resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"input_not_found:{input_path}")

    output_path = Path(args.output).resolve() if args.output else input_path
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    items = payload.get("items") if isinstance(payload, dict) and isinstance(payload.get("items"), list) else []
    if not items:
        raise RuntimeError("no_items_in_result_file")

    metric_names = _parse_metric_names(args.metrics)
    requested_indices = _parse_indices(args.indices)
    clear_indices = _parse_indices(args.clear_indices)

    if args.list_items:
        for position, row in enumerate(items):
            if not isinstance(row, dict):
                continue
            item_index = int(row.get("index") or position)
            question = str(row.get("question") or "").strip().replace("\n", " ")
            print(
                json.dumps(
                    {
                        "position": position,
                        "index": item_index,
                        "has_answer": bool(str(row.get("answer") or "").strip()),
                        "has_reference": bool(str(row.get("ground_truth") or "").strip()),
                        "has_selected_scores": _has_selected_scores(row, metric_names),
                        "question": question[:120],
                    },
                    ensure_ascii=False,
                )
            )
        return

    if output_path != input_path:
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        items = payload.get("items") if isinstance(payload.get("items"), list) else []

    cleared_count = 0
    if clear_indices:
        cleared_count = _clear_rows(items, clear_indices)
        payload["items"] = items
        summary, metrics = _save_payload(payload, output_path, metric_names)
        print(
            json.dumps(
                {
                    "cleared_indices": sorted(clear_indices),
                    "cleared_count": cleared_count,
                    "ragas_summary": summary,
                    "ragas_metrics": metrics,
                },
                ensure_ascii=False,
            )
        )
        if args.clear_only:
            print(f"ragas_saved_to={output_path}")
            return
    elif args.clear_only:
        raise RuntimeError("clear_only_requires_clear_indices")

    modules = _load_ragas_modules()
    api_key, base_url, model_name = _resolve_env(args)
    _apply_openai_env(api_key, base_url)
    embedding_model_path = _resolve_embedding_path(args)

    dataset_rows = []
    valid_indices = []
    selected_count = 0
    skipped_count = 0
    for position, row in enumerate(items):
        if not isinstance(row, dict):
            continue
        item_index = int(row.get("index") or position)
        if requested_indices and item_index not in requested_indices:
            skipped_count += 1
            continue
        if position < max(0, int(args.offset)):
            skipped_count += 1
            continue
        if int(args.limit) > 0 and selected_count >= int(args.limit):
            skipped_count += 1
            continue
        if args.only_unscored and _has_selected_scores(row, metric_names):
            skipped_count += 1
            continue
        question = str(row.get("question") or "").strip()
        answer = str(row.get("answer") or "").strip()
        reference = str(row.get("ground_truth") or "").strip()
        retrieved_contexts = _sanitize_contexts(row.get("retrieved_contexts"))
        if not question or not answer or not reference:
            row["ragas_scores"] = {}
            row["ragas_error"] = "missing_question_or_answer_or_reference"
            continue
        dataset_rows.append(
            {
                "user_input": question,
                "retrieved_contexts": retrieved_contexts,
                "response": answer,
                "reference": reference,
            }
        )
        valid_indices.append(item_index)
        selected_count += 1

    if not dataset_rows:
        raise RuntimeError("no_valid_rows_for_ragas")

    ChatOpenAI = modules["ChatOpenAI"]
    EvaluationDataset = modules["EvaluationDataset"]
    evaluate = modules["evaluate"]
    LangchainLLMWrapper = modules["LangchainLLMWrapper"]
    RunConfig = modules["RunConfig"]

    chat_llm = ChatOpenAI(
        model=model_name,
        api_key=api_key,
        base_url=base_url or None,
        temperature=0,
        timeout=args.timeout,
    )
    evaluator_llm = LangchainLLMWrapper(chat_llm)
    metrics = _build_metrics(modules, metric_names)
    embeddings = _build_embeddings(modules, embedding_model_path, args.embedding_device)

    evaluation_dataset = EvaluationDataset.from_list(dataset_rows)
    print(
        json.dumps(
            {
                "rows_selected": len(dataset_rows),
                "rows_skipped": skipped_count,
                "metrics": metric_names,
                "timeout": args.timeout,
                "max_workers": max(1, int(args.max_workers)),
                "max_retries": max(0, int(args.max_retries)),
                "embedding_model_path": embedding_model_path,
                "embedding_device": args.embedding_device,
            },
            ensure_ascii=False,
        )
    )
    result = evaluate(
        dataset=evaluation_dataset,
        metrics=metrics,
        llm=evaluator_llm,
        embeddings=embeddings,
        run_config=RunConfig(
            timeout=args.timeout,
            max_workers=max(1, int(args.max_workers)),
            max_retries=max(0, int(args.max_retries)),
        ),
        raise_exceptions=False,
    )

    df = result.to_pandas()
    per_row_scores: dict[int, dict[str, float | None]] = {}
    for position, item_index in enumerate(valid_indices):
        row_scores: dict[str, float | None] = {}
        for metric_name in metric_names:
            actual_name = METRIC_SPECS[metric_name]["actual_name"]
            if actual_name in df.columns:
                row_scores[metric_name] = _safe_number(df.iloc[position][actual_name])
            else:
                row_scores[metric_name] = None
        per_row_scores[item_index] = row_scores

    for position, row in enumerate(items):
        if not isinstance(row, dict):
            continue
        row_index = int(row.get("index") or position)
        if row_index in per_row_scores:
            existing_scores = row.get("ragas_scores") if isinstance(row.get("ragas_scores"), dict) else {}
            row["ragas_scores"] = {**existing_scores, **per_row_scores[row_index]}
            row.pop("ragas_error", None)
        elif "ragas_scores" not in row:
            row["ragas_scores"] = {}

    payload["items"] = items
    payload["ragas_model_name"] = model_name
    payload["ragas_embedding_model_path"] = embedding_model_path
    summary, metrics = _save_payload(payload, output_path)
    print(f"ragas_saved_to={output_path}")
    print(f"ragas_summary={json.dumps(summary, ensure_ascii=False)}")
    print(f"ragas_metrics={json.dumps(metrics, ensure_ascii=False)}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        raise
