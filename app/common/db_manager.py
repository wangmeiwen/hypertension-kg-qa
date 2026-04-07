"""数据库健康检查工具（异步）。

该模块提供对 Neo4j 与 Milvus 的基础可达性检查能力，
用于系统启动前自检、健康接口输出与故障快速定位。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

from app.common.config import Config

conf = Config()
logger = logging.getLogger(__name__)


class DatabaseManager:
    """封装 Neo4j 与 Milvus 的连通性健康检查流程。"""

    def __init__(self):
        """初始化数据库健康检查器。

        初始化时会读取配置中的连接信息，并做两项预处理：
        1) 将 localhost 统一规范为 127.0.0.1，避免 IPv4/IPv6 回环差异导致误判。
        2) 从 Neo4j URI 中提取主机名，供 TCP 端口探测使用。

        Returns:
            None: 无返回值。
        """
        self.neo4j_host = self._normalize_loopback_host(self._parse_host(conf.NEO4J_URI))
        self.neo4j_port = 7687
        self.milvus_host = self._normalize_loopback_host(conf.MILVUS_HOST)
        self.milvus_port = int(conf.MILVUS_PORT)

    @staticmethod
    def _normalize_loopback_host(host: str) -> str:
        """规范化回环主机名。

        当输入为 localhost 时，统一转换为 127.0.0.1，
        以减少不同运行环境下 DNS/回环协议栈差异造成的连通性误判。

        Args:
            host: 原始主机名或 IP 字符串。

        Returns:
            str: 规范化后的主机名。
        """
        value = str(host or "").strip()
        if value.lower() == "localhost":
            return "127.0.0.1"
        return value

    @staticmethod
    def _parse_host(uri: str) -> str:
        """从 URI 字符串中提取主机名。

        支持如 bolt://127.0.0.1:7687、neo4j://localhost:7687 等格式。
        若字符串中不含协议前缀，则按纯主机名处理并原样返回。

        Args:
            uri: 包含主机信息的 URI 或主机名。

        Returns:
            str: 解析得到的主机名。
        """
        if "://" in uri:
            host_part = uri.split("://", 1)[1]
            if ":" in host_part:
                return host_part.split(":", 1)[0]
            return host_part
        return uri

    async def check_port_open(self, host: str, port: int, timeout: float = 2.0) -> bool:
        """探测指定 TCP 端口是否可连接。

        本方法通过建立短连接来判断端口连通性，不验证上层协议语义。
        例如 Neo4j 仅表示 Bolt 端口可连，不代表鉴权一定成功。

        Args:
            host: 目标主机名或 IP。
            port: 目标端口号。
            timeout: 超时时间（秒），超过后视为不可达。

        Returns:
            bool: True 表示端口可连，False 表示不可连或连接失败。
        """
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
            writer.close()
            await writer.wait_closed()
            _ = reader
            return True
        except Exception as exc:
            logger.warning(
                "check_port_open_failed: host=%s port=%s timeout=%s error_type=%s error=%s",
                host,
                port,
                timeout,
                type(exc).__name__,
                exc,
            )
            return False

    async def check_neo4j(self) -> Dict[str, Any]:
        """检查 Neo4j 服务连通性。

        当前检查项为 Bolt 端口连通性，返回统一的状态字典，
        供健康检查接口直接透传或组合展示。

        Returns:
            Dict[str, Any]: Neo4j 状态信息，包含主机、端口与运行标记。
        """
        bolt_ok = await self.check_port_open(self.neo4j_host, self.neo4j_port)
        return {
            "name": "Neo4j",
            "host": self.neo4j_host,
            "bolt_port": self.neo4j_port,
            "bolt_ok": bolt_ok,
            "is_running": bolt_ok,
        }

    async def check_milvus(self) -> Dict[str, Any]:
        """检查 Milvus 服务连通性。

        当前检查项为 Milvus 服务端口可达性，返回统一状态结构，
        便于与 Neo4j 检查结果聚合。

        Returns:
            Dict[str, Any]: Milvus 状态信息，包含主机、端口与运行标记。
        """
        is_running = await self.check_port_open(self.milvus_host, self.milvus_port)
        return {
            "name": "Milvus",
            "host": self.milvus_host,
            "port": self.milvus_port,
            "is_running": is_running,
        }

    async def check_all(self) -> Dict[str, Any]:
        """并发检查所有数据库依赖并汇总结果。

        当前包含 Neo4j 与 Milvus 两项检查，返回总状态、分项状态和友好提示文案。

        Returns:
            Dict[str, Any]: 聚合后的数据库健康状态报告。
        """
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
    """对外暴露的数据库健康检查入口。

    该函数用于给业务层或 API 层调用，内部复用单例 DatabaseManager。

    Returns:
        Dict[str, Any]: 数据库健康检查结果。
    """
    return await _db_manager.check_all()
