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
from app.evaluation.agent_replay import (  # noqa: E402
    AgentReplayDataset,
    AgentReplayManifest,
    AgentReplayRecord,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("questions", type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-turns", type=int, default=4)
    parser.add_argument("--min-passed", type=int, default=4)
    parser.add_argument("--snapshot-manifest", type=Path)
    parser.add_argument("--snapshot-output", type=Path)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    load_dotenv(PROJECT_ROOT / ".env", override=True)
    raw_questions = json.loads(args.questions.read_text(encoding="utf-8"))
    canary = None
    if isinstance(raw_questions, dict):
        canary = load_agent_canary(args.questions)
        questions = canary.evaluation_cases()
    elif isinstance(raw_questions, list):
        questions = raw_questions
    else:
        raise ValueError("问题集必须是 canary 对象或 JSON 数组")

    manifest = None
    if args.snapshot_manifest is not None:
        manifest = AgentReplayManifest.model_validate(
            json.loads(args.snapshot_manifest.read_text(encoding="utf-8"))
        )
        if manifest.replay_kind != "frozen_environment":
            raise ValueError("live_replay_requires_frozen_environment_manifest")
        if canary is None or manifest.canary_dataset_id != canary.dataset_id:
            raise ValueError("live_replay_canary_dataset_mismatch")
    if args.snapshot_output is not None and manifest is None:
        raise ValueError("snapshot_output_requires_manifest")

    results = []
    replay_records: list[AgentReplayRecord] = []
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
            response_payload = response.json()
            results.append(evaluate_agent_case(case, response_payload))
            replay_records.append(
                AgentReplayRecord(
                    case_id=str(case["id"]),
                    response=response_payload,
                )
            )

    report = summarize_agent_results(results)
    report["acceptance_passed"] = (
        bool(results)
        and 1 <= args.min_passed <= len(results)
        and report["passed"] >= args.min_passed
    )
    report["evaluation_layer"] = (
        "live_frozen_environment" if manifest is not None else "live_unfrozen"
    )
    report["domain_validation_complete"] = bool(
        manifest is not None
        and canary is not None
        and canary.review_status == "domain_reviewed"
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    if args.snapshot_output is not None and manifest is not None:
        snapshot = AgentReplayDataset(manifest=manifest, records=replay_records)
        args.snapshot_output.parent.mkdir(parents=True, exist_ok=True)
        args.snapshot_output.write_text(
            json.dumps(snapshot.model_dump(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if not report["acceptance_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
