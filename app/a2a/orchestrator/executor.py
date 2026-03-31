"""主代理执行器。"""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from app.a2a.client.subagent_client import SubAgentClientFactory
from app.a2a.orchestrator.graph import compile_orchestrator_graph, run_orchestrator_graph
from app.a2a.orchestrator.state import OrchestratorState


class OrchestratorExecutor:
    """执行主代理 LangGraph。"""

    def __init__(self, subagent_client: SubAgentClientFactory):
        """初始化 OrchestratorExecutor 实例。
        
        Args:
            subagent_client: 参数。
        """
        self.subagent_client = subagent_client
        self.graph = compile_orchestrator_graph(subagent_client)

    async def run(
        self,
        *,
        input_text: str,
        session_id: str = "",
        user_id: str = "",
        trace_id: str = "",
        strategy: str = "iterative",
        max_turns: int = 2,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """执行 run。
        
        Args:
            input_text: 参数。
            session_id: 参数。
            user_id: 参数。
            trace_id: 参数。
            strategy: 参数。
            max_turns: 参数。
            progress_callback: 参数。
        
        Returns:
            执行结果。
        """
        state: OrchestratorState = {
            "input_text": str(input_text or "").strip(),
            "session_id": str(session_id or "").strip(),
            "user_id": str(user_id or "").strip(),
            "trace_id": str(trace_id or "").strip(),
            "strategy": str(strategy or "iterative").strip() or "iterative",
            "max_turns": max(1, int(max_turns or 2)),
            "retrieval_round": 0,
            "pending_subtasks": [],
            "tool_observations": [],
            "reflection_events": [],
            "processing_steps": [],
            "evidence_bundle": {
                "kg_evidence": [],
                "rag_evidence": [],
                "citations": [],
            },
            # 每个主代理请求独立串行闸门，仅限制本请求对子代理的并发度。
            "serial_guard": asyncio.Semaphore(1),
        }

        if progress_callback is not None:
            progress_callback(
                {
                    "event": "orchestrator_start",
                    "detail": "主代理开始分解问题",
                    "tool_name": "orchestrator",
                }
            )

        output = await run_orchestrator_graph(self.graph, state)

        final_obj = output.get("final_output")
        final_output = final_obj if isinstance(final_obj, dict) else {}
        answer = str(final_output.get("answer") or "").strip()
        confidence = float(final_output.get("confidence") or 0.0)
        evidence_summary_obj = final_output.get("evidence_summary")
        evidence_summary = evidence_summary_obj if isinstance(evidence_summary_obj, dict) else {}

        if progress_callback is not None:
            progress_callback(
                {
                    "event": "orchestrator_done",
                    "detail": "主代理完成终答",
                    "tool_name": "orchestrator",
                }
            )

        return {
            "output": answer,
            "output_type": "orchestrator_final",
            "confidence": confidence,
            "tool_observations": output.get("tool_observations") if isinstance(output.get("tool_observations"), list) else [],
            "reflection_events": output.get("reflection_events") if isinstance(output.get("reflection_events"), list) else [],
            "processing_steps": output.get("processing_steps") if isinstance(output.get("processing_steps"), list) else [],
            "telemetry": {
                "retrieval_round": int(output.get("retrieval_round") or 0),
                "strategy": str(output.get("strategy") or strategy),
            },
            "evidence_summary": evidence_summary,
        }
