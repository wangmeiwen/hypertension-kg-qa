"""A2A 工具网关权限测试。"""

from __future__ import annotations

from typing import Any

import pytest

from app.a2a.tool_gateway import MCPToolGateway


class _FakeMcpServer:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def ainvoke_batch(self, calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self.calls.extend(calls)
        return [{"ok": True, "result": {"matched": True}, "meta": {}} for _ in calls]


def test_orchestrator_only_allows_history_read() -> None:
    gateway = MCPToolGateway(mcp_server=_FakeMcpServer(), agent_type="orchestrator")

    gateway.ensure_allowed_tools("history_read")
    with pytest.raises(PermissionError, match="tool_not_allowed:orchestrator:kg_query"):
        gateway.ensure_allowed_tools("kg_query")


@pytest.mark.parametrize("agent_type", ["unknown", "planner", "controller"])
def test_unknown_agent_types_are_denied(agent_type: str) -> None:
    gateway = MCPToolGateway(mcp_server=_FakeMcpServer(), agent_type=agent_type)

    with pytest.raises(PermissionError, match=f"tool_not_allowed:{agent_type}"):
        gateway.ensure_allowed_tools("history_read")


def test_empty_tool_name_is_denied() -> None:
    gateway = MCPToolGateway(mcp_server=_FakeMcpServer(), agent_type="kg")

    with pytest.raises(PermissionError, match="tool_not_allowed:kg:"):
        gateway.ensure_allowed_tools("")


@pytest.mark.asyncio
async def test_allowed_tool_reaches_mcp_server() -> None:
    server = _FakeMcpServer()
    gateway = MCPToolGateway(mcp_server=server, agent_type="orchestrator")

    result = await gateway.invoke_tool("history_read", {"session_id": "session-1"})

    assert result["ok"] is True
    assert server.calls == [
        {"tool_name": "history_read", "payload": {"session_id": "session-1"}}
    ]
