"""通过真实 /agent/run 接口执行多跳问题集并输出可复核结果。"""

import argparse
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from dotenv import load_dotenv


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("questions", type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-turns", type=int, default=4)
    parser.add_argument("--min-passed", type=int, default=4)
    return parser.parse_args()


def _relationship_edges(
    observations: list[dict[str, Any]],
) -> tuple[set[tuple[str, str]], int]:
    edges: set[tuple[str, str]] = set()
    max_graph_hop = 0
    for observation in observations:
        evidence = observation.get("evidence")
        evidence_rows = evidence if isinstance(evidence, list) else []
        for item in evidence_rows:
            if not isinstance(item, dict):
                continue
            provenance = item.get("provenance")
            provenance_data = provenance if isinstance(provenance, dict) else {}
            raw = provenance_data.get("raw")
            raw_data = raw if isinstance(raw, dict) else {}
            source = str(raw_data.get("source_name") or "").strip()
            target = str(raw_data.get("target_name") or "").strip()
            if source and target:
                edges.add(tuple(sorted((source, target))))
            hop_value = raw_data.get("graph_hop")
            if isinstance(hop_value, int):
                max_graph_hop = max(max_graph_hop, hop_value)
    return edges, max_graph_hop


def _has_two_edge_path(edges: set[tuple[str, str]]) -> bool:
    degrees: dict[str, int] = {}
    for source, target in edges:
        degrees[source] = degrees.get(source, 0) + 1
        degrees[target] = degrees.get(target, 0) + 1
    return any(value >= 2 for value in degrees.values())


def _evaluate_case(case: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
    answer = str(response.get("answer") or "")
    expected = case.get("expected_terms")
    expected_terms = expected if isinstance(expected, list) else []
    matched_terms = [str(term) for term in expected_terms if str(term) in answer]
    groups = case.get("expected_term_groups")
    expected_groups = groups if isinstance(groups, list) else []
    matched_groups = [
        [str(term) for term in group if str(term) in answer]
        for group in expected_groups
        if isinstance(group, list)
    ]
    observations = response.get("tool_observations")
    observation_rows = observations if isinstance(observations, list) else []
    edges, max_graph_hop = _relationship_edges(observation_rows)
    required_matches = int(case.get("min_expected_matches") or len(expected_terms))
    required_groups = int(case.get("min_expected_groups") or len(expected_groups))
    requires_multihop = bool(case.get("requires_multihop", True))
    has_multihop_evidence = max_graph_hop >= 2 or _has_two_edge_path(edges)
    answer_terms_passed = (
        sum(1 for group in matched_groups if group) >= required_groups
        if expected_groups
        else len(matched_terms) >= required_matches
    )
    passed = (
        bool(response.get("ok"))
        and answer_terms_passed
        and (has_multihop_evidence if requires_multihop else True)
    )
    return {
        "id": case.get("id"),
        "question": case.get("question"),
        "passed": passed,
        "matched_terms": matched_terms,
        "required_matches": required_matches,
        "matched_groups": matched_groups,
        "required_groups": required_groups,
        "relationship_edges": [list(edge) for edge in sorted(edges)],
        "max_graph_hop": max_graph_hop,
        "has_multihop_evidence": has_multihop_evidence,
        "answer": answer,
        "confidence": response.get("confidence"),
        "trace_id": response.get("trace_id"),
        "tool_observations": observation_rows,
        "error": response.get("error"),
    }


def main() -> None:
    args = _parse_args()
    project_root = Path(__file__).resolve().parents[2]
    load_dotenv(project_root / ".env", override=True)
    questions = json.loads(args.questions.read_text(encoding="utf-8"))
    if not isinstance(questions, list):
        raise ValueError("问题集必须是 JSON 数组")

    results = []
    with httpx.Client(base_url=args.base_url, timeout=1800) as client:
        for case in questions:
            if not isinstance(case, dict):
                continue
            response = client.post(
                "/agent/run",
                json={
                    "input": str(case["question"]),
                    "session_id": f"multihop-eval-{uuid4().hex}",
                    "user_id": os.environ["ADMIN_USERNAME"],
                    "strategy": "iterative",
                    "max_turns": args.max_turns,
                },
            )
            response.raise_for_status()
            results.append(_evaluate_case(case, response.json()))

    report = {
        "total": len(results),
        "passed": sum(1 for item in results if item["passed"]),
        "all_passed": bool(results) and all(item["passed"] for item in results),
        "results": results,
    }
    report["acceptance_passed"] = (
        bool(results)
        and 1 <= args.min_passed <= len(results)
        and report["passed"] >= args.min_passed
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    if not report["acceptance_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
