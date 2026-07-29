"""a2a 模块。

导出 A2A 协议服务和 Agent Card 构建入口。
"""

from app.a2a.cards import all_agent_cards
from app.a2a.service import A2AService

__all__ = ["A2AService", "all_agent_cards"]
