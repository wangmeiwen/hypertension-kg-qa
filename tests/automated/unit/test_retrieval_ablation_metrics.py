from __future__ import annotations

import asyncio
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

import numpy as np
import pytest

from app.evaluation.retrieval_metrics import (
    RetrievalEvaluationCase,
    RetrievalRunItem,
    evaluate_retrieval_ablation,
    evaluate_retrieval_item,
)
from app.rag_core.vector_store import VectorStore


def _case(case_id: str, evidence_ids: list[str]) -> RetrievalEvaluationCase:
    return {
        "case_id": case_id,
        "question": "高血压如何管理？",
        "relevant_evidence": [
            {"evidence_id": evidence_id} for evidence_id in evidence_ids
        ],
    }


def _run(
    case_id: str,
    *,
    mode: str,
    hit_ids: list[str],
    status: str = "succeeded",
    latency_ms: float = 10,
    token_count: int = 0,
) -> RetrievalRunItem:
    return {
        "case_id": case_id,
        "mode": mode,
        "status": status,
        "hits": [
            {"evidence_id": evidence_id, "text": "", "score": 1.0}
            for evidence_id in hit_ids
        ],
        "latency_ms": latency_ms,
        "token_count": token_count,
        "error": "" if status == "succeeded" else "retrieval_failed",
    }


def test_retrieval_item_calculates_rank_metrics_without_duplicate_credit() -> None:
    report = evaluate_retrieval_item(
        _case("q1", ["gold-a", "gold-b"]),
        _run(
            "q1",
            mode="dense",
            hit_ids=["noise", "gold-a", "gold-a"],
        ),
        k=3,
    )

    assert report["recall@3"] == 0.5
    assert report["mrr"] == 0.5
    assert report["ndcg@3"] == pytest.approx(0.386853, abs=1e-6)
    assert report["matched_gold_count"] == 1


def test_retrieval_item_supports_normalized_reference_text_matching() -> None:
    evaluation_case: RetrievalEvaluationCase = {
        "case_id": "q1",
        "question": "家庭血压阈值是多少？",
        "relevant_evidence": [{"text": "家庭血压达到或超过 135/85 mmHg。"}],
    }
    run_item = _run("q1", mode="hybrid", hit_ids=[])
    run_item["hits"] = [
        {
            "evidence_id": "",
            "text": "指南指出：家庭血压达到或超过135/85 mmHg，可以诊断高血压。",
            "score": 0.9,
        }
    ]

    report = evaluate_retrieval_item(evaluation_case, run_item, k=1)

    assert report["recall@1"] == 1.0
    assert report["evidence_hit"] is True


def test_retrieval_ablation_aggregates_quality_cost_and_failures() -> None:
    cases = [_case("q1", ["a"]), _case("q2", ["b"])]
    runs = [
        _run(
            "q1",
            mode="agent",
            hit_ids=["a"],
            latency_ms=100,
            token_count=20,
        ),
        _run(
            "q2",
            mode="agent",
            hit_ids=[],
            status="failed",
            latency_ms=300,
            token_count=10,
        ),
    ]

    report = evaluate_retrieval_ablation(cases, runs, k=2)
    agent = report["modes"]["agent"]

    assert agent["recall@2"] == 0.5
    assert agent["failure_rate"] == 0.5
    assert agent["latency_ms_avg"] == 200
    assert agent["latency_ms_p95"] == 300
    assert agent["token_count_total"] == 30
    assert "dense" in report["missing_modes"]


def test_retrieval_ablation_rejects_incomplete_mode_runs() -> None:
    with pytest.raises(ValueError, match="mode_missing_cases:dense:q2"):
        evaluate_retrieval_ablation(
            [_case("q1", ["a"]), _case("q2", ["b"])],
            [_run("q1", mode="dense", hit_ids=["a"])],
            k=1,
        )


class _EmbeddingStub:
    def __call__(self, _texts: list[str]) -> dict[str, Any]:
        return {
            "dense": np.asarray([[0.1, 0.2]], dtype=np.float32),
            "sparse": np.asarray([[0.5, 0.1]], dtype=np.float32),
        }


def _hit(
    *,
    parent_id: str,
    chunk_id: str,
    text: str,
    distance: float,
) -> dict[str, Any]:
    return {
        "entity": {
            "parent_id": parent_id,
            "chunk_id": chunk_id,
            "text": text,
            "source": "guide.pdf",
        },
        "distance": distance,
    }


def test_vector_store_exposes_dense_sparse_hybrid_and_rerank_modes() -> None:
    store = object.__new__(VectorStore)
    store.collection_name = "eval"
    store.collection_fields = {"dense_vector", "sparse_vector"}
    store.dense_dim = 2
    store.embedding_function = _EmbeddingStub()
    store._resolve_internal_recall_limit = lambda *_args: 3
    store._build_search_output_fields = lambda: ["text", "parent_id", "chunk_id"]
    store._log_candidate_diagnostics = lambda *_args, **_kwargs: None
    dense_hits = [
        _hit(parent_id="a", chunk_id="a1", text="dense-a", distance=0.9),
        _hit(parent_id="b", chunk_id="b1", text="dense-b", distance=0.8),
    ]
    sparse_hits = [
        _hit(parent_id="b", chunk_id="b1", text="sparse-b", distance=0.95),
        _hit(parent_id="c", chunk_id="c1", text="sparse-c", distance=0.7),
    ]

    def search_with_params(
        *, search_kwargs: dict[str, Any], search_params: dict[str, Any]
    ) -> list[list[dict[str, Any]]]:
        del search_params
        return [
            (
                sparse_hits
                if search_kwargs["anns_field"] == "sparse_vector"
                else dense_hits
            )
        ]

    rerank_calls: list[list[str]] = []

    def rerank(_query: str, docs: list[Any], limit: int) -> list[Any]:
        rerank_calls.append([doc.page_content for doc in docs])
        return list(reversed(docs))[:limit]

    store._search_with_params = search_with_params
    store._rerank_sub_chunks = rerank

    dense = asyncio.run(
        store.search_by_mode(
            query="高血压",
            k=2,
            retrieval_mode="dense",
        )
    )
    sparse = asyncio.run(
        store.search_by_mode(
            query="高血压",
            k=2,
            retrieval_mode="sparse",
        )
    )
    hybrid = asyncio.run(
        store.search_by_mode(
            query="高血压",
            k=3,
            retrieval_mode="hybrid",
        )
    )
    reranked = asyncio.run(
        store.search_by_mode(
            query="高血压",
            k=3,
            retrieval_mode="rerank",
        )
    )

    assert [doc.metadata["retrieval_method"] for doc in dense] == [
        "dense",
        "dense",
    ]
    assert [doc.metadata["retrieval_method"] for doc in sparse] == [
        "sparse",
        "sparse",
    ]
    assert hybrid[0].metadata["retrieval_method"] == "dense_sparse_rrf"
    assert [doc.page_content for doc in reranked] == list(
        reversed([doc.page_content for doc in hybrid])
    )
    assert len(rerank_calls) == 1


def test_retrieval_ablation_cli_runs_from_project_root(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[3]
    dataset_path = tmp_path / "dataset.json"
    run_path = tmp_path / "dense.json"
    output_path = tmp_path / "report.json"
    dataset_path.write_text(
        json.dumps(
            {
                "review_status": "pending_domain_review",
                "cases": [
                    {
                        "case_id": "q1",
                        "question": "家庭血压阈值是多少？",
                        "relevant_evidence": [{"evidence_id": "guide:p1:c1"}],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    run_path.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "case_id": "q1",
                        "status": "succeeded",
                        "hits": [
                            {
                                "evidence_id": "guide:p1:c1",
                                "text": "家庭血压达到或超过135/85 mmHg。",
                                "score": 0.9,
                            }
                        ],
                        "latency_ms": 12,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(project_root / "tests" / "ragas" / "run_retrieval_ablation.py"),
            "--dataset",
            str(dataset_path),
            "--run",
            f"dense={run_path}",
            "--output",
            str(output_path),
            "--k",
            "1",
        ],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["dataset_review_status"] == "pending_domain_review"
    assert report["modes"]["dense"]["recall@1"] == 1.0
