"""进程内并发治理工具。"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from collections import defaultdict
from typing import Any, AsyncIterator, Iterator
from uuid import uuid4


class LockAcquireTimeout(TimeoutError):
    """资源锁获取超时。"""


class AsyncReaderWriterLock:
    """单进程异步读写锁：多读共享，写独占。"""

    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._readers = 0
        self._writer = False
        self._waiting_writers = 0

    async def acquire_read(self, timeout: float | None = None) -> None:
        async def _wait() -> None:
            async with self._condition:
                while self._writer or self._waiting_writers > 0:
                    await self._condition.wait()
                self._readers += 1

        await _wait_with_timeout(_wait(), timeout)

    async def release_read(self) -> None:
        async with self._condition:
            self._readers = max(0, self._readers - 1)
            if self._readers == 0:
                self._condition.notify_all()

    async def acquire_write(self, timeout: float | None = None) -> None:
        async def _wait() -> None:
            async with self._condition:
                self._waiting_writers += 1
                try:
                    while self._writer or self._readers > 0:
                        await self._condition.wait()
                    self._writer = True
                finally:
                    self._waiting_writers = max(0, self._waiting_writers - 1)

        await _wait_with_timeout(_wait(), timeout)

    async def release_write(self) -> None:
        async with self._condition:
            self._writer = False
            self._condition.notify_all()


async def _wait_with_timeout(awaitable: Any, timeout: float | None) -> None:
    if timeout is None:
        await awaitable
        return
    try:
        await asyncio.wait_for(awaitable, timeout=max(0.001, float(timeout)))
    except asyncio.TimeoutError as exc:
        raise LockAcquireTimeout("concurrency_lock_timeout") from exc


class ConcurrencyGuard:
    """统一管理 keyed lock、读写锁和可选持久锁。"""

    def __init__(self) -> None:
        self._async_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._rw_locks: defaultdict[str, AsyncReaderWriterLock] = defaultdict(AsyncReaderWriterLock)
        self._sync_locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)

    @contextlib.asynccontextmanager
    async def keyed_lock(self, key: str, timeout: float | None = None) -> AsyncIterator[None]:
        lock = self._async_locks[str(key)]
        await _wait_with_timeout(lock.acquire(), timeout)
        try:
            yield
        finally:
            lock.release()

    @contextlib.asynccontextmanager
    async def reader_lock(self, key: str, timeout: float | None = None) -> AsyncIterator[None]:
        lock = self._rw_locks[str(key)]
        await lock.acquire_read(timeout=timeout)
        try:
            yield
        finally:
            await lock.release_read()

    @contextlib.asynccontextmanager
    async def writer_lock(self, key: str, timeout: float | None = None) -> AsyncIterator[None]:
        lock = self._rw_locks[str(key)]
        await lock.acquire_write(timeout=timeout)
        try:
            yield
        finally:
            await lock.release_write()

    @contextlib.contextmanager
    def sync_keyed_lock(self, key: str) -> Iterator[None]:
        lock = self._sync_locks[str(key)]
        with lock:
            yield

    @contextlib.asynccontextmanager
    async def mysql_lock(
        self,
        key: str,
        *,
        job_id: str = "",
        ttl_seconds: int = 900,
        owner: str | None = None,
    ) -> AsyncIterator[dict[str, str]]:
        """可选 MySQL 持久锁；数据库不可用时抛出调用方处理。"""
        from app.backend.repositories.database import execute, fetch_one

        safe_key = str(key)
        safe_owner = owner or uuid4().hex
        expires_seconds = max(1, int(ttl_seconds or 900))
        await execute(
            """
            INSERT INTO operation_locks (lock_key, job_id, owner, expires_at, heartbeat_at)
            VALUES (:lock_key, :job_id, :owner, TIMESTAMPADD(SECOND, :ttl, UTC_TIMESTAMP()), UTC_TIMESTAMP())
            ON DUPLICATE KEY UPDATE
                owner = IF(expires_at < UTC_TIMESTAMP(), VALUES(owner), owner),
                job_id = IF(expires_at < UTC_TIMESTAMP(), VALUES(job_id), job_id),
                expires_at = IF(expires_at < UTC_TIMESTAMP(), VALUES(expires_at), expires_at),
                heartbeat_at = IF(expires_at < UTC_TIMESTAMP(), VALUES(heartbeat_at), heartbeat_at)
            """,
            {"lock_key": safe_key, "job_id": str(job_id or ""), "owner": safe_owner, "ttl": expires_seconds},
        )
        row = await fetch_one("SELECT owner FROM operation_locks WHERE lock_key = :lock_key", {"lock_key": safe_key})
        if str((row or {}).get("owner") or "") != safe_owner:
            raise LockAcquireTimeout(f"mysql_lock_busy:{safe_key}")
        async def _heartbeat_loop() -> None:
            interval = max(1.0, min(30.0, expires_seconds / 3))
            while True:
                await asyncio.sleep(interval)
                await self.heartbeat_mysql_lock(safe_key, safe_owner, ttl_seconds=expires_seconds)

        heartbeat_task = asyncio.create_task(_heartbeat_loop(), name=f"operation-lock-heartbeat:{safe_key}")
        try:
            yield {"lock_key": safe_key, "owner": safe_owner}
        finally:
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task
            await execute(
                "DELETE FROM operation_locks WHERE lock_key = :lock_key AND owner = :owner",
                {"lock_key": safe_key, "owner": safe_owner},
            )

    async def heartbeat_mysql_lock(self, key: str, owner: str, ttl_seconds: int = 900) -> None:
        from app.backend.repositories.database import execute

        await execute(
            """
            UPDATE operation_locks
            SET heartbeat_at = UTC_TIMESTAMP(),
                expires_at = TIMESTAMPADD(SECOND, :ttl, UTC_TIMESTAMP())
            WHERE lock_key = :lock_key AND owner = :owner
            """,
            {"lock_key": str(key), "owner": str(owner), "ttl": max(1, int(ttl_seconds or 900))},
        )


concurrency_guard = ConcurrencyGuard()
