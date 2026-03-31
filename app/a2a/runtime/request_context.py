"""A2A 请求上下文解析。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ParsedMessage:
    """解析后的 A2A message。"""

    text: str = ""
    context_id: str = ""
    user_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class RequestContextParser:
    """A2A 消息解析工具。"""

    @staticmethod
    def extract_text(parts_obj: Any) -> str:
        """提取 text。
        
        Args:
            parts_obj: 参数。
        
        Returns:
            执行结果。
        """
        parts = parts_obj if isinstance(parts_obj, list) else []
        chunks: list[str] = []
        for part in parts:
            if not isinstance(part, dict):
                continue
            if str(part.get("kind") or "").strip().lower() != "text":
                continue
            text = str(part.get("text") or "").strip()
            if text:
                chunks.append(text)
        return "\n".join(chunks).strip()

    @classmethod
    def parse_message(cls, message_obj: Any) -> ParsedMessage:
        """解析 message。
        
        Args:
            message_obj: 参数。
        
        Returns:
            执行结果。
        """
        message = message_obj if isinstance(message_obj, dict) else {}
        metadata_obj = message.get("metadata")
        metadata = metadata_obj if isinstance(metadata_obj, dict) else {}
        context_id = str(message.get("contextId") or metadata.get("session_id") or "").strip()
        user_id = str(message.get("userId") or metadata.get("user_id") or "").strip()
        text = cls.extract_text(message.get("parts"))
        if not text:
            text = str(metadata.get("input_text") or "").strip()
        return ParsedMessage(
            text=text,
            context_id=context_id,
            user_id=user_id,
            metadata=metadata,
        )
