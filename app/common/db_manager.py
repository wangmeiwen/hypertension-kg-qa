"""数据库健康检查（异步版）。"""

from __future__ import annotations

import asyncio
from typing import Any, Dict

from app.common.config import Config

conf = Config()


class DatabaseManager:
    def __init__(self):
        self.neo4j_host = self._normalize_loopback_host(self._parse_host(conf.NEO4J_URI))
        self.neo4j_port = 7687
        self.milvus_host = self._normalize_loopback_host(conf.MILVUS_HOST)
        self.milvus_port = int(conf.MILVUS_PORT)

    @staticmethod
    def _normalize_loopback_host(host: str) -> str:
        value = str(host or "").strip()
        if value.lower() == "localhost":
            return "127.0.0.1"
        return value

    @staticmethod
    def _parse_host(uri: str) -> str:
        if "://" in uri:
            host_part = uri.split("://", 1)[1]
            if ":" in host_part:
                return host_part.split(":", 1)[0]
            return host_part
        return uri

    async def check_port_open(self, host: str, port: int, timeout: float = 2.0) -> bool:
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
            writer.close()
            await writer.wait_closed()
            _ = reader
            return True
        except Exception:
            return False

    async def check_neo4j(self) -> Dict[str, Any]:
        bolt_ok = await self.check_port_open(self.neo4j_host, self.neo4j_port)
        return {
            "name": "Neo4j",
            "host": self.neo4j_host,
            "bolt_port": self.neo4j_port,
            "bolt_ok": bolt_ok,
            "is_running": bolt_ok,
        }

    async def check_milvus(self) -> Dict[str, Any]:
        is_running = await self.check_port_open(self.milvus_host, self.milvus_port)
        return {
            "name": "Milvus",
            "host": self.milvus_host,
            "port": self.milvus_port,
            "is_running": is_running,
        }

    async def check_all(self) -> Dict[str, Any]:
        neo4j_status, milvus_status = await asyncio.gather(self.check_neo4j(), self.check_milvus())
        all_running = bool(neo4j_status.get("is_running")) and bool(milvus_status.get("is_running"))
        return {
            "all_running": all_running,
            "neo4j": neo4j_status,
            "milvus": milvus_status,
            "message": "所有数据库运行正常" if all_running else "部分数据库未启动",
        }


_db_manager = DatabaseManager()


async def check_databases() -> Dict[str, Any]:
    return await _db_manager.check_all()
