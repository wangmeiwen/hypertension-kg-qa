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
        self._max_keys = max(1000, int(max_keys))
        self._lock = threading.Lock()
        self._windows: dict[str, deque[float]] = {}

    def _evict_if_needed(self) -> None:
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


