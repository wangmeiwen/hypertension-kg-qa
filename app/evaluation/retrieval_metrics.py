"""检索消融的确定性指标计算。"""

from __future__ import annotations

import math
import unicodedata
from collections import defaultdict
from typing import Any, TypedDict

RETRIEVAL_ABLATION_MODES = (
    "kg",
    "dense",
    "sparse",
    "hybrid",
    "rerank",
    "agent",
)


class RetrievalGoldEvidence(TypedDict, total=False):
    """单条金标证据，至少包含 evidence_id 或 text。"""

    evidence_id: str
    text: str


class RetrievalEvaluationCase(TypedDict):
    """固定问题及其金标证据。"""

    case_id: str
    question: str
    relevant_evidence: list[RetrievalGoldEvidence]


class RetrievalHit(TypedDict, total=False):
    """某检索模式返回的有序证据。"""

    evidence_id: str
    text: str
    score: float


class RetrievalRunItem(TypedDict):
    """一个模式在一道题上的运行记录。"""

    case_id: str
    mode: str
    status: str
    hits: list[RetrievalHit]
    latency_ms: float
    token_count: int
    error: str


def _normalized_text(value: Any) -> str:
    if value is None:
        return ""
    normalized = "".join(
        unicodedata.normalize("NFKC", str(value)).casefold().strip().split()
    )
    return "".join(character for character in normalized if character.isalnum())


def _gold_matches_hit(gold: RetrievalGoldEvidence, hit: RetrievalHit) -> bool:
    gold_id = _normalized_text(gold.get("evidence_id"))
    hit_id = _normalized_text(hit.get("evidence_id"))
    if gold_id and hit_id and gold_id == hit_id:
        return True
    gold_text = _normalized_text(gold.get("text"))
    hit_text = _normalized_text(hit.get("text"))
    if len(gold_text) < 12 or len(hit_text) < 12:
        return False
    return gold_text in hit_text or hit_text in gold_text


def _safe_latency(value: Any) -> float:
    try:
        latency = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, latency) if math.isfinite(latency) else 0.0


def _safe_token_count(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def evaluate_retrieval_item(
    evaluation_case: RetrievalEvaluationCase,
    run_item: RetrievalRunItem,
    *,
    k: int,
) -> dict[str, Any]:
    """计算单题 Recall@K、MRR、nDCG 和证据命中。"""
    if k < 1:
        raise ValueError("retrieval_metric_k_must_be_positive")
    gold = evaluation_case["relevant_evidence"]
    if not gold:
        raise ValueError(f"case_has_no_relevant_evidence:{evaluation_case['case_id']}")
    failed = run_item["status"] != "succeeded"
    top_hits = [] if failed else list(run_item["hits"][:k])
    matched_gold: set[int] = set()
    relevant_ranks: list[int] = []
    for rank, hit in enumerate(top_hits, start=1):
        matched_new_gold = False
        for gold_index, gold_item in enumerate(gold):
            if gold_index not in matched_gold and _gold_matches_hit(gold_item, hit):
                matched_gold.add(gold_index)
                matched_new_gold = True
        if matched_new_gold:
            relevant_ranks.append(rank)

    recall = len(matched_gold) / len(gold)
    reciprocal_rank = 1 / relevant_ranks[0] if relevant_ranks else 0.0
    dcg = sum(1 / math.log2(rank + 1) for rank in relevant_ranks)
    ideal_hits = min(len(gold), k)
    ideal_dcg = sum(1 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
    ndcg = dcg / ideal_dcg if ideal_dcg else 0.0
    return {
        "case_id": evaluation_case["case_id"],
        "mode": run_item["mode"],
        f"recall@{k}": round(recall, 6),
        "mrr": round(reciprocal_rank, 6),
        f"ndcg@{k}": round(ndcg, 6),
        "evidence_hit": bool(relevant_ranks),
        "matched_gold_count": len(matched_gold),
        "gold_count": len(gold),
        "returned_hit_count": len(top_hits),
        "latency_ms": _safe_latency(run_item["latency_ms"]),
        "token_count": _safe_token_count(run_item["token_count"]),
        "failed": failed,
        "error": run_item["error"],
    }


def _percentile_95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return ordered[index]


def evaluate_retrieval_ablation(
    cases: list[RetrievalEvaluationCase],
    run_items: list[RetrievalRunItem],
    *,
    k: int,
) -> dict[str, Any]:
    """按模式汇总消融结果，不对缺失 case 静默补零。"""
    if not cases:
        raise ValueError("evaluation_cases_must_not_be_empty")
    cases_by_id = {item["case_id"]: item for item in cases}
    if len(cases_by_id) != len(cases):
        raise ValueError("duplicate_evaluation_case_id")
    grouped_runs: dict[str, dict[str, RetrievalRunItem]] = defaultdict(dict)
    for run_item in run_items:
        mode = run_item["mode"]
        if mode not in RETRIEVAL_ABLATION_MODES:
            raise ValueError(f"unsupported_retrieval_mode:{mode}")
        case_id = run_item["case_id"]
        if case_id not in cases_by_id:
            raise ValueError(f"run_references_unknown_case:{case_id}")
        if case_id in grouped_runs[mode]:
            raise ValueError(f"duplicate_run_item:{mode}:{case_id}")
        grouped_runs[mode][case_id] = run_item

    mode_reports: dict[str, Any] = {}
    case_reports: list[dict[str, Any]] = []
    for mode, mode_runs in sorted(grouped_runs.items()):
        missing_case_ids = [
            case_id for case_id in cases_by_id if case_id not in mode_runs
        ]
        if missing_case_ids:
            raise ValueError(f"mode_missing_cases:{mode}:{','.join(missing_case_ids)}")
        reports = [
            evaluate_retrieval_item(cases_by_id[case_id], mode_runs[case_id], k=k)
            for case_id in cases_by_id
        ]
        case_reports.extend(reports)
        latencies = [float(report["latency_ms"]) for report in reports]
        total_tokens = sum(int(report["token_count"]) for report in reports)
        mode_reports[mode] = {
            "case_count": len(reports),
            f"recall@{k}": round(
                sum(float(report[f"recall@{k}"]) for report in reports) / len(reports),
                6,
            ),
            "mrr": round(
                sum(float(report["mrr"]) for report in reports) / len(reports),
                6,
            ),
            f"ndcg@{k}": round(
                sum(float(report[f"ndcg@{k}"]) for report in reports) / len(reports),
                6,
            ),
            "evidence_hit_rate": round(
                sum(1 for report in reports if report["evidence_hit"]) / len(reports),
                6,
            ),
            "failure_rate": round(
                sum(1 for report in reports if report["failed"]) / len(reports),
                6,
            ),
            "latency_ms_avg": round(sum(latencies) / len(latencies), 3),
            "latency_ms_p95": round(_percentile_95(latencies), 3),
            "token_count_total": total_tokens,
            "token_count_avg": round(total_tokens / len(reports), 3),
        }
    return {
        "k": k,
        "case_count": len(cases),
        "modes": mode_reports,
        "missing_modes": [
            mode for mode in RETRIEVAL_ABLATION_MODES if mode not in mode_reports
        ],
        "cases": case_reports,
        "matching": "evidence_id_exact_or_normalized_text_containment",
    }
