"""通过真实 /agent/run 接口执行多跳问题集并输出可复核结果。"""

import argparse
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

import httpx
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.evaluation.agent_metrics import (  # noqa: E402
    evaluate_agent_case,
    summarize_agent_results,
)
from app.evaluation.agent_canary import load_agent_canary  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("questions", type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-turns", type=int, default=4)
    parser.add_argument("--min-passed", type=int, default=4)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    load_dotenv(PROJECT_ROOT / ".env", override=True)
    raw_questions = json.loads(args.questions.read_text(encoding="utf-8"))
    if isinstance(raw_questions, dict):
        questions = load_agent_canary(args.questions).evaluation_cases()
    elif isinstance(raw_questions, list):
        questions = raw_questions
    else:
        raise ValueError("问题集必须是 canary 对象或 JSON 数组")

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
            results.append(evaluate_agent_case(case, response.json()))

    report = summarize_agent_results(results)
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
