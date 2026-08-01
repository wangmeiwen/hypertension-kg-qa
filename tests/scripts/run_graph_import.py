"""通过真实后台接口提交图谱抽取任务并等待终态。"""

import argparse
import json
import os
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--job-id")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=True)
    username = os.environ["ADMIN_USERNAME"]
    password = os.environ["ADMIN_PASSWORD"]

    with httpx.Client(base_url=args.base_url, timeout=60) as client:
        login = client.post(
            "/admin/login",
            json={"username": username, "password": password},
        )
        login.raise_for_status()
        token = str(login.json().get("token") or "")
        if not token:
            raise RuntimeError("管理员登录失败")
        headers = {"Authorization": f"Bearer {token}"}

        job_id = str(args.job_id or "").strip()
        if not job_id:
            with args.pdf.open("rb") as pdf_stream:
                response = client.post(
                    "/admin/graph/jobs/extract-import",
                    headers=headers,
                    files={"file": (args.pdf.name, pdf_stream, "application/pdf")},
                    timeout=120,
                )
            response.raise_for_status()
            submitted = response.json()
            if not bool(submitted.get("ok")):
                raise RuntimeError(str(submitted.get("error") or "图谱任务提交失败"))
            job = submitted.get("data") or {}
            job_id = str(job.get("job_id") or "")
            if not job_id:
                raise RuntimeError("图谱任务响应缺少 job_id")
            print(
                json.dumps(
                    {"event": "submitted", "job_id": job_id},
                    ensure_ascii=False,
                )
            )

        last_snapshot = ""
        while True:
            try:
                status_response = client.get(
                    f"/admin/graph/jobs/{job_id}",
                    headers=headers,
                )
            except httpx.TransportError as exc:
                print(
                    json.dumps(
                        {"event": "poll_retry", "error": str(exc)},
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                time.sleep(args.poll_seconds)
                continue
            status_response.raise_for_status()
            payload = status_response.json()
            job = payload.get("data") or {}
            snapshot = {
                "status": job.get("status"),
                "stage": job.get("stage"),
                "progress_text": job.get("progress_text"),
                "completed_chunks": job.get("completed_chunks"),
                "total_chunks": job.get("total_chunks"),
                "current_chunk_index": job.get("current_chunk_index"),
                "entity_count": (job.get("result") or {}).get("entity_count"),
                "relation_count": (job.get("result") or {}).get("relation_count"),
                "error": job.get("error"),
            }
            snapshot_text = json.dumps(snapshot, ensure_ascii=False)
            if snapshot_text != last_snapshot:
                print(snapshot_text, flush=True)
                last_snapshot = snapshot_text
            if str(job.get("status") or "") in TERMINAL_STATUSES:
                print(json.dumps(job, ensure_ascii=False), flush=True)
                if job.get("status") != "succeeded":
                    raise SystemExit(1)
                return
            time.sleep(args.poll_seconds)


if __name__ == "__main__":
    main()
