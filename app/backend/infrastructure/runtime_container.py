"""后端运行时容器定义。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

from app.backend.config import BackendConfig
from app.common.request_rate_limiter import InMemorySlidingWindowRateLimiter

if TYPE_CHECKING:
    from app.a2a import A2AService
    from app.a2a.agent import GraphSubAgent, RagSubAgent
    from app.backend.services.session_memory_service import SessionMemoryService


@dataclass
class BackendRuntimeContainer:
    """集中保存后端运行时单例与状态。"""

    config: BackendConfig
    mysql_initialized: Optional[bool] = None
    mcp_server: Any = None
    a2a_service: Optional["A2AService"] = None
    a2a_graph_service: Optional["A2AService"] = None
    a2a_retrieval_service: Optional["A2AService"] = None
    graph_subagent: Optional["GraphSubAgent"] = None
    rag_subagent: Optional["RagSubAgent"] = None
    session_memory_service: Optional["SessionMemoryService"] = None
    rate_limiter: Optional[InMemorySlidingWindowRateLimiter] = None
