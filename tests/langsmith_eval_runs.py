import argparse
import math
from typing import Any, Iterable, Optional

from langsmith import Client
from langsmith.evaluation import StringEvaluator


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        if "output" in value:
            return str(value.get("output") or "")
        if "answer" in value:
            return str(value.get("answer") or "")
        return str(value)
    return str(value)


def _char_jaccard(a: str, b: str) -> float:
    a_set = set(str(a or "").strip().lower())
    b_set = set(str(b or "").strip().lower())
    if not a_set and not b_set:
        return 1.0
    union = a_set.union(b_set)
    if not union:
        return 0.0
    inter = a_set.intersection(b_set)
    return len(inter) / len(union)


def _contains_low_confidence_phrase(text: str) -> bool:
    t = str(text or "")
    low_conf_phrases = [
        "无法回答",
        "无法确定",
        "不清楚",
        "抱歉",
        "稍后重试",
        "出现异常",
    ]
    return any(phrase in t for phrase in low_conf_phrases)


def _score_without_reference(run_input: str, run_output: str) -> float:
    output = str(run_output or "").strip()
    if not output:
        return 0.0
    base = min(1.0, max(0.2, len(output) / 260.0))
    overlap = _char_jaccard(run_input, run_output)
    penalty = 0.35 if _contains_low_confidence_phrase(output) else 0.0
    score = (0.55 * base) + (0.45 * overlap) - penalty
    return max(0.0, min(1.0, score))


def _label_by_score(score: float) -> str:
    if score >= 0.8:
        return "GOOD"
    if score >= 0.55:
        return "OK"
    if score >= 0.3:
        return "WEAK"
    return "BAD"


def _grader(run_input: str, run_output: str, answer: Optional[str]) -> dict[str, Any]:
    if answer is not None and str(answer).strip():
        score = _char_jaccard(run_output, answer)
    else:
        score = _score_without_reference(run_input, run_output)
    score = max(0.0, min(1.0, float(score)))
    return {
        "score": score,
        "value": _label_by_score(score),
        "comment": f"auto-score={score:.4f}",
    }


def _iter_top_runs(client: Client, project_name: str, limit: int) -> Iterable[Any]:
    return client.list_runs(
        project_name=project_name,
        execution_order=1,
        error=False,
        limit=limit,
    )


def evaluate_project_runs(project_name: str, limit: int) -> dict[str, Any]:
    client = Client()
    evaluator = StringEvaluator(evaluation_name="auto_qa_health", grading_function=_grader)

    evaluated = 0
    score_sum = 0.0

    for run in _iter_top_runs(client, project_name, limit):
        client.evaluate_run(run, evaluator)
        run_input = _to_text(getattr(run, "inputs", {}))
        run_output = _to_text(getattr(run, "outputs", {}))
        score = _score_without_reference(run_input, run_output)
        score_sum += score
        evaluated += 1

    avg_score = (score_sum / evaluated) if evaluated else 0.0
    return {
        "project": project_name,
        "evaluated_runs": evaluated,
        "avg_score": round(avg_score, 4),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate LangSmith runs for this project")
    parser.add_argument("--project", required=True, help="LangSmith project name")
    parser.add_argument("--limit", type=int, default=100, help="Max top-level runs to evaluate")
    args = parser.parse_args()

    result = evaluate_project_runs(project_name=args.project, limit=max(1, int(args.limit)))
    print("langsmith evaluation summary")
    print(f"project={result['project']}")
    print(f"evaluated_runs={result['evaluated_runs']}")
    print(f"avg_score={result['avg_score']}")


if __name__ == "__main__":
    main()

