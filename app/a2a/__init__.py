"""a2a 模块。

提供 a2a 相关能力与对外接口。
"""


from app.a2a.cards import all_agent_cards
from app.a2a.service import A2AService

__all__ = ["A2AService", "all_agent_cards"]
