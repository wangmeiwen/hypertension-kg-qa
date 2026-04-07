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

_ALLOWED_PREFERRED_SOURCES = {"memory", "kg", "rag", "direct", "capability"}
_ALLOWED_QUESTION_MODES = {
    "repeat_medical_question",
    "memory_lookup",
    "capability_query",
    "direct_query",
    "medical_new_question",
    "medical_gap_followup",
}


def _json_text_len(value: Any) -> int:
    """执行json_text_len流程并返回结果。
    
    Args:
        value: 待处理输入值。
    
    Returns:
        int: 数值结果。
    """
    try:
        return len(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError):
        return len(str(value or ""))


def _sanitize_projection_metrics(turn_artifacts: dict[str, Any]) -> tuple[int, int, str]:
    """清洗输入并返回可用结果。
    
    Args:
        turn_artifacts: 函数输入参数。
    
    Returns:
        tuple[int, int, str]: 执行结果。
    """
    source = turn_artifacts if isinstance(turn_artifacts, dict) else {}
    count = 0
    chars = 0
    try:
        count = int(source.get("tool_observations_count") or 0)
    except (TypeError, ValueError):
        count = 0
    try:
        chars = int(source.get("tool_observations_chars") or 0)
    except (TypeError, ValueError):
        chars = 0
    mode = str(source.get("projection_mode") or "legacy_raw")
    return max(0, count), max(0, chars), mode


def _log_memory_llm_input_metrics(
    *,
    operation: str,
    summary: dict[str, Any],
    retrieval_summary: dict[str, Any],
    messages_delta_count: int,
    assistant_reply_chars: int,
    tool_observations_count: int,
    tool_observations_chars: int,
    projection_mode: str,
    session_id: str = "",
) -> None:
    """执行log_memory_llm_input_metrics流程并返回结果。
    
    Args:
        operation: 函数输入参数。
        summary: 函数输入参数。
        retrieval_summary: 函数输入参数。
        messages_delta_count: 函数输入参数。
        assistant_reply_chars: 函数输入参数。
        tool_observations_count: 函数输入参数。
        tool_observations_chars: 函数输入参数。
        projection_mode: 函数输入参数。
        session_id: 会话标识。
    
    Returns:
        None: 无返回值。
    """
    logger.info(
        "memory_llm_input_metrics %s",
        json.dumps(
            {
                "operation": str(operation or ""),
                "session_id": str(session_id or ""),
                "summary_chars": _json_text_len(summary),
                "retrieval_summary_chars": _json_text_len(retrieval_summary),
                "messages_delta_count": int(messages_delta_count),
                "assistant_reply_chars": int(assistant_reply_chars),
                "tool_observations_count": int(tool_observations_count),
                "tool_observations_chars": int(tool_observations_chars),
                "projection_mode": str(projection_mode or "legacy_raw"),
            },
            ensure_ascii=False,
        ),
    )


def _log_memory_fail_open(
    *,
    operation: str,
    error: BaseException,
    session_id: str = "",
    assistant_message_id: int = 0,
) -> None:
    """执行log_memory_fail_open流程并返回结果。
    
    Args:
        operation: 函数输入参数。
        error: 函数输入参数。
        session_id: 会话标识。
        assistant_message_id: 对象标识。
    
    Returns:
        None: 无返回值。
    """
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
    """提取并返回关键信息。
    
    Args:
        text: 输入文本。
    
    Returns:
        dict[str, Any]: 结构化结果数据。
    """
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
    """清洗输入并返回可用结果。
    
    Args:
        raw: 函数输入参数。
        max_items: 函数输入参数。
        max_len: 函数输入参数。
    
    Returns:
        list[str]: 处理后的列表结果。
    """
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
    """执行fallback_resolved_query流程并返回结果。
    
    Args:
        input_text: 函数输入参数。
    
    Returns:
        ResolvedQuery: 执行结果。
    """
    safe_input = str(input_text or "").strip()
    question = safe_input if safe_input else "请补充问题"
    return {
        "standalone_question": question,
        "intent": "direct_answer",
        "question_mode": "medical_new_question",
        "subqueries": [question],
        "must_cover": ["answer"],
        "entities": [],
        "filters": [],
        "preferred_sources": ["kg"],
        "carry_over_context": [],
    }


def _normalize_resolved_query(data: Any, input_text: str) -> ResolvedQuery:
    """规范化输入并返回标准化结果。
    
    Args:
        data: 函数输入参数。
        input_text: 函数输入参数。
    
    Returns:
        ResolvedQuery: 执行结果。
    """
    if not isinstance(data, dict):
        return _fallback_resolved_query(input_text)

    standalone_question = str(data.get("standalone_question") or "").strip()
    if not standalone_question:
        standalone_question = str(input_text or "").strip() or "请补充问题"

    subqueries = _sanitize_list(data.get("subqueries"))
    if not subqueries:
        subqueries = [standalone_question]

    preferred_sources = []
    for source in _sanitize_list(data.get("preferred_sources"), max_items=3, max_len=32):
        if source in _ALLOWED_PREFERRED_SOURCES and source not in preferred_sources:
            preferred_sources.append(source)
    if not preferred_sources:
        preferred_sources = ["kg"]

    question_mode = str(data.get("question_mode") or "").strip()
    if question_mode not in _ALLOWED_QUESTION_MODES:
        question_mode = "medical_new_question"

    return {
        "standalone_question": standalone_question[:300],
        "intent": str(data.get("intent") or "direct_answer").strip()[:64] or "direct_answer",
        "question_mode": question_mode,
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
        """初始化实例并注入运行依赖。
        
        Args:
            conf: 配置对象。
            llm_client: LLM 客户端实例。
        
        Returns:
            None: 无返回值。
        """
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
        """执行call_json流程并返回结果。
        
        Args:
            system_prompt: 函数输入参数。
            payload: 函数输入参数。
            operation: 函数输入参数。
            session_id: 会话标识。
            assistant_message_id: 对象标识。
        
        Returns:
            dict[str, Any]: 结构化结果数据。
        """
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
        recent_messages_digest: list[str],
        summary: dict[str, Any],
        retrieval_summary: dict[str, Any],
        session_id: str = "",
    ) -> ResolvedQuery:
        """解析并确定query相关信息并返回结果。
        
        Args:
            input_text: 函数输入参数。
            recent_messages_digest: 函数输入参数。
            summary: 函数输入参数。
            retrieval_summary: 函数输入参数。
            session_id: 会话标识。
        
        Returns:
            ResolvedQuery: 执行结果。
        """
        payload = {
            "input_text": str(input_text or ""),
            "recent_messages_digest": recent_messages_digest,
            "summary": summary,
            "retrieval_summary": retrieval_summary,
        }
        _log_memory_llm_input_metrics(
            operation="resolve_query",
            summary=summary,
            retrieval_summary=retrieval_summary,
            messages_delta_count=len(recent_messages_digest) if isinstance(recent_messages_digest, list) else 0,
            assistant_reply_chars=0,
            tool_observations_count=0,
            tool_observations_chars=0,
            projection_mode="digest_only",
            session_id=session_id,
        )
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
        turn_memory_projection: dict[str, Any],
        projection_mode: str = "turn_memory_projection_v1",
        assistant_reply_chars: int = 0,
        tool_observations_count: int = 0,
        tool_observations_chars: int = 0,
        session_id: str = "",
        assistant_message_id: int = 0,
    ) -> dict[str, Any]:
        """执行summarize_turn流程并返回结果。
        
        Args:
            previous_summary: 函数输入参数。
            previous_retrieval_summary: 函数输入参数。
            messages_delta: 函数输入参数。
            turn_memory_projection: 函数输入参数。
            projection_mode: 函数输入参数。
            assistant_reply_chars: 函数输入参数。
            tool_observations_count: 函数输入参数。
            tool_observations_chars: 函数输入参数。
            session_id: 会话标识。
            assistant_message_id: 对象标识。
        
        Returns:
            dict[str, Any]: 结构化结果数据。
        """
        payload = {
            "previous_summary": previous_summary,
            "previous_retrieval_summary": previous_retrieval_summary,
            "messages_delta": messages_delta,
            "turn_memory_projection": turn_memory_projection if isinstance(turn_memory_projection, dict) else {},
        }
        _log_memory_llm_input_metrics(
            operation="summarize_turn",
            summary=previous_summary,
            retrieval_summary=previous_retrieval_summary,
            messages_delta_count=len(messages_delta) if isinstance(messages_delta, list) else 0,
            assistant_reply_chars=assistant_reply_chars,
            tool_observations_count=tool_observations_count,
            tool_observations_chars=tool_observations_chars,
            projection_mode=projection_mode,
            session_id=session_id,
        )
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
