"""LLM 客户端封装模块。

本模块统一封装文本与视觉模型调用，提供响应内容、Token 统计与调用日志记录能力。

主要类：
- LLMResponse: 标准化模型响应结构
- LLMClient: LLM 调用客户端
"""

import json
import asyncio

import time

from typing import Any, Optional

import httpx
import requests  # type: ignore[import-untyped]

from app.common.config import Config

from app.common.langsmith_tracing import set_trace_metadata, traceable
from app.common.feature_flags import get_feature_flag

from app.common.session_logger import get_session_id, log_llm_call


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


class LLMClient:

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

    def chat(
        self,
        messages: list[dict[str, Any]],
        temperature: float = 0.1,
        timeout: int = 60,
    ) -> str:
        """兼容旧接口，只返回内容字符串"""

        response = self.chat_with_tokens(messages, temperature, timeout)

        # 兼容不同返回类型，避免调用方因返回值形态差异导致崩溃

        if isinstance(response, str):

            return response

        if isinstance(response, dict):

            return str(response.get("content", "") or "")

        content = getattr(response, "content", "")

        return str(content or "")

    async def chat_async(
        self,
        messages: list[dict[str, Any]],
        temperature: float = 0.1,
        timeout: int = 60,
    ) -> str:
        """异步兼容接口，只返回内容字符串"""

        response = await self.chat_with_tokens_async(messages, temperature, timeout)

        if isinstance(response, str):

            return response

        if isinstance(response, dict):

            return str(response.get("content", "") or "")

        content = getattr(response, "content", "")

        return str(content or "")

    @traceable(name="llm_client.chat_with_tokens", run_type="llm")
    def chat_with_tokens(
        self,
        messages: list[dict[str, Any]],
        temperature: float = 0.1,
        timeout: int = 60,
    ) -> LLMResponse:
        """调用大模型并返回内容和 token 使用量"""

        set_trace_metadata(
            {
                "session_id": get_session_id(),
                "model": self.conf.MODEL_NAME,
                "temperature": temperature,
                "message_count": len(messages),
            },
            tags=["llm", "requests_chat_completions"],
        )

        payload = {
            "model": self.conf.MODEL_NAME,
            "messages": messages,
            "temperature": temperature,
        }

        started = time.perf_counter()

        try:

            resp = requests.post(
                self.endpoint,
                headers=self.headers,
                data=json.dumps(payload),
                timeout=timeout,
            )

            resp.raise_for_status()

            data = resp.json()

        except (
            RuntimeError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            OSError,
            ImportError,
        ) as exc:

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
                error=str(exc),
            )

            raise

        # 提取响应内容

        choices = data.get("choices") or []

        content = ""

        finish_reason = ""

        if choices:

            finish_reason = str(choices[0].get("finish_reason", "") or "")

            message = choices[0].get("message", "")

            if isinstance(message, dict):

                content = message.get("content", "") or ""

            else:

                content = str(message or "")

            # 兼容部分模型返回 list[dict] 的 content 结构

            if isinstance(content, list):

                parts = []

                for item in content:

                    if isinstance(item, dict):

                        parts.append(str(item.get("text", "") or ""))

                    else:

                        parts.append(str(item))

                content = "".join(parts)

        # 提取 token 使用量

        usage = data.get("usage", {})

        prompt_tokens = usage.get("prompt_tokens", 0)

        completion_tokens = usage.get("completion_tokens", 0)

        total_tokens = usage.get("total_tokens", 0)

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
    ) -> LLMResponse:
        """异步调用大模型并返回内容和 token 使用量"""


        set_trace_metadata(
            {
                "session_id": get_session_id(),
                "model": self.conf.MODEL_NAME,
                "temperature": temperature,
                "message_count": len(messages),
            },
            tags=["llm", "httpx_async_chat_completions"],
        )

        payload = {
            "model": self.conf.MODEL_NAME,
            "messages": messages,
            "temperature": temperature,
        }

        started = time.perf_counter()

        try:

            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    self.endpoint,
                    headers=self.headers,
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()

        except (
            RuntimeError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            OSError,
            ImportError,
            httpx.HTTPError,
        ) as exc:

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
                error=str(exc),
            )

            raise

        choices = data.get("choices") or []

        content = ""

        finish_reason = ""

        if choices:

            finish_reason = str(choices[0].get("finish_reason", "") or "")

            message = choices[0].get("message", "")

            if isinstance(message, dict):

                content = message.get("content", "") or ""

            else:

                content = str(message or "")

            if isinstance(content, list):

                parts = []

                for item in content:

                    if isinstance(item, dict):

                        parts.append(str(item.get("text", "") or ""))

                    else:

                        parts.append(str(item))

                content = "".join(parts)

        usage = data.get("usage", {})

        prompt_tokens = usage.get("prompt_tokens", 0)

        completion_tokens = usage.get("completion_tokens", 0)

        total_tokens = usage.get("total_tokens", 0)

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
        )

        return LLMResponse(
            content=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            finish_reason=finish_reason,
            raw_response=data,
        )

    def chat_with_vision(
        self,
        messages: list[dict[str, Any]],
        image_data: Optional[str] = None,
        temperature: float = 0.1,
        timeout: int = 120,
    ) -> LLMResponse:
        """调用支持视觉的大模型，支持图片输入"""

        if image_data:

            for msg in messages:

                if msg.get("role") == "user":

                    original_content = msg.get("content", "")

                    msg["content"] = [
                        {"type": "text", "text": original_content},
                        {"type": "image_url", "image_url": {"url": image_data}},
                    ]

                    break

        return self.chat_with_tokens(messages, temperature, timeout)

    async def chat_with_vision_async(
        self,
        messages: list[dict[str, Any]],
        image_data: Optional[str] = None,
        temperature: float = 0.1,
        timeout: int = 120,
    ) -> LLMResponse:
        """异步调用支持视觉的大模型，支持图片输入"""

        if image_data:

            for msg in messages:

                if msg.get("role") == "user":

                    original_content = msg.get("content", "")

                    msg["content"] = [
                        {"type": "text", "text": original_content},
                        {"type": "image_url", "image_url": {"url": image_data}},
                    ]

                    break

        return await self.chat_with_tokens_async(messages, temperature, timeout)



