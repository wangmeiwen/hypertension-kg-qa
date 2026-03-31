import asyncio
import unittest

from app.a2a.models import SubAgentResult
from app.a2a.orchestrator.graph import compile_orchestrator_graph, run_orchestrator_graph


class _DummyClient:
    def __init__(self) -> None:
        self.seen_questions: list[str] = []

    async def send_task(self, task, serial_guard=None):
        _ = serial_guard
        self.seen_questions.append(str(task.question or ""))
        return SubAgentResult(
            subtask_id=task.subtask_id,
            target_agent=task.target_agent,
            question=task.question,
            tool_results=[{"tool_name": task.tool_name, "ok": True, "result": {}}],
            evidence=[{"hit": "dummy_evidence"}],
            no_signal=False,
            reason="ok",
            telemetry={},
        )


class TestOrchestratorGraphOutput(unittest.TestCase):
    def test_run_orchestrator_graph_returns_final_output(self) -> None:
        client = _DummyClient()

        async def _run() -> dict:
            graph = compile_orchestrator_graph(client)
            state = {
                "input_text": "测试问题",
                "session_id": "s1",
                "user_id": "u1",
                "trace_id": "t1",
                "strategy": "iterative",
                "max_turns": 2,
                "retrieval_round": 0,
                "pending_subtasks": [],
                "tool_observations": [],
                "reflection_events": [],
                "processing_steps": [],
                "evidence_bundle": {"kg_evidence": [], "rag_evidence": [], "citations": []},
                "serial_guard": asyncio.Semaphore(1),
            }
            return await run_orchestrator_graph(graph, state)

        output = asyncio.run(_run())
        final_output = output.get("final_output")
        self.assertIsInstance(final_output, dict)
        self.assertTrue(bool(str((final_output or {}).get("answer") or "").strip()))
        self.assertGreaterEqual(int(output.get("retrieval_round") or 0), 1)
        self.assertTrue(client.seen_questions)
        self.assertEqual(client.seen_questions[0], "测试问题")


if __name__ == "__main__":
    unittest.main()
