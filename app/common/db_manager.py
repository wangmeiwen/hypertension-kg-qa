"""
数据库检查模块
用于检查和管理 Neo4j 和 Milvus 数据库连接
"""

import socket
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Any

from app.common.config import Config

conf = Config()


class DatabaseManager:
    """数据库管理器 - 管理 Neo4j 和 Milvus 连接"""
    
    def __init__(self):
        self.neo4j_host = self._parse_host(conf.NEO4J_URI)
        self.neo4j_port = 7687  # Neo4j Bolt 默认端口
        self.milvus_host = conf.MILVUS_HOST
        self.milvus_port = int(conf.MILVUS_PORT)
    
    def _parse_host(self, uri: str) -> str:
        """从 URI 解析主机名"""
        # 处理 bolt://hostname:port 格式
        if "://" in uri:
            host_part = uri.split("://")[1]
            if ":" in host_part:
                return host_part.split(":")[0]
            return host_part
        return uri
    
    def check_port_open(self, host: str, port: int, timeout: int = 2) -> bool:
        """检查端口是否开放"""
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except (socket.timeout, ConnectionRefusedError, OSError):
            return False
    
    def check_neo4j(self) -> Dict[str, Any]:
        """检查 Neo4j 连接状态"""
        bolt_ok = self.check_port_open(self.neo4j_host, self.neo4j_port)
        
        return {
            "name": "Neo4j",
            "host": self.neo4j_host,
            "bolt_port": self.neo4j_port,
            "bolt_ok": bolt_ok,
            "is_running": bolt_ok
        }
    
    def check_milvus(self) -> Dict[str, Any]:
        """检查 Milvus 连接状态"""
        is_running = self.check_port_open(self.milvus_host, self.milvus_port)
        
        return {
            "name": "Milvus",
            "host": self.milvus_host,
            "port": self.milvus_port,
            "is_running": is_running
        }
    
    def check_all(self) -> Dict[str, Any]:
        """检查所有数据库状态"""
        with ThreadPoolExecutor(max_workers=2) as executor:
            neo4j_future = executor.submit(self.check_neo4j)
            milvus_future = executor.submit(self.check_milvus)
            neo4j_status = neo4j_future.result()
            milvus_status = milvus_future.result()
        
        all_running = neo4j_status["is_running"] and milvus_status["is_running"]
        
        return {
            "all_running": all_running,
            "neo4j": neo4j_status,
            "milvus": milvus_status,
            "message": "所有数据库运行正常" if all_running else "部分数据库未启动"
        }

def check_databases() -> Dict[str, Any]:
    """检查所有数据库状态的函数"""
    return db_manager.check_all()

# 全局数据库管理器实例
db_manager = DatabaseManager()





