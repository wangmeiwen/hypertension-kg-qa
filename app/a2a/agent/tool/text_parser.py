"""提供 A2A 工具调用结果解析时使用的文本和 JSON 清洗工具。"""

import json
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


def _strip_code_block(text: str) -> str:
    """去除 Markdown 代码块包装，返回可继续解析的纯文本内容。"""
    content = (text or "").strip()

    if content.startswith("```"):
        parts = content.split("```")
        if len(parts) >= 3:
            block = parts[1]
            if block.startswith("json"):
                block = block[4:]
            return block.strip()

    return content


def _extract_json_object(text: str) -> Dict[str, Any]:
    """从文本中提取 JSON 对象，支持完整 JSON 或夹杂说明文字的 JSON 片段。"""
    cleaned = _strip_code_block(text)

    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.debug(
            "extract_json_object_initial_parse_failed: error_type=%s error=%s",
            type(exc).__name__,
            exc,
        )
        pass

    start = cleaned.find("{")
    end = cleaned.rfind("}")

    if start == -1 or end == -1 or end <= start:
        raise ValueError("json_not_found")

    candidate = cleaned[start : end + 1]
    return json.loads(candidate)


def _to_compact_text(data: Any, max_chars: int = 2600) -> str:
    """将任意对象序列化为紧凑文本，并在超过长度限制时截断。"""
    try:
        text = json.dumps(data, ensure_ascii=False)
    except (TypeError, ValueError):
        text = str(data)

    if len(text) <= max_chars:
        return text

    return text[:max_chars] + "...[truncated]"


def _compact_summary(value: Any, max_len: int = 180) -> str:
    """生成指定最大长度内的单段文本摘要。"""
    text = str(value or "").strip()

    if len(text) <= max_len:
        return text

    return text[:max_len] + "..."
