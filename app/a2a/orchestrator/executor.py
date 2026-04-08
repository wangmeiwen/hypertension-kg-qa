"""主控执行器。"""

from __future__ import annotations

from typing import Any, Callable

from app.a2a.client.subagent_client import SubAgentClientFactory
from app.a2a.orchestrator.controller import UnifiedOrchestratorController
from app.common.config import Config


class OrchestratorExecutor:
    """执行新的统一主控决策循环。"""

    def __init__(self, subagent_client: SubAgentClientFactory):
        self.subagent_client = subagent_client
        self.conf = Config()
        self.controller = UnifiedOrchestratorController(subagent_client, conf=self.conf)

    @staticmethod
    def _clamp_positive(value: int, lower: int, upper: int) -> int:
        return max(int(lower), min(int(value), int(upper)))

    def _resolve_max_turns(self, max_turns: int | None) -> int:
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
        original_input_text: str = "",
        memory_context: dict[str, Any] | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
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
            memory_context=safe_memory_context,
            progress_callback=progress_callback,
        )
