"""原生 A2A SDK 服务器装配适配层。"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class A2AServerBundle:
    """A2A 服务器组件容器。"""

    app: Any
    request_handler: Any
    agent_executor: Any
    agent_card: dict[str, Any]


def _import_a2a_sdk() -> dict[str, Any]:
    """按需导入 A2A SDK。"""
    from a2a.server.apps.jsonrpc.fastapi_app import A2AFastAPIApplication
    from a2a.server.agent_execution import AgentExecutor
    from a2a.server.request_handlers import DefaultRequestHandler

    return {
        "A2AFastAPIApplication": A2AFastAPIApplication,
        "AgentExecutor": AgentExecutor,
        "DefaultRequestHandler": DefaultRequestHandler,
    }


def build_request_handler(agent_executor: Any) -> Any:
    """基于原生 SDK 构建 request handler。"""
    sdk = _import_a2a_sdk()
    handler_cls = sdk["DefaultRequestHandler"]
    return handler_cls(agent_executor=agent_executor)


def build_fastapi_app(
    *,
    agent_card: dict[str, Any],
    request_handler: Any,
    rpc_url: str,
    card_url: str = "/.well-known/agent-card.json",
) -> Any:
    """基于原生 SDK 构建 FastAPI 子应用。"""
    sdk = _import_a2a_sdk()
    app_cls = sdk["A2AFastAPIApplication"]

    # 不同 SDK 版本构造参数可能变化，这里采用逐步降级构建。
    try:
        app = app_cls(
            request_handler=request_handler,
            agent_card=agent_card,
            rpc_url=rpc_url,
            card_url=card_url,
        )
    except TypeError as exc:
        logger.warning(
            "A2A SDK app init fallback to legacy signature: error_type=%s error=%s",
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        try:
            app = app_cls(request_handler=request_handler, agent_card=agent_card)
        except TypeError as nested_exc:
            logger.warning(
                "A2A SDK app init fallback to minimal signature: error_type=%s error=%s",
                type(nested_exc).__name__,
                nested_exc,
                exc_info=True,
            )
            app = app_cls(request_handler=request_handler)
    return getattr(app, "app", app)


def mount_agent_app(root_app: Any, mount_path: str, agent_app: Any) -> None:
    """将子应用挂载到主应用。"""
    root_app.mount(str(mount_path or "/").strip(), agent_app)
