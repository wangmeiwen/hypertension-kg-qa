"""系统与 MCP 路由。"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.backend.infrastructure.database_health import check_databases
from app.core.langsmith_tracing import set_trace_metadata


def create_system_mcp_router(
    await_call: Callable[..., Awaitable[Any]],
    build_readiness_report: Callable[[], Awaitable[dict[str, Any]]],
    get_mcp: Callable[[], Any],
    log_api_error: Callable[..., None],
    get_trace_id: Callable[[Optional[Request]], str],
    enforce_rate_limit: Callable[..., Optional[JSONResponse]],
    rate_limiter: Any,
    endpoint_limits: dict[str, dict[str, int]],
    window_seconds: float,
) -> APIRouter:
    """创建系统健康检查、MCP 工具发现和 MCP 调用路由。

    Args:
        await_call: 兼容同步/异步函数的统一 await 调用器。
        build_readiness_report: 构建服务 readiness 分级报告的函数。
        get_mcp: 获取 MCP 服务实例的函数。
        log_api_error: 记录 API 错误日志的函数。
        get_trace_id: 从请求中提取 trace_id 的函数。
        enforce_rate_limit: 重接口限流检查函数。
        rate_limiter: 限流器实例。
        endpoint_limits: 各接口限流配置。
        window_seconds: 限流统计窗口长度。

    Returns:
        已注册系统与 MCP 路由的 APIRouter。
    """
    router = APIRouter()

    @router.get("/db/status")
    async def db_status() -> Any:
        """获取数据库连接状态。"""
        return await await_call(check_databases)

    @router.get("/health/readiness")
    async def health_readiness() -> JSONResponse:
        """返回服务 readiness 分级状态。"""
        report = await await_call(build_readiness_report)
        status_code = 200 if report["level"] != "unavailable" else 503
        return JSONResponse(
            status_code=status_code, content={"ok": report["ready"], "data": report}
        )

    @router.get("/agent/tools")
    async def agent_tools() -> dict[str, Any]:
        """返回 Agent 策略与可用工具信息。"""
        mcp = get_mcp()
        mcp_tools = await await_call(mcp.list_tools)
        return {
            "default_strategy": "iterative",
            "supported_strategies": ["iterative", "graph"],
            "iterative_actions": [
                {"name": "call_tool", "description": "调用 MCP 工具继续获取证据"},
                {"name": "final_answer", "description": "证据充分后直接输出最终答案"},
            ],
            "mcp_tools": mcp_tools,
        }

    @router.get("/mcp/tools")
    async def mcp_tools() -> dict[str, Any]:
        """返回 MCP 服务中注册的工具列表。"""
        try:
            mcp = get_mcp()
            tools = await await_call(mcp.list_tools)
            return {"ok": True, "tools": tools}
        except RuntimeError as exc:
            log_api_error("/mcp/tools", "runtime", exc)
            return {"ok": False, "error": f"mcp_runtime_error:{str(exc)}"}
        except (
            RuntimeError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            OSError,
            ImportError,
        ) as exc:
            log_api_error("/mcp/tools", "unknown", exc)
            return {"ok": False, "error": f"mcp_unknown_error:{str(exc)}"}

    @router.post("/mcp/invoke")
    async def mcp_invoke(data: dict, request: Request) -> Any:
        """调用 MCP 工具。"""
        trace_id = get_trace_id(request)
        tool_name = data.get("tool_name")
        server_name = data.get("server_name")
        payload_obj = data.get("payload", {})
        payload = payload_obj if isinstance(payload_obj, dict) else {}
        user_id = str(data.get("user_id") or payload.get("user_id") or "").strip()
        session_id = str(
            data.get("session_id") or payload.get("session_id") or ""
        ).strip()

        set_trace_metadata(
            {
                "trace_id": trace_id,
                "request_id": trace_id,
                "endpoint": "/mcp/invoke",
                "tool_name": str(tool_name or ""),
                "server_name": str(server_name or ""),
                "user_id": user_id,
                "session_id": session_id,
            },
            tags=["api", "mcp_invoke"],
        )
        limited = enforce_rate_limit(
            endpoint="/mcp/invoke",
            user_id=user_id,
            session_id=session_id,
            request=request,
            rate_limiter=rate_limiter,
            endpoint_limits=endpoint_limits,
            window_seconds=window_seconds,
        )
        if limited is not None:
            return limited

        if not tool_name:
            return {"ok": False, "error": "tool_name_required", "trace_id": trace_id}

        try:
            mcp = get_mcp()
            resolved_tool_name = (
                f"{str(server_name).strip()}::{str(tool_name).strip()}"
                if str(server_name or "").strip()
                else str(tool_name).strip()
            )
            rows = await mcp.ainvoke_batch(
                [
                    {
                        "tool_name": resolved_tool_name,
                        "payload": payload,
                    }
                ]
            )
            row_obj = rows[0] if isinstance(rows, list) and rows else {}
            result = (
                row_obj
                if isinstance(row_obj, dict)
                else {
                    "ok": False,
                    "error": "mcp_batch_empty_result",
                    "error_type": "runtime_error",
                    "meta": {},
                }
            )
            if isinstance(result, dict):
                meta_obj = result.get("meta")
                meta = meta_obj if isinstance(meta_obj, dict) else {}
                meta["trace_id"] = trace_id
                result["meta"] = meta
                if "trace_id" not in result:
                    result["trace_id"] = trace_id
            return result
        except ValueError as exc:
            log_api_error(
                "/mcp/invoke",
                "payload_validation",
                exc,
                tool_name=tool_name,
                server_name=server_name,
            )
            return {
                "ok": False,
                "error": f"invalid_payload:{str(exc)}",
                "trace_id": trace_id,
            }
        except RuntimeError as exc:
            log_api_error(
                "/mcp/invoke",
                "runtime",
                exc,
                tool_name=tool_name,
                server_name=server_name,
            )
            return {
                "ok": False,
                "error": f"mcp_runtime_error:{str(exc)}",
                "trace_id": trace_id,
            }
        except (
            RuntimeError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            OSError,
            ImportError,
        ) as exc:
            log_api_error(
                "/mcp/invoke",
                "unknown",
                exc,
                tool_name=tool_name,
                server_name=server_name,
            )
            return {
                "ok": False,
                "error": f"mcp_unknown_error:{str(exc)}",
                "trace_id": trace_id,
            }

    return router
