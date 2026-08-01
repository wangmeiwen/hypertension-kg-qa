"""
轻量级内存限流器。

按 key 维护滑动时间窗口计数，用于重接口的用户/会话限流。
"""

from collections import deque
import threading
import time
from typing import Any


class InMemorySlidingWindowRateLimiter:
    """线程安全的滑动窗口限流器。"""

    def __init__(self, max_keys: int = 20000):
        """初始化滑动窗口限流器。

        限流器按 key 维护请求时间戳队列；当 key 总量超过阈值时，
        会触发近似 FIFO 淘汰，避免内存无限增长。

        Args:
            max_keys: 允许追踪的最大 key 数量。

        Returns:
            None: 无返回值。
        """
        self._max_keys = max(1000, int(max_keys))
        self._lock = threading.Lock()
        self._windows: dict[str, deque[float]] = {}

    def _evict_if_needed(self) -> None:
        """在 key 数量超限时执行内存回收。

        当 `_windows` 中 key 过多时，按插入顺序移除一小批最早 key，
        用低开销方式控制内存上界。

        Returns:
            None: 无返回值。
        """
        if len(self._windows) <= self._max_keys:
            return
        # FIFO 近似淘汰：移除最早插入的一批 key，避免内存持续增长。
        for idx, key in enumerate(list(self._windows.keys())):
            self._windows.pop(key, None)
            if idx >= 256:
                break

    def check(
        self,
        key: str,
        limit: int,
        window_seconds: float,
    ) -> dict[str, Any]:
        """执行一次限流判断并返回窗口状态。

        逻辑流程：
        1) 清理窗口中过期请求；
        2) 判断当前请求是否超过阈值；
        3) 若允许则写入时间戳；
        4) 返回标准限流结果，包含剩余额度与重试等待时间。

        Args:
            key: 限流键（如 user_id、session_id 或 IP）。
            limit: 窗口内允许的最大请求数。
            window_seconds: 限流窗口大小（秒）。

        Returns:
            dict[str, Any]: 限流判定结果，常用字段包括：
                - allowed: 是否允许本次请求
                - count: 当前窗口内请求数
                - remaining: 剩余可用配额
                - retry_after_ms: 建议重试等待毫秒数
        """
        safe_key = str(key or "").strip()
        safe_limit = max(1, int(limit))
        safe_window = max(1.0, float(window_seconds))
        now = time.monotonic()
        cutoff = now - safe_window

        with self._lock:
            bucket = self._windows.get(safe_key)
            if bucket is None:
                bucket = deque()
                self._windows[safe_key] = bucket
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()

            current = len(bucket)
            if current >= safe_limit:
                oldest = bucket[0] if bucket else now
                retry_after_ms = int(max(0.0, (oldest + safe_window - now) * 1000.0))
                return {
                    "allowed": False,
                    "count": current,
                    "limit": safe_limit,
                    "window_seconds": safe_window,
                    "remaining": 0,
                    "retry_after_ms": retry_after_ms,
                }

            bucket.append(now)
            current = len(bucket)
            oldest = bucket[0] if bucket else now
            reset_after_ms = int(max(0.0, (oldest + safe_window - now) * 1000.0))
            self._evict_if_needed()
            return {
                "allowed": True,
                "count": current,
                "limit": safe_limit,
                "window_seconds": safe_window,
                "remaining": max(0, safe_limit - current),
                "retry_after_ms": reset_after_ms,
            }
