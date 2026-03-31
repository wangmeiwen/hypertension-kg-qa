import time
import unittest
from typing import Any, Dict, Optional

from app.a2a.service import A2AService


def _build_message(text: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "role": "user",
        "parts": [{"kind": "text", "text": text}],
        "metadata": metadata or {},
    }


class TestA2AContract(unittest.TestCase):
    def _new_service(self) -> A2AService:
        def run_query(
            input_text: str,
            context_id: str = "",
            user_id: str = "",
            metadata: Optional[Dict[str, Any]] = None,
            cancel_check=None,
            progress_callback=None,
        ) -> Dict[str, Any]:
            _ = context_id
            _ = user_id
            _ = metadata
            text = str(input_text or "")
            if text == "fail":
                raise RuntimeError("boom")
            if text == "slow":
                for idx in range(20):
                    if cancel_check is not None and bool(cancel_check()):
                        break
                    if progress_callback is not None:
                        progress_callback({"event": "tick", "detail": f"tick-{idx + 1}"})
                    time.sleep(0.01)
                return {"output": "slow-done", "output_type": "slow", "confidence": 0.5}
            if progress_callback is not None:
                progress_callback({"event": "start", "detail": "running"})
            return {
                "output": f"echo:{text}",
                "output_type": "echo",
                "confidence": 0.88,
                "tool_observations": [{"source": "unit-test"}],
                "telemetry": {"unit": True},
            }

        service = A2AService(
            run_query=run_query,
            agent_name="Unit Test Agent",
            agent_description="contract",
            agent_url="/a2a/test/rpc",
            events_base_path="/a2a/test/tasks",
            max_workers=2,
        )
        self.addCleanup(service.shutdown)
        return service

    @staticmethod
    def _wait_terminal(service: A2AService, task_id: str, timeout_s: float = 3.0) -> Dict[str, Any]:
        started = time.time()
        while True:
            task = service.get_task(task_id)
            if not task:
                raise AssertionError("task_not_found")
            state = str(((task.get("status") or {}).get("state") or "")).strip().lower()
            if state in {"completed", "failed", "canceled"}:
                return task
            if time.time() - started > timeout_s:
                raise AssertionError("wait_terminal_timeout")
            time.sleep(0.01)

    def test_message_stream_and_resubscribe_url(self) -> None:
        service = self._new_service()
        stream_resp = service.handle_rpc(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "message/stream",
                "params": {"message": _build_message("hello")},
            }
        )
        result = stream_resp.get("result") or {}
        task = result.get("task") or {}
        stream = result.get("stream") or {}
        task_id = str(task.get("id") or "")

        self.assertTrue(bool(task_id))
        self.assertEqual(stream.get("url"), f"/a2a/test/tasks/{task_id}/events?cursor=0")

        resub_resp = service.handle_rpc(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tasks/resubscribe",
                "params": {"taskId": task_id, "cursor": 7},
            }
        )
        resub_stream = ((resub_resp.get("result") or {}).get("stream") or {})
        self.assertEqual(resub_stream.get("url"), f"/a2a/test/tasks/{task_id}/events?cursor=7")
        self.assertEqual(resub_stream.get("cursor"), 7)

    def test_task_completion_contains_artifacts(self) -> None:
        service = self._new_service()
        send_resp = service.handle_rpc(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "message/send",
                "params": {
                    "message": _build_message(
                        "done",
                        metadata={"session_id": "s1", "user_id": "u1"},
                    )
                },
            }
        )
        task_id = str((((send_resp.get("result") or {}).get("task") or {}).get("id")) or "")
        final_task = self._wait_terminal(service, task_id)

        self.assertEqual(((final_task.get("status") or {}).get("state")), "completed")
        artifact_names = {
            str(artifact.get("name") or "")
            for artifact in (final_task.get("artifacts") or [])
            if isinstance(artifact, dict)
        }
        self.assertIn("final_answer", artifact_names)
        self.assertIn("tool_observations", artifact_names)
        self.assertIn("telemetry", artifact_names)

    def test_task_cancel(self) -> None:
        service = self._new_service()
        send_resp = service.handle_rpc(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "message/send",
                "params": {"message": _build_message("slow")},
            }
        )
        task_id = str((((send_resp.get("result") or {}).get("task") or {}).get("id")) or "")

        cancel_resp = service.handle_rpc(
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tasks/cancel",
                "params": {"taskId": task_id, "reason": "unit_test_cancel"},
            }
        )
        self.assertTrue(bool((cancel_resp.get("result") or {}).get("ok")))

        final_task = self._wait_terminal(service, task_id)
        self.assertEqual(((final_task.get("status") or {}).get("state")), "canceled")


if __name__ == "__main__":
    unittest.main()
