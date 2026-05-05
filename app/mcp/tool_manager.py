"""MCP 工具管理模块。

本模块提供基于 YAML 配置的工具注册与管理能力，支持：
- 从配置文件加载工具定义
- 工具别名映射与黑名单过滤
- 向 MCP 服务器批量注册工具
"""

import logging
from pathlib import Path
from typing import Any, Dict

from app.mcp.server import MCPServer, MCPTool

logger = logging.getLogger(__name__)


class ToolManager:
    """MCP 工具管理器。

    负责从 YAML 配置文件加载工具配置，并将工具注册到 MCP 服务器。

    Attributes:
        config_path: 配置文件路径。
        config: 加载后的配置字典。
    """

    def __init__(self, config_path: Path):
        """初始化工具管理器。

        Args:
            config_path: YAML 配置文件路径。

        Returns:
            None: 无返回值。
        """
        self.config_path = config_path
        self.config = self._load_config(config_path)

    def _load_config(self, path: Path) -> Dict[str, Any]:
        """加载并解析 YAML 配置文件。

        Args:
            path: 配置文件路径。

        Returns:
            Dict[str, Any]: 解析后的配置字典。

        Raises:
            FileNotFoundError: 配置文件不存在。
            RuntimeError: PyYAML 未安装。
            ValueError: 配置文件格式无效。
        """
        if not path.exists():
            logger.error("MCP 工具配置文件不存在: %s", path)
            raise FileNotFoundError(f"mcp_tool_config_not_found:{path}")

        try:
            import yaml  # type: ignore[import-untyped]
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, ImportError) as exc:
            logger.error(
                "PyYAML 未安装，无法解析 MCP 工具配置: error_type=%s error=%s",
                type(exc).__name__,
                exc,
                exc_info=True,
            )
            raise RuntimeError("pyyaml_not_installed") from exc

        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        if not isinstance(data, dict):
            logger.error(
                "MCP 工具配置文件格式无效，期望 dict 实际为 %s",
                type(data).__name__,
            )
            raise ValueError("invalid_mcp_tool_config")

        tools = data.get("tools", [])
        if not isinstance(tools, list):
            logger.error(
                "MCP 工具配置中 tools 字段格式无效，期望 list 实际为 %s",
                type(tools).__name__,
            )
            raise ValueError("invalid_tools_section")

        return data

    def register_tools(
        self,
        server: MCPServer,
        builtin_registry: Dict[str, Dict[str, Any]],
        extra_blacklist: list[str] | None = None,
    ) -> dict[str, Any]:
        """将配置中的工具注册到 MCP 服务器。

        根据配置文件中的 tools 列表，将工具注册到服务器。
        支持别名映射、黑名单过滤和 handler 校验。

        Args:
            server: MCP 服务器实例。
            builtin_registry: 内置工具注册表，包含工具 handler 和元数据。
            extra_blacklist: 额外的工具黑名单列表。

        Returns:
            dict[str, Any]: 注册结果，包含：
                - registered: 成功注册的工具列表
                - skipped: 被跳过的工具及原因
                - blacklist: 生效的黑名单集合
        """
        tools_config = self.config.get("tools", [])
        configured_blacklist = self.config.get("blacklist", [])
        if not isinstance(configured_blacklist, list):
            configured_blacklist = []

        runtime_blacklist = extra_blacklist or []
        blacklist_set = {str(item).strip() for item in configured_blacklist + runtime_blacklist if str(item).strip()}

        registered = []
        skipped = []

        for item in tools_config:
            if not isinstance(item, dict):
                skipped.append({"item": item, "reason": "invalid_item"})
                continue

            server_name = str(item.get("server_name") or "local").strip()
            tool_name = str(item.get("tool_name") or "").strip()
            alias = str(item.get("alias") or tool_name).strip()

            if not tool_name or not alias:
                skipped.append({"item": item, "reason": "missing_tool_name_or_alias"})
                continue

            canonical = f"{server_name}::{tool_name}"
            if alias in blacklist_set or tool_name in blacklist_set or canonical in blacklist_set:
                skipped.append({"tool": canonical, "reason": "blacklisted"})
                continue

            builtin = builtin_registry.get(tool_name)
            if not builtin:
                skipped.append({"tool": canonical, "reason": "tool_not_in_registry"})
                continue

            description = str(item.get("description") or builtin.get("description") or "")
            input_schema = item.get("input_schema") or builtin.get("input_schema") or {}
            handler = builtin.get("handler")
            if not callable(handler):
                skipped.append({"tool": canonical, "reason": "handler_not_callable"})
                continue

            server.register(
                MCPTool(
                    name=alias,
                    server_name=server_name,
                    tool_name=tool_name,
                    description=description,
                    input_schema=input_schema,
                    handler=handler,
                )
            )
            registered.append(canonical)

        return {
            "registered": registered,
            "skipped": skipped,
            "blacklist": sorted(blacklist_set),
        }
