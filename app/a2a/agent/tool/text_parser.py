import json
from typing import Any, Dict


def _strip_code_block(text: str) -> str:
    """去除 markdown 代码块包裹并返回纯文本。"""
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
    """从文本中提取 JSON 对象。"""
    cleaned = _strip_code_block(text)

    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, TypeError, ValueError):
        pass

    start = cleaned.find("{")
    end = cleaned.rfind("}")

    if start == -1 or end == -1 or end <= start:
        raise ValueError("json_not_found")

    candidate = cleaned[start : end + 1]
    return json.loads(candidate)


def _to_compact_text(data: Any, max_chars: int = 2600) -> str:
    """将对象序列化为紧凑文本，超长时截断。"""
    try:
        text = json.dumps(data, ensure_ascii=False)
    except (TypeError, ValueError):
        text = str(data)

    if len(text) <= max_chars:
        return text

    return text[:max_chars] + "...[truncated]"


def _compact_summary(value: Any, max_len: int = 180) -> str:
    """压缩文本摘要到指定长度。"""
    text = str(value or "").strip()

    if len(text) <= max_len:
        return text

    return text[:max_len] + "..."
