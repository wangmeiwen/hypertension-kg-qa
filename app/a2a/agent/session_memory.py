"""session_memory 模块。

提供 session_memory 相关能力与对外接口。
"""


from __future__ import annotations

import threading
from typing import Any


class SessionMemory:
    """线程安全会话内存。"""

    def __init__(self) -> None:
        """初始化 SessionMemory 实例。
        """
        self._lock = threading.RLock()
        self._store: dict[str, list[dict[str, Any]]] = {}

    def append(self, session_id: str, item: dict[str, Any]) -> None:
        """执行 append。
        
        Args:
            session_id: 参数。
            item: 参数。
        
        Returns:
            执行结果。
        """
        sid = str(session_id or "").strip()
        if not sid:
            return
        row = item if isinstance(item, dict) else {}
        with self._lock:
            bucket = self._store.get(sid, [])
            bucket.append(row)
            self._store[sid] = bucket

    def get(self, session_id: str) -> list[dict[str, Any]]:
        """执行 get。
        
        Args:
            session_id: 参数。
        
        Returns:
            执行结果。
        """
        sid = str(session_id or "").strip()
        with self._lock:
            rows = self._store.get(sid, [])
            return list(rows)

    def clear(self, session_id: str) -> None:
        """执行 clear。
        
        Args:
            session_id: 参数。
        
        Returns:
            执行结果。
        """
        sid = str(session_id or "").strip()
        with self._lock:
            self._store.pop(sid, None)
