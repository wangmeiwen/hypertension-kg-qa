"""主代理迭代执行器（兼容层）。"""

from __future__ import annotations

from typing import Any, Callable, Optional

from app.a2a.agent.graph_subagent import GraphSubAgent
from app.a2a.agent.rag_subagent import RagSubAgent
from app.a2a.client.subagent_client import SubAgentClientFactory
from app.a2a.orchestrator.executor import OrchestratorExecutor
from app.mcp.tools import get_mcp_server


class IterativeAgent:
    """主代理：问题分解 + 串行调度子代理 + 补检索决策。"""

    def __init__(
        self,
        mcp_server: Any | None = None,
        graph_subagent: GraphSubAgent | None = None,
        rag_subagent: RagSubAgent | None = None,
    ) -> None:
        """初始化 IterativeAgent 实例。
        
        Args:
            mcp_server: 参数。
            graph_subagent: 参数。
            rag_subagent: 参数。
        """
        mcp = mcp_server if mcp_server is not None else get_mcp_server()
        self.graph_subagent = graph_subagent if graph_subagent is not None else GraphSubAgent(mcp)
        self.rag_subagent = rag_subagent if rag_subagent is not None else RagSubAgent(mcp)
        self.subagent_client = SubAgentClientFactory(self.graph_subagent, self.rag_subagent)
        self.executor = OrchestratorExecutor(self.subagent_client)

    async def run(
        self,
        *,
        input_text: str,
        session_id: str = "",
        user_id: str = "",
        strategy: str = "iterative",
        max_turns: Optional[int] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
        progress_callback: Optional[Callable[[dict[str, Any]], None]] = None,
    ) -> dict[str, Any]:
        """执行 run。
        
        Args:
            input_text: 参数。
            session_id: 参数。
            user_id: 参数。
            strategy: 参数。
            max_turns: 参数。
            cancel_check: 参数。
            progress_callback: 参数。
        
        Returns:
            执行结果。
        """
        if cancel_check is not None and bool(cancel_check()):
            return {
                "output": "任务已取消",
                "output_type": "cancelled",
                "confidence": 0.0,
                "tool_observations": [],
                "reflection_events": [],
                "processing_steps": ["任务在执行前被取消"],
                "telemetry": {"cancelled": True},
                "evidence_summary": {
                    "evidence_count": 0,
                    "conflict_count": 0,
                    "hop_coverage": 0.0,
                    "uncovered_points": ["任务取消"],
                    "high_risk": False,
                    "high_risk_tokens": [],
                    "disambiguation_ambiguous_count": 0,
                    "community_count": 0,
                    "top_community_score": 0.0,
                    "force_supplement_required": False,
                },
            }

        turns = 2
        if isinstance(max_turns, int) and max_turns > 0:
            turns = max_turns

        return await self.executor.run(
            input_text=str(input_text or "").strip(),
            session_id=str(session_id or "").strip(),
            user_id=str(user_id or "").strip(),
            trace_id="",
            strategy=str(strategy or "iterative").strip() or "iterative",
            max_turns=turns,
            progress_callback=progress_callback,
        )
