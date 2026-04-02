"""后端通用领域异常。"""

from __future__ import annotations


class UnauthorizedError(Exception):
    """未授权异常。"""


class NotFoundError(Exception):
    """资源不存在异常。"""


class InvalidRequestError(Exception):
    """非法请求异常。"""


class RateLimitedError(Exception):
    """限流异常。"""


class ServiceUnavailableError(Exception):
    """服务不可用异常。"""
