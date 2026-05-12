"""主控执行器。"""

from __future__ import annotations

from typing import Any, Callable

from app.a2a.client.subagent_client import SubAgentClientFactory
from app.a2a.orchestrator.controller import UnifiedOrchestratorController
from app.core.config import Config


class OrchestratorExecutor:
    """执行新的统一主控决策循环。"""

    def __init__(self, subagent_client: SubAgentClientFactory):
        """初始化执行器并绑定主控控制器与配置。"""
        self.subagent_client = subagent_client
        self.conf = Config()
        self.controller = UnifiedOrchestratorController(subagent_client, conf=self.conf)

    async def aclose(self) -> None:
        """释放执行器持有的控制器资源。"""
        await self.controller.aclose()

    @staticmethod
    def _clamp_positive(value: int, lower: int, upper: int) -> int:
        """将数值限制在正向闭区间内。"""
        return max(int(lower), min(int(value), int(upper)))

    def _resolve_max_turns(self, max_turns: int | None) -> int:
        """解析本轮可用最大轮次，优先用户输入并应用上下界。"""
        default_turns = int(self.conf.A2A_ORCHESTRATOR_MAX_TURNS_DEFAULT)
        max_limit = int(self.conf.A2A_ORCHESTRATOR_MAX_TURNS_MAX)
        if isinstance(max_turns, int) and max_turns > 0:
            return self._clamp_positive(max_turns, 1, max_limit)
        return self._clamp_positive(default_turns, 1, max_limit)

    async def run(
        self,
        *,
        input_text: str,
        session_id: str = "",
        user_id: str = "",
        trace_id: str = "",
        strategy: str = "iterative",
        max_turns: int | None = None,
        image_data: str = "",
        file_id: str = "",
        original_input_text: str = "",
        memory_context: dict[str, Any] | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """执行统一主控循环并返回最终编排结果。

        Args:
            input_text: 当前用户问题。
            session_id: 会话 ID，用于关联记忆和历史检索索引。
            user_id: 用户 ID，当前透传给控制器。
            trace_id: 兼容旧接口的追踪 ID，当前由上层链路处理。
            strategy: 兼容旧接口的策略名，当前统一使用控制器策略。
            max_turns: 主控最大决策轮数。
            image_data: 可选图片数据，透传给最终回答生成阶段。
            file_id: 可选阿里 File API 文件 ID，透传给最终回答生成阶段。
            original_input_text: 兼容旧接口的原始输入文本。
            memory_context: 会话记忆和历史检索索引上下文。
            progress_callback: 过程事件回调。

        Returns:
            包含回答、证据摘要、处理步骤和遥测信息的编排结果。
        """
        _ = trace_id
        _ = strategy
        _ = original_input_text
        safe_max_turns = self._resolve_max_turns(max_turns)
        safe_memory_context = memory_context if isinstance(memory_context, dict) else {}
        return await self.controller.run(
            input_text=str(input_text or "").strip(),
            session_id=str(session_id or "").strip(),
            user_id=str(user_id or "").strip(),
            max_turns=safe_max_turns,
            image_data=str(image_data or "").strip(),
            file_id=str(file_id or "").strip(),
            memory_context=safe_memory_context,
            progress_callback=progress_callback,
        )
