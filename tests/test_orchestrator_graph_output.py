"""test_orchestrator_graph_output 模块。"""

import asyncio
import unittest

from app.a2a.models import SubAgentResult
from app.a2a.orchestrator.graph import compile_orchestrator_graph, run_orchestrator_graph


class _DummyClient:
    def __init__(self) -> None:
        """初始化测试用子代理客户端桩对象。

        该桩对象会记录收到的问题文本，便于断言编排层是否正确透传输入。

        Returns:
            None: 无返回值。
        """
        self.seen_questions: list[str] = []

    async def send_task(self, task, serial_guard=None):
        """模拟子代理任务执行并返回固定成功结果。

        Args:
            task: 编排器下发的子任务对象。
            serial_guard: 串行控制信号量；测试中不使用，仅保持接口兼容。

        Returns:
            SubAgentResult: 包含虚拟工具结果与证据的固定返回。
        """
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
        """编排图执行后应产出最终答案与中间状态。

        核心断言：
        1) `final_output` 为字典且 answer 非空；
        2) 检索轮次大于等于 1；
        3) 子代理确实收到原始问题。

        Returns:
            None: 无返回值。
        """
        client = _DummyClient()

        async def _run() -> dict:
            """构建并运行一次编排图。

            Returns:
                dict: 编排图执行完成后的状态字典。
            """
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
