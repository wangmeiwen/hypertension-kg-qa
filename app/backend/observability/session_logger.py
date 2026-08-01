"""
会话日志模块。

本模块提供请求、会话、LLM 与工具调用的结构化日志记录能力，支持：
- 请求/会话上下文管理
- PII 脱敏与内容截断
- API/LLM/操作/工具四类日志落盘
- 会话元数据统计计数

主要函数：
- log_api_call: 记录 API 调用日志
- log_llm_call: 记录 LLM 调用日志
- log_session_op: 记录会话操作日志
- log_tool_event: 记录工具调用日志

"""

import contextlib
import contextvars
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import threading
import uuid
from typing import Any

from app.a2a.telemetry import normalize_token_usage
from app.core.concurrency import concurrency_guard

logger = logging.getLogger(__name__)


_REQUEST_ID_CTX: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default=""
)
_SESSION_ID_CTX: contextvars.ContextVar[str] = contextvars.ContextVar(
    "session_id", default=""
)
_USER_ID_CTX: contextvars.ContextVar[str] = contextvars.ContextVar(
    "user_id", default=""
)
_TURN_ID_CTX: contextvars.ContextVar[str] = contextvars.ContextVar(
    "turn_id", default=""
)

_WRITE_LOCK = threading.Lock()
_PHONE_PATTERN = re.compile(r"(?<!\d)(1\d{10})(?!\d)")
_EMAIL_PATTERN = re.compile(r"([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})")
_IDCARD_PATTERN = re.compile(r"(?<!\d)(\d{17}[\dXx]|\d{15})(?!\d)")


def _session_artifact_lock_key(session_id: str) -> str:
    """基于会话 ID 构造会话日志工件锁 key。"""
    return f"session-artifacts:{_safe_id(session_id)}"


def _session_artifact_lock_key_for_path(file_path: Path) -> str | None:
    """从会话日志路径中提取会话工件锁 key。"""
    parts = file_path.parts
    for idx, part in enumerate(parts):
        if part == "sessions" and idx + 1 < len(parts):
            return f"session-artifacts:{parts[idx + 1]}"
    return None


def _utc_now() -> str:
    """
    获取当前 UTC 时间字符串。

    Args:
        None。

    Returns:
        str: ISO8601 格式 UTC 时间。
    """
    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


def _project_root() -> Path:
    """
    获取项目根目录路径。

    Args:
        None。

    Returns:
        Path: 项目根目录路径。
    """
    return Path(__file__).resolve().parents[3]


def _safe_id(raw: str, fallback: str = "unknown") -> str:
    """
    将任意字符串规范化为安全 ID。

    Args:
        raw: 原始字符串。
        fallback: 空值时回退值。

    Returns:
        str: 规范化后的安全 ID。
    """
    text = str(raw or "").strip()
    if not text:
        return fallback
    allowed = []
    for ch in text:
        if ch.isalnum() or ch in {"-", "_"}:
            allowed.append(ch)
        else:
            allowed.append("_")
    cleaned = "".join(allowed).strip("_")
    if not cleaned:
        return fallback
    return cleaned


def _logs_root() -> Path:
    """
    获取日志根目录并确保目录存在。

    Args:
        None。

    Returns:
        Path: 日志根目录。
    """
    custom = str(os.getenv("LOG_ROOT_DIR", "logs")).strip()
    root = _project_root() / custom
    root.mkdir(parents=True, exist_ok=True)
    return root


def _api_dir() -> Path:
    """
    获取 API 日志目录并确保目录存在。

    Args:
        None。

    Returns:
        Path: API 日志目录。
    """
    custom = str(os.getenv("LOG_API_DIR", "logs/api")).strip()
    directory = _project_root() / custom
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _sessions_dir() -> Path:
    """
    获取会话日志目录并确保目录存在。

    Args:
        None。

    Returns:
        Path: 会话日志目录。
    """
    custom = str(os.getenv("LOG_SESSION_DIR", "logs/sessions")).strip()
    directory = _project_root() / custom
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _include_llm_content() -> bool:
    """
    判断是否在日志中记录 LLM 内容。

    Args:
        None。

    Returns:
        bool: 是否包含 LLM 内容。
    """
    raw = str(os.getenv("LOG_INCLUDE_LLM_CONTENT", "true")).strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _max_content_chars() -> int:
    """
    获取日志内容最大长度限制。

    Args:
        None。

    Returns:
        int: 最大字符数。
    """
    raw = str(os.getenv("LOG_LLM_CONTENT_MAX_CHARS", "8000")).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        logger.warning(
            "session_logger_invalid_max_content_chars: raw=%s error_type=%s error=%s",
            raw,
            type(exc).__name__,
            exc,
        )
        value = 8000
    if value < 500:
        return 500
    return value


def _compact_text(value: Any, max_chars: int) -> str:
    """
    将对象压缩为限定长度字符串。

    Args:
        value: 待压缩对象。
        max_chars: 最大字符数。

    Returns:
        str: 压缩后的文本。
    """
    try:
        text = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        logger.debug(
            "session_logger_compact_text_json_fallback: value_type=%s error_type=%s error=%s",
            type(value).__name__,
            type(exc).__name__,
            exc,
        )
        text = str(value)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "...[truncated]"


def _sha256_text(value: Any) -> str:
    """
    对对象文本表示计算 SHA256 摘要。

    Args:
        value: 任意可序列化对象。

    Returns:
        str: SHA256 十六进制摘要。
    """
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        logger.debug(
            "session_logger_sha_json_fallback: value_type=%s error_type=%s error=%s",
            type(value).__name__,
            type(exc).__name__,
            exc,
        )
        text = str(value)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _mask_pii_text(text: str) -> str:
    """
    对文本中的 PII 字段进行脱敏。

    Args:
        text: 原始文本。

    Returns:
        str: 脱敏后的文本。
    """
    value = str(text or "")
    value = _PHONE_PATTERN.sub("<PHONE_MASKED>", value)
    value = _EMAIL_PATTERN.sub("<EMAIL_MASKED>", value)
    value = _IDCARD_PATTERN.sub("<ID_MASKED>", value)
    return value


def _mask_sensitive(value: Any) -> Any:
    """
    递归脱敏对象中的敏感字段与 PII。

    Args:
        value: 任意对象。

    Returns:
        Any: 脱敏后的对象。
    """
    if isinstance(value, dict):
        masked: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key).lower()
            if key_text in {
                "authorization",
                "api_key",
                "apikey",
                "token",
                "password",
                "secret",
            }:
                masked[key] = "***"
            else:
                masked[key] = _mask_sensitive(item)
        return masked
    if isinstance(value, list):
        return [_mask_sensitive(item) for item in value]
    if isinstance(value, str):
        return _mask_pii_text(value)
    return value


def _append_jsonl(file_path: Path, payload: dict[str, Any]) -> None:
    """
    以 JSONL 形式追加写入日志记录。

    Args:
        file_path: 目标日志文件路径。
        payload: 日志内容字典。

    Returns:
        None。
    """
    file_path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(_mask_sensitive(payload), ensure_ascii=False, default=str)
    lock_key = _session_artifact_lock_key_for_path(file_path)
    lock_cm = (
        concurrency_guard.sync_keyed_lock(lock_key)
        if lock_key
        else contextlib.nullcontext()
    )
    with lock_cm:
        with _WRITE_LOCK:
            with file_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")


def append_jsonl_atomic(file_path: Path, payload: dict[str, Any]) -> None:
    """以公共入口追加 JSONL，复用统一写锁和脱敏逻辑。

    Args:
        file_path: 目标 JSONL 文件路径。
        payload: 待写入的日志对象。

    Returns:
        None: 无返回值。
    """
    _append_jsonl(file_path, payload)


def create_request_id() -> str:
    """
    创建新的请求 ID。

    Args:
        None。

    Returns:
        str: UUID 字符串。
    """
    return str(uuid.uuid4())


def set_request_context(request_id: str) -> contextvars.Token[str]:
    """
    设置当前上下文请求 ID。

    Args:
        request_id: 请求 ID。

    Returns:
        contextvars.Token[str]: 上下文 token。
    """
    return _REQUEST_ID_CTX.set(str(request_id or ""))


def reset_request_context(token: contextvars.Token[str]) -> None:
    """
    重置请求上下文。

    Args:
        token: set_request_context 返回的 token。

    Returns:
        None。
    """
    _REQUEST_ID_CTX.reset(token)


def set_session_context(
    session_id: str, user_id: str = ""
) -> tuple[contextvars.Token[str], contextvars.Token[str]]:
    """
    设置会话上下文。

    Args:
        session_id: 会话 ID。
        user_id: 用户 ID。

    Returns:
        tuple[contextvars.Token[str], contextvars.Token[str]]: 会话与用户上下文 token。
    """
    token_sid = _SESSION_ID_CTX.set(str(session_id or ""))
    token_uid = _USER_ID_CTX.set(str(user_id or ""))
    return token_sid, token_uid


def reset_session_context(
    tokens: tuple[contextvars.Token[str], contextvars.Token[str]],
) -> None:
    """
    重置会话上下文。

    Args:
        tokens: set_session_context 返回的 token 元组。

    Returns:
        None。
    """
    token_sid, token_uid = tokens
    _SESSION_ID_CTX.reset(token_sid)
    _USER_ID_CTX.reset(token_uid)


def get_request_id() -> str:
    """
    获取当前请求 ID。

    Args:
        None。

    Returns:
        str: 请求 ID。
    """
    return _REQUEST_ID_CTX.get()


def set_turn_context(turn_id: str) -> contextvars.Token[str]:
    """设置当前问答轮次 ID。"""
    return _TURN_ID_CTX.set(str(turn_id))


def reset_turn_context(token: contextvars.Token[str]) -> None:
    """重置当前问答轮次 ID。"""
    _TURN_ID_CTX.reset(token)


def get_turn_id() -> str:
    """获取当前问答轮次 ID。"""
    return _TURN_ID_CTX.get()


def get_session_id() -> str:
    """
    获取当前会话 ID。

    Args:
        None。

    Returns:
        str: 会话 ID。
    """
    return _SESSION_ID_CTX.get()


def get_user_id() -> str:
    """
    获取当前用户 ID。

    Args:
        None。

    Returns:
        str: 用户 ID。
    """
    return _USER_ID_CTX.get()


def ensure_session_meta(session_id: str, user_id: str = "") -> None:
    """
    确保会话元数据文件存在并更新基础字段。

    Args:
        session_id: 会话 ID。
        user_id: 用户 ID。

    Returns:
        None。
    """
    sid = _safe_id(session_id)
    session_dir = _sessions_dir() / sid
    session_dir.mkdir(parents=True, exist_ok=True)
    meta_file = session_dir / "session_meta.json"
    now = _utc_now()

    with concurrency_guard.sync_keyed_lock(_session_artifact_lock_key(sid)):
        with _WRITE_LOCK:
            if meta_file.exists():
                try:
                    existing = json.loads(meta_file.read_text(encoding="utf-8"))
                except (OSError, ValueError, TypeError) as exc:
                    logger.warning(
                        "session_logger_meta_read_failed_reset: session_id=%s error_type=%s error=%s",
                        sid,
                        type(exc).__name__,
                        exc,
                    )
                    existing = {}
            else:
                existing = {}

            session_meta = {
                "session_id": sid,
                "user_id": str(user_id or existing.get("user_id") or ""),
                "created_at": str(existing.get("created_at") or now),
                "latest_request_at": now,
                "llm_call_count": int(existing.get("llm_call_count") or 0),
                "op_event_count": int(existing.get("op_event_count") or 0),
                "tool_event_count": int(existing.get("tool_event_count") or 0),
            }
            meta_file.write_text(
                json.dumps(session_meta, ensure_ascii=False, indent=2), encoding="utf-8"
            )


def _bump_session_meta_count(session_id: str, field_name: str) -> None:
    """
    增加会话元数据中的计数字段。

    Args:
        session_id: 会话 ID。
        field_name: 计数字段名。

    Returns:
        None。
    """
    sid = _safe_id(session_id)
    meta_file = _sessions_dir() / sid / "session_meta.json"
    session_dir = _sessions_dir() / sid
    session_dir.mkdir(parents=True, exist_ok=True)
    with concurrency_guard.sync_keyed_lock(_session_artifact_lock_key(sid)):
        with _WRITE_LOCK:
            if meta_file.exists():
                try:
                    data = json.loads(meta_file.read_text(encoding="utf-8"))
                except (OSError, ValueError, TypeError) as exc:
                    logger.warning(
                        "session_logger_meta_counter_read_failed_reset: session_id=%s error_type=%s error=%s",
                        sid,
                        type(exc).__name__,
                        exc,
                    )
                    data = {"session_id": sid}
            else:
                data = {
                    "session_id": sid,
                    "user_id": get_user_id(),
                    "created_at": _utc_now(),
                    "llm_call_count": 0,
                    "op_event_count": 0,
                    "tool_event_count": 0,
                }
            data[field_name] = int(data.get(field_name) or 0) + 1
            data["latest_request_at"] = _utc_now()
            meta_file.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )


def log_api_call(
    request_id: str,
    method: str,
    path: str,
    status_code: int,
    is_streaming: bool,
    ttfb_ms: float,
    stream_total_ms: float,
    client_ip: str = "",
    session_id: str = "",
    user_id: str = "",
    error_type: str = "",
) -> None:
    """
    记录 API 调用日志。

    Args:
        request_id: 请求 ID。
        method: HTTP 方法。
        path: 请求路径。
        status_code: 响应状态码。
        is_streaming: 是否流式响应。
        ttfb_ms: 首字节耗时毫秒。
        stream_total_ms: 全流耗时毫秒。
        client_ip: 客户端 IP。
        session_id: 会话 ID。
        user_id: 用户 ID。
        error_type: 错误类型。

    Returns:
        None。
    """
    day_file = _api_dir() / f"{datetime.now().strftime('%Y-%m-%d')}.jsonl"
    ttfb = max(0.0, float(ttfb_ms))
    stream_total = max(ttfb, float(stream_total_ms))
    streaming = bool(is_streaming)
    if not streaming:
        ttfb = stream_total
    payload = {
        "ts": _utc_now(),
        "request_id": str(request_id or ""),
        "trace_id": str(request_id or ""),
        "method": str(method or ""),
        "path": str(path or ""),
        "status_code": int(status_code),
        "is_streaming": streaming,
        "ttfb_ms": round(ttfb, 2),
        "stream_total_ms": round(stream_total, 2),
        "client_ip": str(client_ip or ""),
        "session_id": str(session_id or ""),
        "user_id": str(user_id or ""),
        "error_type": str(error_type or ""),
    }
    _append_jsonl(day_file, payload)


def log_llm_call(
    session_id: str,
    model: str,
    temperature: float,
    messages: list[dict[str, Any]],
    response_content: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    latency_ms: float,
    finish_reason: str,
    error: str = "",
    trace_id: str = "",
    actor_role: str = "",
    actor_name: str = "",
) -> None:
    """
    记录 LLM 调用日志。

    Args:
        session_id: 会话 ID。
        model: 模型名称。
        temperature: 采样温度。
        messages: 请求消息列表。
        response_content: 模型输出文本。
        prompt_tokens: 输入 token 数。
        completion_tokens: 输出 token 数。
        total_tokens: 总 token 数。
        latency_ms: 调用耗时毫秒。
        finish_reason: 结束原因。
        error: 错误信息。
        trace_id: 链路追踪 ID。
        actor_role: 当前 LLM 调用发起方角色。
        actor_name: 当前 LLM 调用发起方名称。

    Returns:
        None。
    """
    sid = _safe_id(session_id)
    ensure_session_meta(sid, get_user_id())
    turn_id = get_turn_id()
    max_chars = _max_content_chars()
    req_id = get_request_id()
    resolved_trace_id = str(trace_id).strip()
    if not resolved_trace_id:
        resolved_trace_id = req_id

    messages_masked = _mask_sensitive(messages)
    include_content = _include_llm_content()
    messages_compact = (
        _compact_text(messages_masked, max_chars) if include_content else ""
    )
    response_compact = (
        _compact_text(response_content, max_chars) if include_content else ""
    )

    day_file = (
        _sessions_dir() / sid / "llm" / f"{datetime.now().strftime('%Y-%m-%d')}.jsonl"
    )
    payload = {
        "ts": _utc_now(),
        "request_id": req_id,
        "trace_id": resolved_trace_id,
        "turn_id": turn_id,
        "session_id": sid,
        "user_id": get_user_id(),
        "model": str(model or ""),
        "temperature": float(temperature),
        "messages_compact": messages_compact,
        "messages_hash": _sha256_text(messages_masked),
        "response_compact": response_compact,
        "response_hash": _sha256_text(response_content),
        "prompt_tokens": int(prompt_tokens),
        "completion_tokens": int(completion_tokens),
        "total_tokens": int(total_tokens),
        "latency_ms": round(float(latency_ms), 2),
        "finish_reason": str(finish_reason or ""),
        "error": str(error or ""),
        "actor_role": str(actor_role or ""),
        "actor_name": str(actor_name or ""),
    }
    _append_jsonl(day_file, payload)
    _bump_session_meta_count(sid, "llm_call_count")


def log_session_op(
    session_id: str,
    turn: int,
    stage: str,
    action: str,
    status: str,
    duration_ms: float = 0.0,
    input_summary: str = "",
    output_summary: str = "",
    error_type: str = "",
    notes: str = "",
) -> None:
    """
    记录会话操作事件。

    Args:
        session_id: 会话 ID。
        turn: 轮次编号。
        stage: 阶段名称。
        action: 操作名称。
        status: 操作状态。
        duration_ms: 耗时毫秒。
        input_summary: 输入摘要。
        output_summary: 输出摘要。
        error_type: 错误类型。
        notes: 备注信息。

    Returns:
        None。
    """
    sid = _safe_id(session_id)
    ensure_session_meta(sid, get_user_id())
    turn_id = get_turn_id()
    if not turn_id:
        turn_id = f"{sid}:{int(turn)}"
    day_file = (
        _sessions_dir() / sid / "ops" / f"{datetime.now().strftime('%Y-%m-%d')}.jsonl"
    )
    payload = {
        "ts": _utc_now(),
        "request_id": get_request_id(),
        "trace_id": get_request_id(),
        "session_id": sid,
        "user_id": get_user_id(),
        "turn_id": turn_id,
        "turn": int(turn),
        "stage": str(stage or ""),
        "action": str(action or ""),
        "status": str(status or ""),
        "duration_ms": round(float(duration_ms), 2),
        "input_summary": str(input_summary or ""),
        "output_summary": str(output_summary or ""),
        "error_type": str(error_type or ""),
        "notes": str(notes or ""),
    }
    _append_jsonl(day_file, payload)
    _bump_session_meta_count(sid, "op_event_count")


def log_tool_event(
    session_id: str,
    turn: int,
    tool_name: str,
    arguments: dict[str, Any],
    result_ok: bool,
    has_signal: bool,
    duration_ms: float,
    fallback_used: bool = False,
    blocked_reason: str = "",
    token_usage: dict[str, Any] | None = None,
) -> None:
    """
    记录工具调用事件。

    Args:
        session_id: 会话 ID。
        turn: 轮次编号。
        tool_name: 工具名称。
        arguments: 工具参数。
        result_ok: 调用是否成功。
        has_signal: 是否产生有效信号。
        duration_ms: 耗时毫秒。
        fallback_used: 是否使用回退策略。
        blocked_reason: 阻断原因。
        token_usage: 工具内部模型调用产生的 token 统计。

    Returns:
        None。
    """
    sid = _safe_id(session_id)
    ensure_session_meta(sid, get_user_id())
    turn_id = get_turn_id()
    if not turn_id:
        turn_id = f"{sid}:{int(turn)}"
    normalized_tokens = normalize_token_usage(token_usage)
    day_file = (
        _sessions_dir() / sid / "tools" / f"{datetime.now().strftime('%Y-%m-%d')}.jsonl"
    )
    payload = {
        "ts": _utc_now(),
        "request_id": get_request_id(),
        "trace_id": get_request_id(),
        "session_id": sid,
        "user_id": get_user_id(),
        "turn_id": turn_id,
        "turn": int(turn),
        "tool_name": str(tool_name or ""),
        "arguments": _mask_sensitive(arguments),
        "result_ok": bool(result_ok),
        "has_signal": bool(has_signal),
        "duration_ms": round(float(duration_ms), 2),
        "token_usage": normalized_tokens,
        "fallback_used": bool(fallback_used),
        "blocked_reason": str(blocked_reason or ""),
    }
    _append_jsonl(day_file, payload)
    _bump_session_meta_count(sid, "tool_event_count")
