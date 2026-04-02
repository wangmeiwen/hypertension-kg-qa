"""会话记忆 LLM 适配层。"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

import httpx

from app.a2a.agent.llm_client import LLMClient
from app.backend.services.session_memory_prompts import (
    RESOLVE_QUERY_SYSTEM_PROMPT,
    SUMMARIZE_TURN_SYSTEM_PROMPT,
)
from app.backend.services.session_memory_types import ResolvedQuery
from app.common.config import Config


logger = logging.getLogger(__name__)


def _log_memory_fail_open(
    *,
    operation: str,
    error: BaseException,
    session_id: str = "",
    assistant_message_id: int = 0,
) -> None:
    logger.warning(
        "memory_fail_open %s",
        json.dumps(
            {
                "operation": str(operation or ""),
                "error_type": type(error).__name__,
                "session_id": str(session_id or ""),
                "assistant_message_id": int(assistant_message_id or 0),
            },
            ensure_ascii=False,
        ),
    )


def _extract_json_object(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        return {}

    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except (TypeError, ValueError):
        pass

    fenced_match = re.search(r"```json\s*(\{[\s\S]*?\})\s*```", raw, re.IGNORECASE)
    if fenced_match:
        candidate = str(fenced_match.group(1) or "").strip()
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data
        except (TypeError, ValueError):
            pass

    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        candidate = raw[start : end + 1]
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data
        except (TypeError, ValueError):
            pass

    return {}


def _sanitize_list(raw: Any, max_items: int = 8, max_len: int = 160) -> list[str]:
    if not isinstance(raw, list):
        return []
    rows: list[str] = []
    for item in raw:
        text = str(item or "").strip()
        if not text:
            continue
        rows.append(text[:max_len])
        if len(rows) >= max_items:
            break
    return rows


def _fallback_resolved_query(input_text: str) -> ResolvedQuery:
    safe_input = str(input_text or "").strip()
    question = safe_input if safe_input else "请补充问题"
    return {
        "standalone_question": question,
        "intent": "direct_answer",
        "subqueries": [question],
        "must_cover": ["answer"],
        "entities": [],
        "filters": [],
        "preferred_sources": ["kg"],
        "carry_over_context": [],
    }


def _normalize_resolved_query(data: Any, input_text: str) -> ResolvedQuery:
    if not isinstance(data, dict):
        return _fallback_resolved_query(input_text)

    standalone_question = str(data.get("standalone_question") or "").strip()
    if not standalone_question:
        standalone_question = str(input_text or "").strip() or "请补充问题"

    subqueries = _sanitize_list(data.get("subqueries"))
    if not subqueries:
        subqueries = [standalone_question]

    preferred_sources = []
    for source in _sanitize_list(data.get("preferred_sources"), max_items=2, max_len=8):
        if source in {"kg", "rag"} and source not in preferred_sources:
            preferred_sources.append(source)
    if not preferred_sources:
        preferred_sources = ["kg"]

    return {
        "standalone_question": standalone_question[:300],
        "intent": str(data.get("intent") or "direct_answer").strip()[:64] or "direct_answer",
        "subqueries": subqueries,
        "must_cover": _sanitize_list(data.get("must_cover")),
        "entities": _sanitize_list(data.get("entities")),
        "filters": _sanitize_list(data.get("filters")),
        "preferred_sources": preferred_sources,
        "carry_over_context": _sanitize_list(data.get("carry_over_context")),
    }


class SessionMemoryLLM:
    """会话记忆双 LLM 统一入口。"""

    def __init__(self, conf: Config | None = None, llm_client: LLMClient | None = None) -> None:
        self.conf = conf if conf is not None else Config()
        self.model_name = str(self.conf.SESSION_MEMORY_MODEL_NAME or self.conf.MODEL_NAME)
        self.temperature = 0.1

        if llm_client is not None:
            self.client = llm_client
        else:
            llm_conf = Config()
            llm_conf.MODEL_NAME = self.model_name
            self.client = LLMClient(llm_conf)

    async def _call_json(
        self,
        system_prompt: str,
        payload: dict[str, Any],
        *,
        operation: str,
        session_id: str = "",
        assistant_message_id: int = 0,
    ) -> dict[str, Any]:
        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False),
            },
        ]
        try:
            response = await self.client.chat_with_tokens_async(
                messages=messages,
                temperature=self.temperature,
                timeout=90,
            )
            return _extract_json_object(str(getattr(response, "content", "") or ""))
        except (
            httpx.ReadTimeout,
            httpx.HTTPError,
            asyncio.TimeoutError,
        ) as exc:
            _log_memory_fail_open(
                operation=operation,
                error=exc,
                session_id=session_id,
                assistant_message_id=assistant_message_id,
            )
            return {}

    async def resolve_query(
        self,
        *,
        input_text: str,
        recent_messages: list[dict[str, Any]],
        summary: dict[str, Any],
        retrieval_summary: dict[str, Any],
        session_id: str = "",
    ) -> ResolvedQuery:
        payload = {
            "input_text": str(input_text or ""),
            "recent_messages": recent_messages,
            "summary": summary,
            "retrieval_summary": retrieval_summary,
        }
        try:
            data = await self._call_json(
                RESOLVE_QUERY_SYSTEM_PROMPT,
                payload,
                operation="resolve_query",
                session_id=session_id,
            )
            return _normalize_resolved_query(data, input_text)
        except (
            RuntimeError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            OSError,
            ImportError,
            httpx.ReadTimeout,
            httpx.HTTPError,
            asyncio.TimeoutError,
        ) as exc:
            _log_memory_fail_open(operation="resolve_query", error=exc, session_id=session_id)
            return _fallback_resolved_query(input_text)

    async def summarize_turn(
        self,
        *,
        previous_summary: dict[str, Any],
        previous_retrieval_summary: dict[str, Any],
        messages_delta: list[dict[str, Any]],
        turn_artifacts: dict[str, Any],
        session_id: str = "",
        assistant_message_id: int = 0,
    ) -> dict[str, Any]:
        payload = {
            "previous_summary": previous_summary,
            "previous_retrieval_summary": previous_retrieval_summary,
            "messages_delta": messages_delta,
            "turn_artifacts": turn_artifacts,
        }
        try:
            data = await self._call_json(
                SUMMARIZE_TURN_SYSTEM_PROMPT,
                payload,
                operation="summarize_turn",
                session_id=session_id,
                assistant_message_id=assistant_message_id,
            )
            if not isinstance(data, dict):
                return {
                    "summary": previous_summary,
                    "retrieval_summary": previous_retrieval_summary,
                }
            return {
                "summary": data.get("summary") if isinstance(data.get("summary"), dict) else previous_summary,
                "retrieval_summary": data.get("retrieval_summary")
                if isinstance(data.get("retrieval_summary"), dict)
                else previous_retrieval_summary,
            }
        except (
            RuntimeError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            OSError,
            ImportError,
            httpx.ReadTimeout,
            httpx.HTTPError,
            asyncio.TimeoutError,
        ) as exc:
            _log_memory_fail_open(
                operation="summarize_turn",
                error=exc,
                session_id=session_id,
                assistant_message_id=assistant_message_id,
            )
            return {
                "summary": previous_summary,
                "retrieval_summary": previous_retrieval_summary,
            }
