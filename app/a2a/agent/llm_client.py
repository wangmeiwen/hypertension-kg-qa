"""LLM 客户端封装模块。

本模块统一封装文本与视觉模型调用，提供响应内容、Token 统计与调用日志记录能力。

主要类：
- LLMResponse: 标准化模型响应结构
- LLMClient: LLM 调用客户端
"""

import asyncio
import copy
import hashlib
import json
import logging
import time

from typing import Any, AsyncIterator, Optional

import httpx
import requests  # type: ignore[import-untyped]

from app.common.config import Config

from app.common.langsmith_tracing import set_trace_metadata, traceable
from app.common.feature_flags import get_feature_flag

from app.common.session_logger import get_request_id, get_session_id, log_llm_call


logger = logging.getLogger(__name__)


class LLMRetryExhaustedError(TimeoutError):
    """LLM 超时重试达到上限后抛出的聚合异常。"""


def _hash_payload(payload: Any) -> str:
    """计算请求载荷哈希，便于日志比对与去重排查。"""
    try:
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        text = str(payload)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _compact_payload(payload: Any, max_chars: int = 300) -> str:
    """将载荷压缩为短文本，避免日志过大。"""
    try:
        text = json.dumps(payload, ensure_ascii=False)
    except (TypeError, ValueError):
        text = str(payload)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "...[truncated]"


def _log_server_llm_event(event: str, payload: dict[str, Any]) -> None:
    """记录标准化 LLM 服务端事件日志。"""
    logger.info(
        "llm_server_event %s",
        json.dumps(
            {
                "event": str(event or ""),
                "request_id": get_request_id(),
                "session_id": get_session_id(),
                **payload,
            },
            ensure_ascii=False,
        ),
    )


def _format_error_text(exc: BaseException) -> str:
    """将异常格式化为稳定可读的错误文本。"""
    text = str(exc or "").strip()
    return text or type(exc).__name__


def _is_retryable_timeout_error(exc: BaseException) -> bool:
    """判断异常是否属于可重试的超时类错误。"""
    return isinstance(
        exc,
        (
            TimeoutError,
            requests.Timeout,
            httpx.TimeoutException,
        ),
    )


class LLMResponse:
    """LLM 响应结果，包含内容和 token 使用量"""

    def __init__(
        self,
        content: str = "",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        finish_reason: str = "",
        raw_response: dict[str, Any] | None = None,
    ):
        """初始化标准化 LLM 响应对象。

        Args:
            content: 模型返回的文本内容。
            prompt_tokens: 输入 token 数。
            completion_tokens: 输出 token 数。
            total_tokens: 总 token 数。
            finish_reason: 生成结束原因。
            raw_response: 原始接口返回体。
        """
        self.content = content

        self.prompt_tokens = prompt_tokens

        self.completion_tokens = completion_tokens

        self.total_tokens = total_tokens

        self.finish_reason = finish_reason

        self.raw_response = raw_response or {}

    def __str__(self) -> str:
        """返回响应内容字符串，便于直接打印对象。"""
        return str(self.content or "")

    def __repr__(self) -> str:
        """返回对象的详细表示。

        Returns:
            str: 便于调试日志查看的摘要字符串。
        """
        return f"LLMResponse(content={self.content[:100]!r}..., tokens={self.total_tokens})"


class LLMStreamEvent:
    """流式 LLM 事件，统一表达增量文本与收尾统计。"""

    def __init__(
        self,
        *,
        type: str,
        delta: str = "",
        finish_reason: str = "",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        raw_response: dict[str, Any] | None = None,
    ) -> None:
        self.type = str(type or "").strip() or "delta"
        self.delta = str(delta or "")
        self.finish_reason = str(finish_reason or "")
        self.prompt_tokens = int(prompt_tokens or 0)
        self.completion_tokens = int(completion_tokens or 0)
        self.total_tokens = int(total_tokens or 0)
        self.raw_response = raw_response or {}


def _normalize_text_content(value: Any) -> str:
    """兼容字符串与 OpenAI content 列表结构。"""
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
            elif item is not None:
                parts.append(str(item))
        return "".join(parts)
    if value is None:
        return ""
    return str(value)


def _extract_message_content(message: Any) -> str:
    """从 message/delta 结构中提取文本内容。"""
    if isinstance(message, dict):
        if "content" in message:
            return _normalize_text_content(message.get("content"))
        if "text" in message:
            return _normalize_text_content(message.get("text"))
    return _normalize_text_content(message)


def _prepare_vision_messages(messages: list[dict[str, Any]], image_data: Optional[str] = None) -> list[dict[str, Any]]:
    """为视觉模型构建独立消息副本，避免原地修改调用方数据。"""
    prepared = copy.deepcopy(messages)
    if not image_data:
        return prepared

    for msg in prepared:
        if msg.get("role") != "user":
            continue
        original_content = msg.get("content", "")
        msg["content"] = [
            {"type": "text", "text": _extract_message_content(original_content)},
            {"type": "image_url", "image_url": {"url": image_data}},
        ]
        break
    return prepared


def _extract_content_and_finish_reason(data: dict[str, Any]) -> tuple[str, str]:
    """从 chat completions 响应中提取正文与结束原因。"""
    choices = data.get("choices") or []
    if not choices:
        return "", ""

    choice = choices[0] if isinstance(choices[0], dict) else {}
    finish_reason = str(choice.get("finish_reason", "") or "")
    message = choice.get("message", "")
    return _extract_message_content(message), finish_reason


def _extract_usage_dict(data: dict[str, Any]) -> tuple[int, int, int]:
    """从响应中提取 token 统计。"""
    usage = data.get("usage", {})
    usage_dict = usage if isinstance(usage, dict) else {}
    return (
        int(usage_dict.get("prompt_tokens", 0) or 0),
        int(usage_dict.get("completion_tokens", 0) or 0),
        int(usage_dict.get("total_tokens", 0) or 0),
    )


class LLMClient:
    """统一封装文本与视觉模型调用能力。"""

    def __init__(self, conf: Config | None = None):
        """初始化 LLM 客户端并准备调用端点与请求头。

        Args:
            conf: 系统配置对象。
        """
        self.conf = conf or Config()

        self._validate_config()

        self.endpoint = self.conf.MODEL_BASE_URL.rstrip("/") + "/chat/completions"

        self.headers = {
            "Authorization": f"Bearer {self.conf.MODEL_API_KEY}",
            "Content-Type": "application/json",
        }

    def _validate_config(self) -> None:
        """校验 LLM 必填配置，缺失时快速失败并给出明确错误信息"""

        missing = []

        if not self.conf.MODEL_API_KEY:

            missing.append("MODEL_API_KEY")

        if not self.conf.MODEL_BASE_URL:

            missing.append("MODEL_BASE_URL")

        if not self.conf.MODEL_NAME:

            missing.append("MODEL_NAME")

        if missing:

            raise RuntimeError(
                f"LLM 配置缺失: {', '.join(missing)}。"
                f"请在 .env 文件中设置以下环境变量: {', '.join(missing)}"
            )

    @traceable(name="llm_client.chat_with_tokens", run_type="llm")
    def chat_with_tokens(
        self,
        messages: list[dict[str, Any]],
        temperature: float = 0.1,
        timeout: int = 60,
        actor_role: str | None = None,
        actor_name: str | None = None,
    ) -> LLMResponse:
        """调用大模型并返回内容和 token 使用量"""

        set_trace_metadata(
            {
                "session_id": get_session_id(),
                "model": self.conf.MODEL_NAME,
                "temperature": temperature,
                "message_count": len(messages),
                "actor_role": str(actor_role or ""),
                "actor_name": str(actor_name or ""),
            },
            tags=["llm", "requests_chat_completions"],
        )

        payload = {
            "model": self.conf.MODEL_NAME,
            "messages": messages,
            "temperature": temperature,
        }
        request_timeout = max(1, int(timeout or self.conf.LLM_REQUEST_TIMEOUT_SECONDS))
        max_attempts = max(1, int(self.conf.LLM_REQUEST_RETRY_MAX_ATTEMPTS or 1))
        message_hash = _hash_payload(messages)
        errors: list[str] = []
        data: dict[str, Any] = {}

        for attempt in range(1, max_attempts + 1):
            _log_server_llm_event(
                "llm_request_start",
                {
                    "model": self.conf.MODEL_NAME,
                    "temperature": float(temperature),
                    "message_count": len(messages),
                    "messages_compact": _compact_payload(messages),
                    "messages_hash": message_hash,
                    "actor_role": str(actor_role or ""),
                    "actor_name": str(actor_name or ""),
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                    "timeout_seconds": request_timeout,
                },
            )

            started = time.perf_counter()

            try:

                resp = requests.post(
                    self.endpoint,
                    headers=self.headers,
                    data=json.dumps(payload),
                    timeout=request_timeout,
                )

                resp.raise_for_status()

                data = resp.json()
                break

            except (
                RuntimeError,
                ValueError,
                TypeError,
                KeyError,
                AttributeError,
                OSError,
                ImportError,
                requests.RequestException,
                TimeoutError,
            ) as exc:
                error_text = _format_error_text(exc)
                errors.append(f"attempt={attempt}, error_type={type(exc).__name__}, error={error_text}")

                _log_server_llm_event(
                    "llm_request_error",
                    {
                        "model": self.conf.MODEL_NAME,
                        "temperature": float(temperature),
                        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                        "error_type": type(exc).__name__,
                        "error": error_text,
                        "messages_hash": message_hash,
                        "actor_role": str(actor_role or ""),
                        "actor_name": str(actor_name or ""),
                        "attempt": attempt,
                        "max_attempts": max_attempts,
                        "timeout_seconds": request_timeout,
                        "will_retry": bool(_is_retryable_timeout_error(exc) and attempt < max_attempts),
                    },
                )

                log_llm_call(
                    session_id=get_session_id(),
                    model=self.conf.MODEL_NAME,
                    temperature=temperature,
                    messages=messages,
                    response_content="",
                    prompt_tokens=0,
                    completion_tokens=0,
                    total_tokens=0,
                    latency_ms=(time.perf_counter() - started) * 1000,
                    finish_reason="",
                    error=error_text,
                    actor_role=str(actor_role or ""),
                    actor_name=str(actor_name or ""),
                )

                if _is_retryable_timeout_error(exc) and attempt < max_attempts:
                    continue
                if _is_retryable_timeout_error(exc):
                    raise LLMRetryExhaustedError(
                        "llm_request_retry_exhausted: "
                        f"attempts={max_attempts}; timeout_seconds={request_timeout}; "
                        f"errors={' | '.join(errors)}"
                    ) from exc
                raise

        # 提取响应内容

        content, finish_reason = _extract_content_and_finish_reason(data)
        prompt_tokens, completion_tokens, total_tokens = _extract_usage_dict(data)

        log_llm_call(
            session_id=get_session_id(),
            model=self.conf.MODEL_NAME,
            temperature=temperature,
            messages=messages,
            response_content=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            latency_ms=(time.perf_counter() - started) * 1000,
            finish_reason=finish_reason,
            actor_role=str(actor_role or ""),
            actor_name=str(actor_name or ""),
        )

        _log_server_llm_event(
            "llm_request_success",
            {
                "model": self.conf.MODEL_NAME,
                "temperature": float(temperature),
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                "prompt_tokens": int(prompt_tokens),
                "completion_tokens": int(completion_tokens),
                "total_tokens": int(total_tokens),
                "finish_reason": str(finish_reason or ""),
                "response_compact": _compact_payload(content),
                "response_hash": _hash_payload(content),
                "actor_role": str(actor_role or ""),
                "actor_name": str(actor_name or ""),
            },
        )

        return LLMResponse(
            content=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            finish_reason=finish_reason,
            raw_response=data,
        )

    @traceable(name="llm_client.chat_with_tokens_async", run_type="llm")
    async def chat_with_tokens_async(
        self,
        messages: list[dict[str, Any]],
        temperature: float = 0.1,
        timeout: int = 60,
        actor_role: str | None = None,
        actor_name: str | None = None,
    ) -> LLMResponse:
        """异步调用大模型并返回内容和 token 使用量"""
        set_trace_metadata(
            {
                "session_id": get_session_id(),
                "model": self.conf.MODEL_NAME,
                "temperature": temperature,
                "message_count": len(messages),
                "actor_role": str(actor_role or ""),
                "actor_name": str(actor_name or ""),
            },
            tags=["llm", "httpx_async_chat_completions"],
        )

        payload = {
            "model": self.conf.MODEL_NAME,
            "messages": messages,
            "temperature": temperature,
        }
        request_timeout = max(1, int(timeout or self.conf.LLM_REQUEST_TIMEOUT_SECONDS))
        max_attempts = max(1, int(self.conf.LLM_REQUEST_RETRY_MAX_ATTEMPTS or 1))
        message_hash = _hash_payload(messages)
        errors: list[str] = []
        data: dict[str, Any] = {}

        for attempt in range(1, max_attempts + 1):
            _log_server_llm_event(
                "llm_request_start",
                {
                    "model": self.conf.MODEL_NAME,
                    "temperature": float(temperature),
                    "message_count": len(messages),
                    "messages_compact": _compact_payload(messages),
                    "messages_hash": message_hash,
                    "actor_role": str(actor_role or ""),
                    "actor_name": str(actor_name or ""),
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                    "timeout_seconds": request_timeout,
                },
            )

            started = time.perf_counter()

            try:
                async with httpx.AsyncClient(timeout=request_timeout) as client:
                    resp = await client.post(
                        self.endpoint,
                        headers=self.headers,
                        json=payload,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                break

            except (
                RuntimeError,
                ValueError,
                TypeError,
                KeyError,
                AttributeError,
                OSError,
                ImportError,
                httpx.HTTPError,
                TimeoutError,
            ) as exc:
                error_text = _format_error_text(exc)
                errors.append(f"attempt={attempt}, error_type={type(exc).__name__}, error={error_text}")
                should_retry = _is_retryable_timeout_error(exc) and attempt < max_attempts

                _log_server_llm_event(
                    "llm_request_error",
                    {
                        "model": self.conf.MODEL_NAME,
                        "temperature": float(temperature),
                        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                        "error_type": type(exc).__name__,
                        "error": error_text,
                        "messages_hash": message_hash,
                        "actor_role": str(actor_role or ""),
                        "actor_name": str(actor_name or ""),
                        "attempt": attempt,
                        "max_attempts": max_attempts,
                        "timeout_seconds": request_timeout,
                        "will_retry": bool(should_retry),
                    },
                )

                log_llm_call(
                    session_id=get_session_id(),
                    model=self.conf.MODEL_NAME,
                    temperature=temperature,
                    messages=messages,
                    response_content="",
                    prompt_tokens=0,
                    completion_tokens=0,
                    total_tokens=0,
                    latency_ms=(time.perf_counter() - started) * 1000,
                    finish_reason="",
                    error=error_text,
                    actor_role=str(actor_role or ""),
                    actor_name=str(actor_name or ""),
                )

                if should_retry:
                    continue
                if _is_retryable_timeout_error(exc):
                    raise LLMRetryExhaustedError(
                        "llm_request_retry_exhausted: "
                        f"attempts={max_attempts}; timeout_seconds={request_timeout}; "
                        f"errors={' | '.join(errors)}"
                    ) from exc
                raise

        else:
            raise LLMRetryExhaustedError(
                "llm_request_retry_exhausted: "
                f"attempts={max_attempts}; timeout_seconds={request_timeout}; "
                f"errors={' | '.join(errors)}"
            )

        content, finish_reason = _extract_content_and_finish_reason(data)
        prompt_tokens, completion_tokens, total_tokens = _extract_usage_dict(data)

        log_llm_call(
            session_id=get_session_id(),
            model=self.conf.MODEL_NAME,
            temperature=temperature,
            messages=messages,
            response_content=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            latency_ms=(time.perf_counter() - started) * 1000,
            finish_reason=finish_reason,
            actor_role=str(actor_role or ""),
            actor_name=str(actor_name or ""),
        )

        _log_server_llm_event(
            "llm_request_success",
            {
                "model": self.conf.MODEL_NAME,
                "temperature": float(temperature),
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                "prompt_tokens": int(prompt_tokens),
                "completion_tokens": int(completion_tokens),
                "total_tokens": int(total_tokens),
                "finish_reason": str(finish_reason or ""),
                "response_compact": _compact_payload(content),
                "response_hash": _hash_payload(content),
                "actor_role": str(actor_role or ""),
                "actor_name": str(actor_name or ""),
            },
        )

        return LLMResponse(
            content=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            finish_reason=finish_reason,
            raw_response=data,
        )

    async def stream_chat_with_tokens_async(
        self,
        messages: list[dict[str, Any]],
        temperature: float = 0.1,
        timeout: int = 60,
        actor_role: str | None = None,
        actor_name: str | None = None,
    ) -> AsyncIterator[LLMStreamEvent]:
        """异步流式调用大模型并持续产出增量文本。"""
        set_trace_metadata(
            {
                "session_id": get_session_id(),
                "model": self.conf.MODEL_NAME,
                "temperature": temperature,
                "message_count": len(messages),
                "actor_role": str(actor_role or ""),
                "actor_name": str(actor_name or ""),
            },
            tags=["llm", "httpx_async_chat_completions_stream"],
        )

        payload = {
            "model": self.conf.MODEL_NAME,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        request_timeout = max(1, int(timeout or self.conf.LLM_REQUEST_TIMEOUT_SECONDS))
        max_attempts = max(1, int(self.conf.LLM_REQUEST_RETRY_MAX_ATTEMPTS or 1))
        message_hash = _hash_payload(messages)
        errors: list[str] = []
        full_chunks: list[str] = []
        finish_reason = ""
        prompt_tokens = 0
        completion_tokens = 0
        total_tokens = 0
        raw_last_chunk: dict[str, Any] = {}
        started = time.perf_counter()

        for attempt in range(1, max_attempts + 1):
            _log_server_llm_event(
                "llm_stream_request_start",
                {
                    "model": self.conf.MODEL_NAME,
                    "temperature": float(temperature),
                    "message_count": len(messages),
                    "messages_compact": _compact_payload(messages),
                    "messages_hash": message_hash,
                    "actor_role": str(actor_role or ""),
                    "actor_name": str(actor_name or ""),
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                    "timeout_seconds": request_timeout,
                },
            )
            started = time.perf_counter()

            try:
                async with httpx.AsyncClient(timeout=request_timeout) as client:
                    async with client.stream(
                        "POST",
                        self.endpoint,
                        headers=self.headers,
                        json=payload,
                    ) as resp:
                        resp.raise_for_status()
                        async for raw_line in resp.aiter_lines():
                            line = str(raw_line or "").strip()
                            if not line:
                                continue
                            if line.startswith("data:"):
                                line = line[5:].strip()
                            if not line:
                                continue
                            if line == "[DONE]":
                                break

                            try:
                                data = json.loads(line)
                            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                                raise ValueError(f"invalid_stream_chunk:{line[:120]}") from exc

                            raw_last_chunk = data if isinstance(data, dict) else {}
                            chunk_prompt_tokens, chunk_completion_tokens, chunk_total_tokens = _extract_usage_dict(raw_last_chunk)
                            if chunk_prompt_tokens or chunk_completion_tokens or chunk_total_tokens:
                                prompt_tokens = chunk_prompt_tokens
                                completion_tokens = chunk_completion_tokens
                                total_tokens = chunk_total_tokens

                            choices = raw_last_chunk.get("choices") or []
                            if not choices:
                                continue
                            choice = choices[0] if isinstance(choices[0], dict) else {}
                            finish_reason = str(choice.get("finish_reason", "") or finish_reason or "")
                            delta_text = _extract_message_content(choice.get("delta"))
                            if not delta_text:
                                continue
                            full_chunks.append(delta_text)
                            yield LLMStreamEvent(
                                type="delta",
                                delta=delta_text,
                                finish_reason=finish_reason,
                                prompt_tokens=prompt_tokens,
                                completion_tokens=completion_tokens,
                                total_tokens=total_tokens,
                                raw_response=raw_last_chunk,
                            )
                break

            except (
                RuntimeError,
                ValueError,
                TypeError,
                KeyError,
                AttributeError,
                OSError,
                ImportError,
                httpx.HTTPError,
                TimeoutError,
            ) as exc:
                error_text = _format_error_text(exc)
                errors.append(f"attempt={attempt}, error_type={type(exc).__name__}, error={error_text}")
                should_retry = _is_retryable_timeout_error(exc) and attempt < max_attempts

                _log_server_llm_event(
                    "llm_stream_request_error",
                    {
                        "model": self.conf.MODEL_NAME,
                        "temperature": float(temperature),
                        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                        "error_type": type(exc).__name__,
                        "error": error_text,
                        "messages_hash": message_hash,
                        "actor_role": str(actor_role or ""),
                        "actor_name": str(actor_name or ""),
                        "attempt": attempt,
                        "max_attempts": max_attempts,
                        "timeout_seconds": request_timeout,
                        "will_retry": bool(should_retry),
                    },
                )

                log_llm_call(
                    session_id=get_session_id(),
                    model=self.conf.MODEL_NAME,
                    temperature=temperature,
                    messages=messages,
                    response_content="".join(full_chunks),
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    latency_ms=(time.perf_counter() - started) * 1000,
                    finish_reason=finish_reason,
                    error=error_text,
                    actor_role=str(actor_role or ""),
                    actor_name=str(actor_name or ""),
                )

                if should_retry:
                    continue
                if _is_retryable_timeout_error(exc):
                    raise LLMRetryExhaustedError(
                        "llm_request_retry_exhausted: "
                        f"attempts={max_attempts}; timeout_seconds={request_timeout}; "
                        f"errors={' | '.join(errors)}"
                    ) from exc
                raise
        else:
            raise LLMRetryExhaustedError(
                "llm_request_retry_exhausted: "
                f"attempts={max_attempts}; timeout_seconds={request_timeout}; "
                f"errors={' | '.join(errors)}"
            )

        content = "".join(full_chunks)
        log_llm_call(
            session_id=get_session_id(),
            model=self.conf.MODEL_NAME,
            temperature=temperature,
            messages=messages,
            response_content=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            latency_ms=(time.perf_counter() - started) * 1000,
            finish_reason=finish_reason,
            actor_role=str(actor_role or ""),
            actor_name=str(actor_name or ""),
        )

        _log_server_llm_event(
            "llm_stream_request_success",
            {
                "model": self.conf.MODEL_NAME,
                "temperature": float(temperature),
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                "prompt_tokens": int(prompt_tokens),
                "completion_tokens": int(completion_tokens),
                "total_tokens": int(total_tokens),
                "finish_reason": str(finish_reason or ""),
                "response_compact": _compact_payload(content),
                "response_hash": _hash_payload(content),
                "actor_role": str(actor_role or ""),
                "actor_name": str(actor_name or ""),
            },
        )

        yield LLMStreamEvent(
            type="done",
            finish_reason=finish_reason,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            raw_response=raw_last_chunk,
        )

    def chat_with_vision(
        self,
        messages: list[dict[str, Any]],
        image_data: Optional[str] = None,
        temperature: float = 0.1,
        timeout: int = 120,
        actor_role: str | None = None,
        actor_name: str | None = None,
    ) -> LLMResponse:
        """调用支持视觉的大模型，支持图片输入"""
        prepared_messages = _prepare_vision_messages(messages, image_data)
        return self.chat_with_tokens(
            prepared_messages,
            temperature,
            timeout,
            actor_role=actor_role,
            actor_name=actor_name,
        )

    async def chat_with_vision_async(
        self,
        messages: list[dict[str, Any]],
        image_data: Optional[str] = None,
        temperature: float = 0.1,
        timeout: int = 120,
        actor_role: str | None = None,
        actor_name: str | None = None,
    ) -> LLMResponse:
        """异步调用支持视觉的大模型，支持图片输入"""
        prepared_messages = _prepare_vision_messages(messages, image_data)
        return await self.chat_with_tokens_async(
            prepared_messages,
            temperature,
            timeout,
            actor_role=actor_role,
            actor_name=actor_name,
        )

    async def stream_chat_with_vision_async(
        self,
        messages: list[dict[str, Any]],
        image_data: Optional[str] = None,
        temperature: float = 0.1,
        timeout: int = 120,
        actor_role: str | None = None,
        actor_name: str | None = None,
    ) -> AsyncIterator[LLMStreamEvent]:
        """异步流式调用支持视觉的大模型。"""
        prepared_messages = _prepare_vision_messages(messages, image_data)
        async for event in self.stream_chat_with_tokens_async(
            prepared_messages,
            temperature,
            timeout,
            actor_role=actor_role,
            actor_name=actor_name,
        ):
            yield event



