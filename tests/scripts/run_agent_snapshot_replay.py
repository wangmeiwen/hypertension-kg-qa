"""离线重放冻结的 Agent API 响应并输出分层质量报告。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.evaluation.agent_canary import load_agent_canary  # noqa: E402
from app.evaluation.agent_replay import (  # noqa: E402
    evaluate_agent_replay,
    load_agent_replay,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("canary", type=Path)
    parser.add_argument("replay", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--require-all-passed", action="store_true")
    parser.add_argument("--require-domain-reviewed", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    report = evaluate_agent_replay(
        canary=load_agent_canary(args.canary),
        replay=load_agent_replay(args.replay),
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    if args.require_all_passed and not report["all_passed"]:
        raise SystemExit(1)
    if args.require_domain_reviewed and not report["domain_validation_complete"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
