"""管理员细粒度权限与限流策略。"""

from __future__ import annotations

from dataclasses import dataclass


ADMIN_ALL_PERMISSIONS = frozenset(
    {
        "runtime:read",
        "runtime:write",
        "storage:read",
        "sessions:read",
        "sessions:write",
        "observability:read",
        "documents:read",
        "documents:operate",
        "graph:read",
        "graph:write",
        "graph:operate",
    }
)

ADMIN_READ_REQUEST_LIMIT = 120
ADMIN_WRITE_REQUEST_LIMIT = 30
ADMIN_OPERATE_REQUEST_LIMIT = 6


@dataclass(frozen=True)
class AdminAccessRule:
    """单次管理请求所需权限和限流配额。"""

    permission: str
    limit: int


def _resource_for_path(path: str) -> str:
    if path.startswith(("/admin/runtime", "/admin/perf", "/admin/qa", "/admin/stats")):
        return "runtime"
    if path.startswith("/admin/storage"):
        return "storage"
    if path.startswith("/admin/sessions"):
        return "sessions"
    if path.startswith("/admin/agent"):
        return "observability"
    if path.startswith(("/admin/documents", "/admin/text-embedding")):
        return "documents"
    if path.startswith("/admin/graph"):
        return "graph"
    raise ValueError(f"admin_access_policy_missing:{path}")


def resolve_admin_access_rule(method: str, path: str) -> AdminAccessRule:
    """根据 HTTP 方法和稳定路径前缀解析管理端访问规则。"""
    normalized_method = method.upper()
    resource = _resource_for_path(path)
    if normalized_method == "GET":
        return AdminAccessRule(
            permission=f"{resource}:read",
            limit=ADMIN_READ_REQUEST_LIMIT,
        )
    if path == "/admin/graph/execute-cypher":
        return AdminAccessRule(
            permission="graph:read",
            limit=ADMIN_READ_REQUEST_LIMIT,
        )
    if resource in {"documents", "graph"} and (
        "upload" in path
        or "embed" in path
        or "extract" in path
        or path.endswith("/jobs/embed-milvus")
    ):
        return AdminAccessRule(
            permission=f"{resource}:operate",
            limit=ADMIN_OPERATE_REQUEST_LIMIT,
        )
    return AdminAccessRule(
        permission=f"{resource}:write",
        limit=ADMIN_WRITE_REQUEST_LIMIT,
    )
