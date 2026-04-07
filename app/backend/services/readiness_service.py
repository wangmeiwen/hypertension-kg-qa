"""系统 readiness 服务。"""

from __future__ import annotations

from datetime import datetime
import json
import logging
from typing import Any, Awaitable, Callable, Dict, Optional

from fastapi.responses import JSONResponse

from app.common.db_manager import check_databases
from app.common.feature_flags import get_runtime_flag_snapshot

logger = logging.getLogger(__name__)


class ReadinessService:
    """构建并校验服务 readiness。"""

    def __init__(
        self,
        get_mcp: Callable[[], Any],
        mysql_initialized_resolver: Callable[[], Optional[bool]],
        log_api_error: Callable[..., None],
        await_call: Callable[..., Awaitable[Any]],
    ) -> None:
        """初始化实例并注入运行依赖。
        
        Args:
            get_mcp: 函数输入参数。
            mysql_initialized_resolver: 函数输入参数。
            log_api_error: 函数输入参数。
            await_call: 函数输入参数。
        
        Returns:
            None: 无返回值。
        """
        self._get_mcp = get_mcp
        self._mysql_initialized_resolver = mysql_initialized_resolver
        self._log_api_error = log_api_error
        self._await_call = await_call

    async def build_readiness_report(self) -> Dict[str, Any]:
        """构建服务就绪性报告。"""
        from app.mcp.tools import get_mcp_runtime_status

        db_status = await self._await_call(check_databases)

        neo4j_ready = bool((db_status.get("neo4j") or {}).get("is_running"))
        milvus_ready = bool((db_status.get("milvus") or {}).get("is_running"))
        mysql_ready = bool(self._mysql_initialized_resolver())

        dependencies = [
            {
                "name": "mysql",
                "critical": True,
                "ready": mysql_ready,
                "detail": "数据库初始化成功" if mysql_ready else "数据库初始化失败或尚未完成",
            },
            {
                "name": "neo4j",
                "critical": False,
                "ready": neo4j_ready,
                "detail": f"host={(db_status.get('neo4j') or {}).get('host', '')}",
            },
            {
                "name": "milvus",
                "critical": False,
                "ready": milvus_ready,
                "detail": f"host={(db_status.get('milvus') or {}).get('host', '')}",
            },
        ]

        mcp_detail: Dict[str, Any] = {
            "core_ready": False,
            "missing_required": [],
            "runtime_unavailable_required": [],
            "missing_optional": [],
            "runtime_unavailable_optional": [],
            "runtime_errors": {},
            "tool_count": 0,
            "tool_names": [],
            "optional_issues": [],
        }
        mcp_error = ""

        try:
            mcp_detail = await self._await_call(get_mcp_runtime_status, self._get_mcp())
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            mcp_error = str(exc)
            self._log_api_error("/health/readiness", "mcp_runtime_status", exc)

        mcp_core_ready = bool(mcp_detail.get("core_ready")) and not mcp_error
        dependencies.append(
            {
                "name": "mcp_core_tools",
                "critical": False,
                "ready": mcp_core_ready,
                "detail": mcp_error
                or json.dumps(
                    {
                        "missing_required": mcp_detail.get("missing_required", []),
                        "runtime_unavailable_required": mcp_detail.get("runtime_unavailable_required", []),
                    },
                    ensure_ascii=False,
                ),
            }
        )

        optional_issues = list(mcp_detail.get("optional_issues") or [])
        if not neo4j_ready:
            optional_issues.append("neo4j_unavailable")
        if not milvus_ready:
            optional_issues.append("milvus_unavailable")
        if not mcp_core_ready:
            optional_issues.append("mcp_core_degraded")
        if mcp_error:
            optional_issues.append("mcp_server_unavailable")

        critical_not_ready = [item["name"] for item in dependencies if item["critical"] and not item["ready"]]

        level = "ready"
        if critical_not_ready:
            level = "unavailable"
        elif optional_issues:
            level = "degraded"

        return {
            "level": level,
            "ready": level != "unavailable",
            "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "critical_not_ready": critical_not_ready,
            "optional_issues": optional_issues,
            "dependencies": dependencies,
            "db": db_status,
            "mcp": mcp_detail,
            "runtime_flags": get_runtime_flag_snapshot(),
        }

    async def ensure_core_ready(self, endpoint: str) -> Optional[JSONResponse]:
        """在接口执行前校验核心依赖就绪状态。"""
        report = await self.build_readiness_report()
        if report["level"] != "unavailable":
            return None

        logger.warning(
            "[readiness_block] %s",
            json.dumps(
                {
                    "endpoint": endpoint,
                    "critical_not_ready": report.get("critical_not_ready", []),
                },
                ensure_ascii=False,
            ),
        )
        return JSONResponse(
            status_code=503,
            content={
                "ok": False,
                "error": "service_unavailable",
                "message": "核心依赖未就绪，请稍后重试",
                "readiness": report,
            },
        )
