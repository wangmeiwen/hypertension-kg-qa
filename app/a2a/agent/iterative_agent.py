"""主代理迭代执行器（兼容层）。"""

from __future__ import annotations

from typing import Any, Callable, Optional

from app.a2a.agent.graph_subagent import GraphSubAgent
from app.a2a.agent.rag_subagent import RagSubAgent
from app.a2a.client.subagent_client import SubAgentClientFactory
from app.a2a.orchestrator.executor import OrchestratorExecutor
from app.common.config import Config
from app.mcp.tools import get_mcp_server


class IterativeAgent:
    """主代理：问题分解 + 串行调度子代理 + 补检索决策。"""

    def __init__(
        self,
        mcp_server: Any | None = None,
        graph_subagent: GraphSubAgent | None = None,
        rag_subagent: RagSubAgent | None = None,
    ) -> None:
        """初始化主代理兼容层并装配本地图谱/RAG 子代理。

        Args:
            mcp_server: 可选 MCP 服务实例；为空时使用项目默认 MCP 服务。
            graph_subagent: 可选图谱子代理实例，主要用于测试或外部注入。
            rag_subagent: 可选 RAG 子代理实例，主要用于测试或外部注入。
        """
        mcp = mcp_server if mcp_server is not None else get_mcp_server()
        self.conf = Config()
        self.graph_subagent = graph_subagent if graph_subagent is not None else GraphSubAgent(mcp)
        self.rag_subagent = rag_subagent if rag_subagent is not None else RagSubAgent(mcp)
        self.subagent_client = SubAgentClientFactory(self.graph_subagent, self.rag_subagent)
        self.executor = OrchestratorExecutor(self.subagent_client)

    async def aclose(self) -> None:
        """释放主代理执行器持有的客户端资源。"""
        await self.executor.aclose()

    async def run(
        self,
        *,
        input_text: str,
        session_id: str = "",
        user_id: str = "",
        strategy: str = "iterative",
        max_turns: Optional[int] = None,
        image_data: str = "",
        original_input_text: str = "",
        memory_context: dict[str, Any] | None = None,
        cancel_check: Optional[Callable[[], bool]] = None,
        progress_callback: Optional[Callable[[dict[str, Any]], None]] = None,
    ) -> dict[str, Any]:
        """执行一次高血压问答主代理编排流程。

        Args:
            input_text: 当前需要回答的用户问题。
            session_id: 会话 ID，用于关联历史和运行追踪。
            user_id: 用户 ID，用于区分不同用户上下文。
            strategy: 编排策略名称，默认使用 iterative 迭代策略。
            max_turns: 最大编排轮次；为空时读取配置并受上限约束。
            image_data: 可选图片数据，透传给编排执行器。
            original_input_text: 未经过前置改写的原始问题文本。
            memory_context: 会话记忆上下文。
            cancel_check: 外部取消检查回调，返回 True 时提前终止。
            progress_callback: 编排过程事件回调。

        Returns:
            包含最终答案、证据摘要、工具观察和遥测信息的结果字典。
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

        default_turns = int(self.conf.A2A_ORCHESTRATOR_MAX_TURNS_DEFAULT)
        max_turn_limit = int(self.conf.A2A_ORCHESTRATOR_MAX_TURNS_MAX)
        if isinstance(max_turns, int) and max_turns > 0:
            turns = max(1, min(max_turns, max_turn_limit))
        else:
            turns = max(1, min(default_turns, max_turn_limit))

        return await self.executor.run(
            input_text=str(input_text or "").strip(),
            session_id=str(session_id or "").strip(),
            user_id=str(user_id or "").strip(),
            trace_id="",
            strategy=str(strategy or "iterative").strip() or "iterative",
            max_turns=turns,
            image_data=str(image_data or "").strip(),
            original_input_text=str(original_input_text or "").strip(),
            memory_context=memory_context if isinstance(memory_context, dict) else {},
            progress_callback=progress_callback,
        )
