"""后端通用领域异常。"""

from __future__ import annotations


class UnauthorizedError(Exception):
    """未授权异常。"""


class ForbiddenError(Exception):
    """权限不足异常。"""


class NotFoundError(Exception):
    """资源不存在异常。"""


class InvalidRequestError(Exception):
    """非法请求异常。"""


class ConflictError(Exception):
    """资源状态冲突异常。"""


class RateLimitedError(Exception):
    """包含标准限流响应字段的异常。"""

    def __init__(
        self,
        *,
        endpoint: str,
        dimension: str,
        retry_after_ms: int,
        trace_id: str,
        limit: int,
        window_seconds: float,
    ):
        super().__init__("rate_limited")
        self.endpoint = endpoint
        self.dimension = dimension
        self.retry_after_ms = retry_after_ms
        self.trace_id = trace_id
        self.limit = limit
        self.window_seconds = window_seconds


class ServiceUnavailableError(Exception):
    """服务不可用异常。"""
