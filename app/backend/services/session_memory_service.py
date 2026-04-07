"""会话记忆核心服务。"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime
from typing import Any

import httpx

from app.backend.repositories.database import fetch_all, fetch_one
from app.backend.repositories.session_memory_manager import SessionMemoryManager
from app.backend.services.session_memory_llm import SessionMemoryLLM
from app.backend.services.session_memory_types import (
    MemoryContext,
    ResolvedQuery,
    RetrievalSummary,
    RuntimeMemoryPayload,
    SessionMemorySnapshot,
    SessionSummary,
    TopicShiftSignal,
    TurnMemoryProjection,
)

logger = logging.getLogger(__name__)


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


class SessionMemoryService:
    """会话级记忆构建、更新与透传服务。"""

    _BATCH_SIZE = 10
    _RECENT_WINDOW_ROUNDS = 4
    _RECENT_DIGEST_ITEM_MAX_CHARS = 120
    _SUMMARY_LIST_MAX_ITEMS = 5
    _RETRIEVAL_LIST_MAX_ITEMS = 5
    _FLOW_NOISE_HINTS = ("门控", "归档", "评估完成", "dispatch", "events")

    _ASSISTANT_CAPABILITY_HINTS = (
        "你能做什么",
        "你是谁",
        "功能",
        "功能范围",
        "能力",
        "可以帮我",
        "what can you do",
        "who are you",
        "capability",
    )
    _ADMINISTRATIVE_HINTS = (
        "登录",
        "退出",
        "管理后台",
        "admin",
        "session",
        "会话列表",
        "status",
        "readiness",
    )
    _SMALL_TALK_HINTS = (
        "你好",
        "您好",
        "谢谢",
        "在吗",
        "早上好",
        "晚上好",
        "hello",
        "hi",
        "thanks",
    )
    _TOPIC_RESET_HINTS = (
        "换个话题",
        "另一个问题",
        "不谈这个",
        "重新问",
        "先不说",
        "new topic",
        "another question",
    )
    _SHORT_FOLLOWUP_HINTS = (
        "那",
        "那呢",
        "这种情况呢",
        "这个怎么选",
        "那怎么办",
        "这个有区别吗",
        "这个呢",
        "那这个",
    )
    _MEMORY_LOOKUP_HINTS = (
        "我叫什么",
        "我的名字",
        "你记得我",
        "还记得我",
        "记住我",
        "刚才说什么",
        "总结一下",
        "说到哪了",
        "刚刚聊到哪",
    )
    _NAME_PATTERNS = (
        re.compile(r"(?:我叫|我是|名字是)\s*([A-Za-z\u4e00-\u9fff·]{1,20})"),
        re.compile(r"(?:叫我)\s*([A-Za-z\u4e00-\u9fff·]{1,20})"),
    )
    _ALLOWED_PREFERRED_SOURCES = {"memory", "kg", "rag", "direct", "assistant_capability"}
    _ALLOWED_QUESTION_MODES = {
        "repeat_medical_question",
        "memory_lookup",
        "assistant_capability",
        "generic_direct",
        "medical_new_question",
        "medical_gap_followup",
    }

    def __init__(self, llm: SessionMemoryLLM | None = None) -> None:
        """初始化实例并注入运行依赖。
        
        Args:
            llm: LLM 客户端实例。
        
        Returns:
            None: 无返回值。
        """
        self.llm = llm if llm is not None else SessionMemoryLLM()

    def _empty_summary(self, last_message_id: int = 0) -> SessionSummary:
        """执行empty_summary流程并返回结果。
        
        Args:
            last_message_id: 对象标识。
        
        Returns:
            SessionSummary: 执行结果。
        """
        return {
            "version": 1,
            "last_message_id": max(0, int(last_message_id or 0)),
            "conversation_goal": "",
            "confirmed_facts": [],
            "working_assumptions": [],
            "answered_points": [],
            "open_questions": [],
            "risk_notes": [],
        }

    def _empty_retrieval_summary(self, last_message_id: int = 0) -> RetrievalSummary:
        """执行empty_retrieval_summary流程并返回结果。
        
        Args:
            last_message_id: 对象标识。
        
        Returns:
            RetrievalSummary: 执行结果。
        """
        return {
            "version": 1,
            "last_message_id": max(0, int(last_message_id or 0)),
            "focus_entities": [],
            "covered_topics": [],
            "evidence_gaps": [],
            "recommended_queries": [],
            "retrieval_risk_flags": [],
        }

    def _empty_snapshot(self, last_message_id: int = 0, updated_at: str = "") -> SessionMemorySnapshot:
        """执行empty_snapshot流程并返回结果。
        
        Args:
            last_message_id: 对象标识。
            updated_at: 函数输入参数。
        
        Returns:
            SessionMemorySnapshot: 执行结果。
        """
        return {
            "summary": self._empty_summary(last_message_id),
            "retrieval_summary": self._empty_retrieval_summary(last_message_id),
            "updated_at": str(updated_at or ""),
        }

    @staticmethod
    def _to_int(value: Any, default: int = 0) -> int:
        """执行to_int流程并返回结果。
        
        Args:
            value: 待处理输入值。
            default: 函数输入参数。
        
        Returns:
            int: 数值结果。
        """
        try:
            return int(value)
        except (TypeError, ValueError):
            return int(default)

    @staticmethod
    def _sanitize_text(value: Any, max_len: int) -> str:
        """清洗输入并返回可用结果。
        
        Args:
            value: 待处理输入值。
            max_len: 函数输入参数。
        
        Returns:
            str: 文本结果。
        """
        return str(value or "").strip()[:max_len]

    def _sanitize_list(self, value: Any, max_items: int = 8, max_len: int = 160) -> list[str]:
        """清洗输入并返回可用结果。
        
        Args:
            value: 待处理输入值。
            max_items: 函数输入参数。
            max_len: 函数输入参数。
        
        Returns:
            list[str]: 处理后的列表结果。
        """
        if not isinstance(value, list):
            return []
        rows: list[str] = []
        seen: set[str] = set()
        for item in value:
            text = self._sanitize_text(item, max_len)
            if not text:
                continue
            if text in seen:
                continue
            seen.add(text)
            rows.append(text)
            if len(rows) >= max_items:
                break
        return rows

    def _json_text_len(self, value: Any) -> int:
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

    def _build_assistant_reply_digest(self, reply: str, max_len: int = 160) -> str:
        """构建并返回目标数据。
        
        Args:
            reply: 函数输入参数。
            max_len: 函数输入参数。
        
        Returns:
            str: 文本结果。
        """
        text = str(reply or "").replace("\n", " ").strip()
        if not text:
            return ""
        if len(text) <= max_len:
            return text

        segments: list[str] = []
        for marker in ("结论：", "证据支持点：", "不确定性与适用边界："):
            pos = text.find(marker)
            if pos >= 0:
                snippet = text[pos : pos + 40].strip()
                if snippet:
                    segments.append(snippet)
        if segments:
            merged = " | ".join(segments)
            return self._sanitize_text(merged, max_len)

        return f"{text[:72]}...{text[-24:]}"[:max_len]

    def _extract_answer_sentences(self, reply: str, title: str, next_titles: tuple[str, ...], max_items: int) -> list[str]:
        """提取并返回关键信息。
        
        Args:
            reply: 函数输入参数。
            title: 函数输入参数。
            next_titles: 函数输入参数。
            max_items: 函数输入参数。
        
        Returns:
            list[str]: 处理后的列表结果。
        """
        text = str(reply or "").replace("**", "").strip()
        if not text:
            return []
        marker = f"{title}："
        start = text.find(marker)
        if start < 0:
            return []
        start += len(marker)
        end = len(text)
        for next_title in next_titles:
            next_marker = f"{next_title}："
            pos = text.find(next_marker, start)
            if pos >= 0:
                end = min(end, pos)
        section = text[start:end].replace("\n", " ").strip()
        if not section:
            return []
        rows: list[str] = []
        seen: set[str] = set()
        for item in re.split(r"[。；;!！?\?]\s*", section):
            sentence = self._sanitize_text(item, 120)
            if not sentence or sentence in seen:
                continue
            seen.add(sentence)
            rows.append(sentence)
            if len(rows) >= max_items:
                break
        return rows

    def _extract_answered_points(self, evidence: dict[str, Any], assistant_reply: str) -> list[str]:
        """提取并返回关键信息。
        
        Args:
            evidence: 函数输入参数。
            assistant_reply: 函数输入参数。
        
        Returns:
            list[str]: 处理后的列表结果。
        """
        supported_points = self._sanitize_list(evidence.get("supported_points"), max_items=5, max_len=120)
        if supported_points:
            return supported_points
        conclusion_points = self._extract_answer_sentences(
            assistant_reply,
            "结论",
            ("证据支持点", "不确定性与适用边界"),
            2,
        )
        evidence_points = self._extract_answer_sentences(
            assistant_reply,
            "证据支持点",
            ("不确定性与适用边界",),
            3,
        )
        return self._sanitize_list([*conclusion_points, *evidence_points], max_items=5, max_len=120)

    def _infer_question_scope(self, input_text: str) -> str:
        """推断question_scope相关信息并返回结果。
        
        Args:
            input_text: 函数输入参数。
        
        Returns:
            str: 文本结果。
        """
        text = str(input_text or "").strip().lower()
        if not text:
            return "unknown"
        if any(hint in text for hint in self._ASSISTANT_CAPABILITY_HINTS):
            return "assistant_capability"
        if any(hint in text for hint in self._ADMINISTRATIVE_HINTS):
            return "administrative"
        if any(hint in text for hint in self._SMALL_TALK_HINTS):
            return "small_talk"
        has_medical_hint = any(
            token in text
            for token in ("高血压", "用药", "剂量", "疗效", "安全", "血压", "指南", "acei", "arb", "ccb")
        )
        if has_medical_hint:
            return "medical_qa"
        return "unknown"

    def _infer_history_scope(
        self,
        summary: SessionSummary,
        retrieval_summary: RetrievalSummary,
        recent_messages_digest: list[str],
    ) -> str:
        """推断history_scope相关信息并返回结果。
        
        Args:
            summary: 函数输入参数。
            retrieval_summary: 函数输入参数。
            recent_messages_digest: 函数输入参数。
        
        Returns:
            str: 文本结果。
        """
        candidate = " ".join(
            [
                str(summary.get("conversation_goal") or ""),
                " ".join(self._sanitize_list(retrieval_summary.get("focus_entities"), max_items=5, max_len=80)),
                " ".join(self._sanitize_list(recent_messages_digest[-4:], max_items=4, max_len=120)),
            ]
        )
        return self._infer_question_scope(candidate)

    def _normalize_question_scope(self, scope: Any) -> str:
        """规范化输入并返回标准化结果。
        
        Args:
            scope: 函数输入参数。
        
        Returns:
            str: 文本结果。
        """
        value = str(scope or "").strip()
        if value in {"medical_qa", "assistant_capability", "administrative", "small_talk", "unknown"}:
            return value
        return "unknown"

    def _is_short_followup(self, input_text: str) -> bool:
        """判断条件是否成立。
        
        Args:
            input_text: 函数输入参数。
        
        Returns:
            bool: 是否满足条件。
        """
        text = str(input_text or "").strip().lower()
        if not text or len(text) > 18:
            return False
        return any(hint in text for hint in self._SHORT_FOLLOWUP_HINTS)

    def _is_memory_lookup_question(self, input_text: str) -> bool:
        """判断条件是否成立。
        
        Args:
            input_text: 函数输入参数。
        
        Returns:
            bool: 是否满足条件。
        """
        text = str(input_text or "").strip().lower()
        if not text:
            return False
        return any(hint in text for hint in self._MEMORY_LOOKUP_HINTS)

    def _is_fast_runtime_question(self, input_text: str) -> bool:
        """判断条件是否成立。
        
        Args:
            input_text: 函数输入参数。
        
        Returns:
            bool: 是否满足条件。
        """
        scope = self._infer_question_scope(input_text)
        if scope in {"assistant_capability", "administrative", "small_talk"}:
            return True
        return self._is_memory_lookup_question(input_text) or bool(self._extract_name_candidate(input_text))

    def _normalize_preferred_sources(self, value: Any) -> list[str]:
        """规范化输入并返回标准化结果。
        
        Args:
            value: 待处理输入值。
        
        Returns:
            list[str]: 处理后的列表结果。
        """
        rows: list[str] = []
        for row in self._sanitize_list(value, max_items=3, max_len=20):
            token = str(row or "").strip().lower()
            if token in self._ALLOWED_PREFERRED_SOURCES and token not in rows:
                rows.append(token)
        if not rows:
            rows = ["kg"]
        return rows

    def _normalize_question_mode(self, value: Any, *, default: str = "medical_new_question") -> str:
        """规范化输入并返回标准化结果。
        
        Args:
            value: 待处理输入值。
            default: 函数输入参数。
        
        Returns:
            str: 文本结果。
        """
        token = str(value or "").strip()
        if token in self._ALLOWED_QUESTION_MODES:
            return token
        return default

    def _extract_name_candidate(self, text: str) -> str:
        """提取并返回关键信息。
        
        Args:
            text: 输入文本。
        
        Returns:
            str: 文本结果。
        """
        raw = str(text or "").strip()
        if not raw:
            return ""
        for pattern in self._NAME_PATTERNS:
            matched = pattern.search(raw)
            if matched:
                return self._sanitize_text(matched.group(1), 32)
        return ""

    def _extract_recent_user_questions(self, recent_messages_digest: list[str]) -> list[str]:
        """提取并返回关键信息。
        
        Args:
            recent_messages_digest: 函数输入参数。
        
        Returns:
            list[str]: 处理后的列表结果。
        """
        rows: list[str] = []
        for item in recent_messages_digest:
            text = str(item or "").strip()
            if text.startswith("用户："):
                rows.append(text.replace("用户：", "", 1).strip())
        return rows

    def _extract_recent_assistant_digest(self, recent_messages_digest: list[str]) -> str:
        """提取并返回关键信息。
        
        Args:
            recent_messages_digest: 函数输入参数。
        
        Returns:
            str: 文本结果。
        """
        for item in reversed(recent_messages_digest):
            text = str(item or "").strip()
            if text.startswith("助手："):
                return text.replace("助手：", "", 1).strip()
        return ""

    def _estimate_similarity(self, left: str, right: str) -> float:
        """估算并返回计算结果。
        
        Args:
            left: 函数输入参数。
            right: 函数输入参数。
        
        Returns:
            float: 数值结果。
        """
        left_terms = self._extract_terms(left)
        right_terms = self._extract_terms(right)
        if not left_terms or not right_terms:
            return 0.0
        overlap = len(left_terms.intersection(right_terms))
        base = min(len(left_terms), len(right_terms))
        return float(overlap) / float(max(1, base))

    def _detect_repeat_question(
        self,
        input_text: str,
        summary: SessionSummary,
        recent_messages_digest: list[str],
    ) -> dict[str, Any]:
        """检测并返回判定结果。
        
        Args:
            input_text: 函数输入参数。
            summary: 函数输入参数。
            recent_messages_digest: 函数输入参数。
        
        Returns:
            dict[str, Any]: 结构化结果数据。
        """
        user_questions = self._extract_recent_user_questions(recent_messages_digest)
        latest_similarity = 0.0
        for question in user_questions[-2:]:
            latest_similarity = max(latest_similarity, self._estimate_similarity(input_text, question))
        answered_points = self._sanitize_list(summary.get("answered_points"), max_items=5, max_len=120)
        assistant_digest = self._extract_recent_assistant_digest(recent_messages_digest)
        candidate = "；".join(answered_points[:2]) if answered_points else assistant_digest
        repeat_detected = bool(candidate) and latest_similarity >= 0.8
        return {
            "repeat_question_detected": repeat_detected,
            "repeat_match_score": round(float(latest_similarity), 4),
            "repeat_answer_candidate": self._sanitize_text(candidate, 220),
        }

    def _normalize_turn_memory_projection(self, turn_artifacts: dict[str, Any]) -> dict[str, Any]:
        """规范化输入并返回标准化结果。
        
        Args:
            turn_artifacts: 函数输入参数。
        
        Returns:
            dict[str, Any]: 结构化结果数据。
        """
        source = turn_artifacts if isinstance(turn_artifacts, dict) else {}
        projection_obj = source.get("turn_memory_projection")
        projection_raw = projection_obj if isinstance(projection_obj, dict) else {}

        if not projection_raw:
            # 兼容旧版 turn_artifacts，在服务层兜底转为投影结构。
            evidence_obj = source.get("evidence_summary")
            evidence = evidence_obj if isinstance(evidence_obj, dict) else {}

            missing_points: list[str] = []
            for key in ("missing_points", "uncovered_points", "evidence_gaps"):
                value = evidence.get(key)
                if isinstance(value, list):
                    missing_points.extend([self._sanitize_text(item, 120) for item in value if self._sanitize_text(item, 120)])

            risk_notes: list[str] = []
            high_risk_tokens = evidence.get("high_risk_tokens")
            if isinstance(high_risk_tokens, list):
                risk_notes.extend([self._sanitize_text(item, 120) for item in high_risk_tokens if self._sanitize_text(item, 120)])
            retrieval_risk_flags = evidence.get("retrieval_risk_flags")
            if isinstance(retrieval_risk_flags, list):
                risk_notes.extend([self._sanitize_text(item, 120) for item in retrieval_risk_flags if self._sanitize_text(item, 120)])
            assistant_reply = str(source.get("assistant_reply") or "")

            projection_raw = {
                "answered_points": self._extract_answered_points(evidence, assistant_reply),
                "evidence_gaps": missing_points,
                "focus_entities": [],
                "covered_topics": self._sanitize_list(evidence.get("covered_topics"), max_items=5, max_len=120),
                "risk_notes": risk_notes,
                "assistant_reply_digest": self._build_assistant_reply_digest(assistant_reply),
                "question_scope": "unknown",
            }

        projection: TurnMemoryProjection = {
            "answered_points": self._sanitize_list(projection_raw.get("answered_points"), max_items=5, max_len=120),
            "evidence_gaps": self._sanitize_list(projection_raw.get("evidence_gaps"), max_items=5, max_len=120),
            "focus_entities": self._sanitize_list(projection_raw.get("focus_entities"), max_items=5, max_len=80),
            "covered_topics": self._sanitize_list(projection_raw.get("covered_topics"), max_items=5, max_len=120),
            "risk_notes": self._sanitize_list(projection_raw.get("risk_notes"), max_items=5, max_len=120),
            "assistant_reply_digest": self._sanitize_text(
                projection_raw.get("assistant_reply_digest")
                or self._build_assistant_reply_digest(str(source.get("assistant_reply") or "")),
                180,
            ),
            "question_scope": self._sanitize_text(
                projection_raw.get("question_scope")
                or self._infer_question_scope(str(source.get("assistant_reply") or "")),
                32,
            )
            or "unknown",
        }
        projection["question_scope"] = self._normalize_question_scope(projection.get("question_scope"))

        tool_observations_count = 0
        tool_observations_chars = 0
        try:
            tool_observations_count = int(source.get("tool_observations_count") or 0)
        except (TypeError, ValueError):
            tool_observations_count = 0
        try:
            tool_observations_chars = int(source.get("tool_observations_chars") or 0)
        except (TypeError, ValueError):
            tool_observations_chars = 0
        if tool_observations_count <= 0:
            legacy_tool_obj = source.get("tool_observations")
            legacy_tools = legacy_tool_obj if isinstance(legacy_tool_obj, list) else []
            tool_observations_count = len(legacy_tools)
            tool_observations_chars = self._json_text_len(legacy_tools)

        assistant_reply_chars = 0
        try:
            assistant_reply_chars = int(source.get("assistant_reply_chars") or 0)
        except (TypeError, ValueError):
            assistant_reply_chars = 0
        if assistant_reply_chars <= 0:
            assistant_reply_chars = len(str(source.get("assistant_reply") or ""))

        return {
            "turn_memory_projection": projection,
            "projection_mode": str(source.get("projection_mode") or "turn_memory_projection_v1"),
            "assistant_reply_chars": max(0, int(assistant_reply_chars)),
            "tool_observations_count": max(0, int(tool_observations_count)),
            "tool_observations_chars": max(0, int(tool_observations_chars)),
        }

    def _extract_terms(self, text: str) -> set[str]:
        """提取并返回关键信息。
        
        Args:
            text: 输入文本。
        
        Returns:
            set[str]: 执行结果。
        """
        value = str(text or "").lower()
        rows = re.findall(r"[a-z0-9]{2,}|[\u4e00-\u9fff]{2,}", value)
        return {item for item in rows if item}

    def _estimate_topic_overlap(
        self,
        input_text: str,
        conversation_goal: str,
        focus_entities: list[str],
        recent_messages_digest: list[str],
    ) -> float:
        """估算并返回计算结果。
        
        Args:
            input_text: 函数输入参数。
            conversation_goal: 函数输入参数。
            focus_entities: 函数输入参数。
            recent_messages_digest: 函数输入参数。
        
        Returns:
            float: 数值结果。
        """
        question_terms = self._extract_terms(input_text)
        if not question_terms:
            return 0.0

        context_tokens: set[str] = set()
        context_tokens.update(self._extract_terms(conversation_goal))
        for item in focus_entities:
            context_tokens.update(self._extract_terms(item))
        for item in recent_messages_digest[-4:]:
            context_tokens.update(self._extract_terms(item))
        if not context_tokens:
            return 0.0

        overlap = len(question_terms.intersection(context_tokens))
        return float(overlap) / float(max(1, len(question_terms)))

    def _detect_topic_shift(
        self,
        input_text: str,
        summary: SessionSummary,
        retrieval_summary: RetrievalSummary,
        recent_messages_digest: list[str],
    ) -> TopicShiftSignal:
        """检测并返回判定结果。
        
        Args:
            input_text: 函数输入参数。
            summary: 函数输入参数。
            retrieval_summary: 函数输入参数。
            recent_messages_digest: 函数输入参数。
        
        Returns:
            TopicShiftSignal: 执行结果。
        """
        scope = self._infer_question_scope(input_text)
        history_scope = self._infer_history_scope(summary, retrieval_summary, recent_messages_digest)
        conversation_goal = str(summary.get("conversation_goal") or "")
        focus_entities = retrieval_summary.get("focus_entities") if isinstance(retrieval_summary.get("focus_entities"), list) else []
        overlap_score = self._estimate_topic_overlap(input_text, conversation_goal, focus_entities, recent_messages_digest)
        lowered_input = str(input_text or "").strip().lower()

        has_context_anchor = bool(conversation_goal or focus_entities or recent_messages_digest)
        is_capability_query = any(hint in lowered_input for hint in self._ASSISTANT_CAPABILITY_HINTS)
        explicit_reset = any(hint in lowered_input for hint in self._TOPIC_RESET_HINTS)
        short_followup = self._is_short_followup(input_text)

        topic_shift = False
        reason = "no_shift"
        if short_followup:
            reason = "short_followup_whitelisted"
        elif has_context_anchor and explicit_reset and overlap_score < 0.2:
            topic_shift = True
            reason = "explicit_topic_reset"
        elif has_context_anchor and is_capability_query and overlap_score < 0.1:
            topic_shift = True
            reason = "capability_low_overlap"
        elif (
            has_context_anchor
            and scope != "unknown"
            and history_scope != "unknown"
            and scope != history_scope
            and overlap_score < 0.12
        ):
            topic_shift = True
            reason = "scope_mismatch_low_overlap"

        return {
            "topic_shift_detected": bool(topic_shift),
            "reason": reason,
            "overlap_score": round(float(overlap_score), 4),
            "question_scope": scope,
            "history_scope": history_scope,
            "previous_goal": self._sanitize_text(conversation_goal, 120),
        }

    def _build_minimal_memory_context(
        self,
        *,
        last_message_id: int,
        recent_messages_digest: list[str],
        topic_shift_signal: TopicShiftSignal,
    ) -> MemoryContext:
        """构建并返回目标数据。
        
        Args:
            last_message_id: 对象标识。
            recent_messages_digest: 函数输入参数。
            topic_shift_signal: 函数输入参数。
        
        Returns:
            MemoryContext: 执行结果。
        """
        summary = self._empty_summary(last_message_id)
        retrieval_summary = self._empty_retrieval_summary(last_message_id)
        return {
            "summary": summary,
            "retrieval_summary": retrieval_summary,
            "last_message_id": int(last_message_id),
            "resolved_from_history": False,
            "recent_messages_digest": recent_messages_digest,
            "topic_shift_signal": topic_shift_signal,
            "metadata": {
                "memory_mode": "topic_shift_deweighted",
            },
        }

    def _log_memory_input_metrics(
        self,
        *,
        operation: str,
        summary: dict[str, Any],
        retrieval_summary: dict[str, Any],
        messages_delta_count: int,
        assistant_reply_chars: int,
        tool_observations_count: int,
        tool_observations_chars: int,
        projection_mode: str,
        session_id: str,
    ) -> None:
        """执行log_memory_input_metrics流程并返回结果。
        
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
            "memory_input_metrics %s",
            json.dumps(
                {
                    "operation": str(operation or ""),
                    "session_id": str(session_id or ""),
                    "summary_chars": self._json_text_len(summary),
                    "retrieval_summary_chars": self._json_text_len(retrieval_summary),
                    "messages_delta_count": int(messages_delta_count),
                    "assistant_reply_chars": int(assistant_reply_chars),
                    "tool_observations_count": int(tool_observations_count),
                    "tool_observations_chars": int(tool_observations_chars),
                    "projection_mode": str(projection_mode or "unknown"),
                },
                ensure_ascii=False,
            ),
        )

    def _log_memory_quality_metrics(
        self,
        *,
        operation: str,
        session_id: str,
        projection_mode: str,
        answered_points_count: int,
        covered_topics_count: int,
        focus_entities_count: int,
        risk_notes_count: int,
        question_scope: str,
        topic_shift_detected: bool,
        topic_shift_reason: str,
        overlap_score: float,
    ) -> None:
        """执行log_memory_quality_metrics流程并返回结果。
        
        Args:
            operation: 函数输入参数。
            session_id: 会话标识。
            projection_mode: 函数输入参数。
            answered_points_count: 函数输入参数。
            covered_topics_count: 函数输入参数。
            focus_entities_count: 函数输入参数。
            risk_notes_count: 函数输入参数。
            question_scope: 函数输入参数。
            topic_shift_detected: 函数输入参数。
            topic_shift_reason: 函数输入参数。
            overlap_score: 函数输入参数。
        
        Returns:
            None: 无返回值。
        """
        logger.info(
            "memory_quality_metrics %s",
            json.dumps(
                {
                    "operation": str(operation or ""),
                    "session_id": str(session_id or ""),
                    "projection_mode": str(projection_mode or "unknown"),
                    "answered_points_count": int(answered_points_count),
                    "covered_topics_count": int(covered_topics_count),
                    "focus_entities_count": int(focus_entities_count),
                    "risk_notes_count": int(risk_notes_count),
                    "question_scope": str(question_scope or "unknown"),
                    "topic_shift_detected": bool(topic_shift_detected),
                    "topic_shift_reason": str(topic_shift_reason or "no_shift"),
                    "overlap_score": round(float(overlap_score or 0.0), 4),
                },
                ensure_ascii=False,
            ),
        )

    def _log_memory_projection_warning(self, *, session_id: str, answered_points: list[str], projection_mode: str) -> None:
        """执行log_memory_projection_warning流程并返回结果。
        
        Args:
            session_id: 会话标识。
            answered_points: 函数输入参数。
            projection_mode: 函数输入参数。
        
        Returns:
            None: 无返回值。
        """
        if not answered_points:
            return
        lowered_points = " ".join(str(item or "").lower() for item in answered_points)
        if not any(hint.lower() in lowered_points for hint in self._FLOW_NOISE_HINTS):
            return
        logger.warning(
            "memory_projection_warning %s",
            json.dumps(
                {
                    "session_id": str(session_id or ""),
                    "projection_mode": str(projection_mode or "unknown"),
                    "warning": "answered_points_contains_flow_noise",
                },
                ensure_ascii=False,
            ),
        )

    def _sanitize_summary(self, data: Any) -> SessionSummary:
        """清洗输入并返回可用结果。
        
        Args:
            data: 函数输入参数。
        
        Returns:
            SessionSummary: 执行结果。
        """
        source = data if isinstance(data, dict) else {}
        conversation_goal = self._sanitize_text(source.get("conversation_goal"), 200)
        if conversation_goal:
            conversation_goal = self._sanitize_text(conversation_goal.split("；")[0].split(";")[0], 120)
        return {
            "version": max(1, self._to_int(source.get("version"), 1)),
            "last_message_id": max(0, self._to_int(source.get("last_message_id"), 0)),
            "conversation_goal": conversation_goal,
            "confirmed_facts": self._sanitize_list(
                source.get("confirmed_facts"),
                max_items=self._SUMMARY_LIST_MAX_ITEMS,
            ),
            "working_assumptions": self._sanitize_list(
                source.get("working_assumptions"),
                max_items=self._SUMMARY_LIST_MAX_ITEMS,
            ),
            "answered_points": self._sanitize_list(
                source.get("answered_points"),
                max_items=self._SUMMARY_LIST_MAX_ITEMS,
            ),
            "open_questions": self._sanitize_list(
                source.get("open_questions"),
                max_items=self._SUMMARY_LIST_MAX_ITEMS,
            ),
            "risk_notes": self._sanitize_list(
                source.get("risk_notes"),
                max_items=self._SUMMARY_LIST_MAX_ITEMS,
            ),
        }

    def _sanitize_retrieval_summary(self, data: Any) -> RetrievalSummary:
        """清洗输入并返回可用结果。
        
        Args:
            data: 函数输入参数。
        
        Returns:
            RetrievalSummary: 执行结果。
        """
        source = data if isinstance(data, dict) else {}
        return {
            "version": max(1, self._to_int(source.get("version"), 1)),
            "last_message_id": max(0, self._to_int(source.get("last_message_id"), 0)),
            "focus_entities": self._sanitize_list(
                source.get("focus_entities"),
                max_items=self._RETRIEVAL_LIST_MAX_ITEMS,
            ),
            "covered_topics": self._sanitize_list(
                source.get("covered_topics"),
                max_items=self._RETRIEVAL_LIST_MAX_ITEMS,
            ),
            "evidence_gaps": self._sanitize_list(
                source.get("evidence_gaps"),
                max_items=self._RETRIEVAL_LIST_MAX_ITEMS,
            ),
            "recommended_queries": self._sanitize_list(
                source.get("recommended_queries"),
                max_items=self._RETRIEVAL_LIST_MAX_ITEMS,
            ),
            "retrieval_risk_flags": self._sanitize_list(
                source.get("retrieval_risk_flags"),
                max_items=self._RETRIEVAL_LIST_MAX_ITEMS,
            ),
        }

    def _parse_summary(self, raw: str) -> SessionSummary:
        """解析summary相关信息并返回结果。
        
        Args:
            raw: 函数输入参数。
        
        Returns:
            SessionSummary: 执行结果。
        """
        text = str(raw or "").strip()
        if not text:
            return self._empty_summary()
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("summary_json_not_object")
        return self._sanitize_summary(data)

    def _parse_retrieval_summary(self, raw: str) -> RetrievalSummary:
        """解析retrieval_summary相关信息并返回结果。
        
        Args:
            raw: 函数输入参数。
        
        Returns:
            RetrievalSummary: 执行结果。
        """
        text = str(raw or "").strip()
        if not text:
            return self._empty_retrieval_summary()
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("retrieval_summary_json_not_object")
        return self._sanitize_retrieval_summary(data)

    def _sanitize_resolved_query(self, data: Any, input_text: str) -> ResolvedQuery:
        """清洗输入并返回可用结果。
        
        Args:
            data: 函数输入参数。
            input_text: 函数输入参数。
        
        Returns:
            ResolvedQuery: 执行结果。
        """
        source = data if isinstance(data, dict) else {}
        standalone_question = self._sanitize_text(source.get("standalone_question"), 300)
        if not standalone_question:
            standalone_question = self._sanitize_text(input_text, 300) or "请补充问题"

        subqueries = self._sanitize_list(source.get("subqueries"), max_items=8, max_len=200)
        if not subqueries:
            subqueries = [standalone_question]

        preferred_sources = self._normalize_preferred_sources(source.get("preferred_sources"))
        question_mode = self._normalize_question_mode(source.get("question_mode"), default="medical_new_question")

        return {
            "standalone_question": standalone_question,
            "intent": self._sanitize_text(source.get("intent") or "direct_answer", 64) or "direct_answer",
            "question_mode": question_mode,
            "subqueries": subqueries,
            "must_cover": self._sanitize_list(source.get("must_cover")),
            "entities": self._sanitize_list(source.get("entities")),
            "filters": self._sanitize_list(source.get("filters")),
            "preferred_sources": preferred_sources,
            "carry_over_context": self._sanitize_list(source.get("carry_over_context")),
        }

    async def _load_memory_row(self, session_id: str) -> dict[str, Any] | None:
        """加载memory_row相关信息并返回结果。
        
        Args:
            session_id: 会话标识。
        
        Returns:
            dict[str, Any] | None: 结构化结果数据。
        """
        return await fetch_one(
            """
            SELECT summary_text, retrieval_summary_text, updated_at
            FROM session_memory
            WHERE session_id=:session_id
            """,
            {"session_id": str(session_id or "")},
        )

    async def _get_latest_message_id(self, session_id: str) -> int:
        """获取并返回目标数据。
        
        Args:
            session_id: 会话标识。
        
        Returns:
            int: 数值结果。
        """
        row = await fetch_one(
            "SELECT MAX(id) AS max_id FROM messages WHERE session_id=:session_id",
            {"session_id": str(session_id or "")},
        )
        return max(0, self._to_int((row or {}).get("max_id"), 0))

    async def _message_exists(self, session_id: str, message_id: int) -> bool:
        """判断消息是否仍属于当前会话。"""
        target_id = max(0, int(message_id or 0))
        if target_id <= 0:
            return False
        rows = await self._get_messages_after(session_id, target_id - 1)
        return any(self._to_int(row.get("id"), 0) == target_id for row in rows)

    async def _has_stale_memory_message_id(self, session_id: str, *message_ids: int) -> bool:
        """判断记忆快照引用的消息是否已从会话中删除。"""
        for message_id in message_ids:
            target_id = max(0, int(message_id or 0))
            if target_id > 0 and not await self._message_exists(session_id, target_id):
                return True
        return False

    async def _get_messages_after(self, session_id: str, last_message_id: int) -> list[dict[str, Any]]:
        """获取并返回目标数据。
        
        Args:
            session_id: 会话标识。
            last_message_id: 对象标识。
        
        Returns:
            list[dict[str, Any]]: 处理后的列表结果。
        """
        rows = await fetch_all(
            """
            SELECT id, role, content, think_content, created_at
            FROM messages
            WHERE session_id=:session_id AND id > :last_message_id
            ORDER BY id ASC
            """,
            {
                "session_id": str(session_id or ""),
                "last_message_id": max(0, int(last_message_id or 0)),
            },
        )
        output: list[dict[str, Any]] = []
        for row in rows:
            output.append(
                {
                    "id": self._to_int(row.get("id"), 0),
                    "role": str(row.get("role") or ""),
                    "content": str(row.get("content") or ""),
                    "think_content": str(row.get("think_content") or ""),
                    "created_at": row.get("created_at").isoformat() if row.get("created_at") else None,
                }
            )
        return output

    async def _get_recent_messages(self, session_id: str, limit: int) -> list[dict[str, Any]]:
        """获取并返回目标数据。
        
        Args:
            session_id: 会话标识。
            limit: 函数输入参数。
        
        Returns:
            list[dict[str, Any]]: 处理后的列表结果。
        """
        safe_limit = max(1, int(limit or 1))
        rows = await fetch_all(
            """
            SELECT id, role, content, think_content, created_at
            FROM messages
            WHERE session_id=:session_id
            ORDER BY id DESC
            LIMIT :limit
            """,
            {
                "session_id": str(session_id or ""),
                "limit": safe_limit,
            },
        )
        rows.reverse()
        output: list[dict[str, Any]] = []
        for row in rows:
            output.append(
                {
                    "id": self._to_int(row.get("id"), 0),
                    "role": str(row.get("role") or ""),
                    "content": str(row.get("content") or ""),
                    "think_content": str(row.get("think_content") or ""),
                    "created_at": row.get("created_at").isoformat() if row.get("created_at") else None,
                }
            )
        return output

    def _build_recent_messages_digest(self, messages: list[dict[str, Any]]) -> list[str]:
        """构建并返回目标数据。
        
        Args:
            messages: 函数输入参数。
        
        Returns:
            list[str]: 处理后的列表结果。
        """
        rows: list[str] = []
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            role = str(msg.get("role") or "").strip().lower()
            prefix = "用户" if role == "user" else "助手"
            raw_content = str(msg.get("content") or "").replace("\n", " ")
            if role == "assistant":
                content = self._build_assistant_reply_digest(raw_content, self._RECENT_DIGEST_ITEM_MAX_CHARS)
            else:
                content = self._sanitize_text(raw_content, self._RECENT_DIGEST_ITEM_MAX_CHARS)
            if not content:
                continue
            rows.append(f"{prefix}：{content}")
            if len(rows) >= self._RECENT_WINDOW_ROUNDS * 2:
                break
        return rows

    def _build_fallback_resolved_query(self, input_text: str) -> ResolvedQuery:
        """构建并返回目标数据。
        
        Args:
            input_text: 函数输入参数。
        
        Returns:
            ResolvedQuery: 执行结果。
        """
        safe_input = self._sanitize_text(input_text, 300)
        question = safe_input if safe_input else "请补充问题"
        scope = self._infer_question_scope(question)
        preferred_sources = ["kg"]
        question_mode = "medical_new_question"
        if scope == "assistant_capability":
            preferred_sources = ["assistant_capability"]
            question_mode = "assistant_capability"
        elif scope in {"administrative", "small_talk"}:
            preferred_sources = ["direct"]
            question_mode = "generic_direct"
        elif self._is_memory_lookup_question(question):
            preferred_sources = ["memory"]
            question_mode = "memory_lookup"
        return {
            "standalone_question": question,
            "intent": "direct_answer",
            "question_mode": question_mode,
            "subqueries": [question],
            "must_cover": ["answer"],
            "entities": [],
            "filters": [],
            "preferred_sources": preferred_sources,
            "carry_over_context": [],
        }

    def _build_fast_path_resolved_query(
        self,
        *,
        input_text: str,
        summary: SessionSummary,
        recent_messages_digest: list[str],
    ) -> ResolvedQuery:
        """构建并返回目标数据。
        
        Args:
            input_text: 函数输入参数。
            summary: 函数输入参数。
            recent_messages_digest: 函数输入参数。
        
        Returns:
            ResolvedQuery: 执行结果。
        """
        question = self._sanitize_text(input_text, 300) or "请补充问题"
        scope = self._infer_question_scope(question)
        extracted_name = self._extract_name_candidate(question)
        carry_over_context: list[str] = []
        if extracted_name:
            carry_over_context.append(f"用户之前自称名为{extracted_name}")
        elif self._is_memory_lookup_question(question):
            recent_name = self._extract_name_candidate(" ".join(recent_messages_digest))
            if recent_name:
                carry_over_context.append(f"用户之前自称名为{recent_name}")

        preferred_sources = ["kg"]
        question_mode = "medical_new_question"
        if self._is_memory_lookup_question(question) or extracted_name:
            preferred_sources = ["memory"]
            question_mode = "memory_lookup"
        elif scope == "assistant_capability":
            preferred_sources = ["assistant_capability"]
            question_mode = "assistant_capability"
        elif scope in {"administrative", "small_talk"}:
            preferred_sources = ["direct"]
            question_mode = "generic_direct"

        return {
            "standalone_question": question,
            "intent": "direct_answer",
            "question_mode": question_mode,
            "subqueries": [question],
            "must_cover": ["answer"],
            "entities": [],
            "filters": [],
            "preferred_sources": preferred_sources,
            "carry_over_context": carry_over_context[:3],
        }

    def _build_fast_path_memory_context(
        self,
        *,
        snapshot: SessionMemorySnapshot,
        recent_messages_digest: list[str],
        repeat_signal: dict[str, Any],
        question_scope: str,
    ) -> MemoryContext:
        """构建并返回目标数据。
        
        Args:
            snapshot: 函数输入参数。
            recent_messages_digest: 函数输入参数。
            repeat_signal: 函数输入参数。
            question_scope: 函数输入参数。
        
        Returns:
            MemoryContext: 执行结果。
        """
        summary = self._sanitize_summary(snapshot.get("summary"))
        retrieval_summary = self._sanitize_retrieval_summary(snapshot.get("retrieval_summary"))
        safe_last_message_id = min(
            self._to_int(summary.get("last_message_id"), 0),
            self._to_int(retrieval_summary.get("last_message_id"), 0),
        )
        return {
            "summary": summary,
            "retrieval_summary": retrieval_summary,
            "last_message_id": safe_last_message_id,
            "resolved_from_history": bool(recent_messages_digest or summary.get("conversation_goal")),
            "recent_messages_digest": recent_messages_digest,
            "topic_shift_signal": {
                "topic_shift_detected": False,
                "reason": "no_shift",
                "overlap_score": 0.0,
                "question_scope": question_scope,
                "history_scope": self._infer_history_scope(summary, retrieval_summary, recent_messages_digest),
                "previous_goal": self._sanitize_text(summary.get("conversation_goal"), 120),
            },
            "metadata": {
                "memory_mode": "fast_path",
                **repeat_signal,
            },
        }

    async def _fold_messages(
        self,
        snapshot: SessionMemorySnapshot,
        messages_delta: list[dict[str, Any]],
        turn_artifacts: dict[str, Any],
        *,
        session_id: str = "",
        assistant_message_id: int = 0,
    ) -> SessionMemorySnapshot:
        """执行fold_messages流程并返回结果。
        
        Args:
            snapshot: 函数输入参数。
            messages_delta: 函数输入参数。
            turn_artifacts: 函数输入参数。
            session_id: 会话标识。
            assistant_message_id: 对象标识。
        
        Returns:
            SessionMemorySnapshot: 执行结果。
        """
        if not messages_delta:
            return snapshot

        current_summary = self._sanitize_summary(snapshot.get("summary"))
        current_retrieval = self._sanitize_retrieval_summary(snapshot.get("retrieval_summary"))
        normalized_turn_artifacts = self._normalize_turn_memory_projection(turn_artifacts)
        projection_obj = normalized_turn_artifacts.get("turn_memory_projection")
        projection = projection_obj if isinstance(projection_obj, dict) else {}
        self._log_memory_quality_metrics(
            operation="summarize_turn",
            session_id=session_id,
            projection_mode=str(normalized_turn_artifacts.get("projection_mode") or "turn_memory_projection_v1"),
            answered_points_count=len(self._sanitize_list(projection.get("answered_points"), max_items=5, max_len=120)),
            covered_topics_count=len(self._sanitize_list(projection.get("covered_topics"), max_items=5, max_len=120)),
            focus_entities_count=len(self._sanitize_list(projection.get("focus_entities"), max_items=5, max_len=80)),
            risk_notes_count=len(self._sanitize_list(projection.get("risk_notes"), max_items=5, max_len=120)),
            question_scope=str(projection.get("question_scope") or "unknown"),
            topic_shift_detected=False,
            topic_shift_reason="no_shift",
            overlap_score=0.0,
        )
        self._log_memory_projection_warning(
            session_id=session_id,
            answered_points=self._sanitize_list(projection.get("answered_points"), max_items=5, max_len=120),
            projection_mode=str(normalized_turn_artifacts.get("projection_mode") or "turn_memory_projection_v1"),
        )

        total = len(messages_delta)
        for start in range(0, total, self._BATCH_SIZE):
            end = min(total, start + self._BATCH_SIZE)
            batch = messages_delta[start:end]
            batch_artifacts = normalized_turn_artifacts if end >= total else {}

            self._log_memory_input_metrics(
                operation="summarize_turn",
                summary=current_summary,
                retrieval_summary=current_retrieval,
                messages_delta_count=len(batch),
                assistant_reply_chars=int(batch_artifacts.get("assistant_reply_chars") or 0),
                tool_observations_count=int(batch_artifacts.get("tool_observations_count") or 0),
                tool_observations_chars=int(batch_artifacts.get("tool_observations_chars") or 0),
                projection_mode=str(batch_artifacts.get("projection_mode") or "turn_memory_projection_v1"),
                session_id=session_id,
            )

            llm_result = await self.llm.summarize_turn(
                previous_summary=current_summary,
                previous_retrieval_summary=current_retrieval,
                messages_delta=batch,
                turn_memory_projection=(
                    batch_artifacts.get("turn_memory_projection")
                    if isinstance(batch_artifacts.get("turn_memory_projection"), dict)
                    else {}
                ),
                projection_mode=str(batch_artifacts.get("projection_mode") or "turn_memory_projection_v1"),
                assistant_reply_chars=int(batch_artifacts.get("assistant_reply_chars") or 0),
                tool_observations_count=int(batch_artifacts.get("tool_observations_count") or 0),
                tool_observations_chars=int(batch_artifacts.get("tool_observations_chars") or 0),
                session_id=session_id,
                assistant_message_id=int(assistant_message_id or 0),
            )

            next_summary = self._sanitize_summary((llm_result or {}).get("summary"))
            next_retrieval = self._sanitize_retrieval_summary((llm_result or {}).get("retrieval_summary"))
            latest_batch_id = max(0, self._to_int((batch[-1] if batch else {}).get("id"), 0))
            latest_id = max(
                latest_batch_id,
                self._to_int(next_summary.get("last_message_id"), 0),
                self._to_int(next_retrieval.get("last_message_id"), 0),
            )
            next_summary["last_message_id"] = latest_id
            next_retrieval["last_message_id"] = latest_id
            current_summary = next_summary
            current_retrieval = next_retrieval

        return {
            "summary": current_summary,
            "retrieval_summary": current_retrieval,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }

    async def load_memory(self, session_id: str) -> SessionMemorySnapshot:
        """加载memory相关信息并返回结果。
        
        Args:
            session_id: 会话标识。
        
        Returns:
            SessionMemorySnapshot: 执行结果。
        """
        row = await self._load_memory_row(session_id)
        if not row:
            return self._empty_snapshot()

        updated_at = row.get("updated_at")
        updated_text = updated_at.isoformat() if updated_at else ""

        try:
            summary = self._parse_summary(str(row.get("summary_text") or ""))
            retrieval = self._parse_retrieval_summary(str(row.get("retrieval_summary_text") or ""))
            return {
                "summary": summary,
                "retrieval_summary": retrieval,
                "updated_at": updated_text,
            }
        except (TypeError, ValueError, json.JSONDecodeError):
            return self._empty_snapshot(updated_at=updated_text)

    async def save_memory(self, session_id: str, snapshot: SessionMemorySnapshot) -> None:
        """保存memory相关信息并返回结果。
        
        Args:
            session_id: 会话标识。
            snapshot: 函数输入参数。
        
        Returns:
            None: 无返回值。
        """
        summary = self._sanitize_summary(snapshot.get("summary"))
        retrieval_summary = self._sanitize_retrieval_summary(snapshot.get("retrieval_summary"))
        latest_id = max(
            self._to_int(summary.get("last_message_id"), 0),
            self._to_int(retrieval_summary.get("last_message_id"), 0),
        )
        summary["last_message_id"] = latest_id
        retrieval_summary["last_message_id"] = latest_id

        await SessionMemoryManager.upsert_summary(
            str(session_id or ""),
            json.dumps(summary, ensure_ascii=False),
        )
        await SessionMemoryManager.upsert_retrieval_summary(
            str(session_id or ""),
            json.dumps(retrieval_summary, ensure_ascii=False),
        )

    async def clear_memory(self, session_id: str) -> None:
        """执行clear_memory流程并返回结果。
        
        Args:
            session_id: 会话标识。
        
        Returns:
            None: 无返回值。
        """
        await SessionMemoryManager.clear_summary(str(session_id or ""))

    async def ensure_memory_current(self, session_id: str, current_message_id: int) -> SessionMemorySnapshot:
        """执行ensure_memory_current流程并返回结果。
        
        Args:
            session_id: 会话标识。
            current_message_id: 对象标识。
        
        Returns:
            SessionMemorySnapshot: 执行结果。
        """
        row = await self._load_memory_row(session_id)
        latest_message_id = max(
            max(0, int(current_message_id or 0)),
            await self._get_latest_message_id(session_id),
        )

        if not row:
            rebuilt = await self.rebuild_from_messages(session_id)
            return rebuilt

        updated_at = row.get("updated_at")
        updated_text = updated_at.isoformat() if updated_at else ""

        try:
            summary = self._parse_summary(str(row.get("summary_text") or ""))
            retrieval_summary = self._parse_retrieval_summary(str(row.get("retrieval_summary_text") or ""))
        except (TypeError, ValueError, json.JSONDecodeError):
            await self.clear_memory(session_id)
            rebuilt = await self.rebuild_from_messages(session_id)
            return rebuilt

        snapshot: SessionMemorySnapshot = {
            "summary": summary,
            "retrieval_summary": retrieval_summary,
            "updated_at": updated_text,
        }

        summary_last_id = self._to_int(summary.get("last_message_id"), 0)
        retrieval_last_id = self._to_int(retrieval_summary.get("last_message_id"), 0)
        if await self._has_stale_memory_message_id(session_id, summary_last_id, retrieval_last_id):
            await self.clear_memory(session_id)
            rebuilt = await self.rebuild_from_messages(session_id)
            return rebuilt

        known_id = min(
            summary_last_id,
            retrieval_last_id,
        )
        if known_id >= latest_message_id:
            return snapshot

        delta_messages = await self._get_messages_after(session_id, known_id)
        if not delta_messages:
            rebuilt = await self.rebuild_from_messages(session_id)
            return rebuilt

        updated_snapshot = await self._fold_messages(snapshot, delta_messages, {}, session_id=session_id)

        await self.save_memory(session_id, updated_snapshot)
        return updated_snapshot

    async def build_runtime_context(
        self,
        session_id: str,
        input_text: str,
        current_message_id: int,
    ) -> RuntimeMemoryPayload:
        """构建runtime_context相关信息并返回结果。
        
        Args:
            session_id: 会话标识。
            input_text: 函数输入参数。
            current_message_id: 对象标识。
        
        Returns:
            RuntimeMemoryPayload: 执行结果。
        """
        fast_path_enabled = self._is_fast_runtime_question(str(input_text or ""))
        if fast_path_enabled:
            snapshot = await self.ensure_memory_current(session_id, current_message_id)
            recent_messages = await self._get_recent_messages(
                session_id,
                self._RECENT_WINDOW_ROUNDS * 2,
            )
            digest = self._build_recent_messages_digest(recent_messages)
            repeat_signal = self._detect_repeat_question(
                str(input_text or ""),
                self._sanitize_summary(snapshot.get("summary")),
                digest,
            )
            question_scope = self._infer_question_scope(str(input_text or ""))
            resolved_query = self._build_fast_path_resolved_query(
                input_text=str(input_text or ""),
                summary=self._sanitize_summary(snapshot.get("summary")),
                recent_messages_digest=digest,
            )
            memory_context = self._build_fast_path_memory_context(
                snapshot=snapshot,
                recent_messages_digest=digest,
                repeat_signal=repeat_signal,
                question_scope=question_scope,
            )
            self._log_memory_quality_metrics(
                operation="resolve_query_fast_path",
                session_id=session_id,
                projection_mode="fast_path",
                answered_points_count=len(self._sanitize_list(memory_context.get("summary", {}).get("answered_points"), max_items=5, max_len=120)),
                covered_topics_count=len(self._sanitize_list(memory_context.get("retrieval_summary", {}).get("covered_topics"), max_items=5, max_len=120)),
                focus_entities_count=len(self._sanitize_list(memory_context.get("retrieval_summary", {}).get("focus_entities"), max_items=5, max_len=80)),
                risk_notes_count=len(self._sanitize_list(memory_context.get("summary", {}).get("risk_notes"), max_items=5, max_len=120)),
                question_scope=question_scope,
                topic_shift_detected=False,
                topic_shift_reason="no_shift",
                overlap_score=0.0,
            )
            return {
                "original_input_text": str(input_text or ""),
                "resolved_query": resolved_query,
                "memory_context": memory_context,
            }

        snapshot = await self.ensure_memory_current(session_id, current_message_id)
        recent_messages = await self._get_recent_messages(
            session_id,
            self._RECENT_WINDOW_ROUNDS * 2,
        )
        digest = self._build_recent_messages_digest(recent_messages)
        digest_for_shift = list(digest)
        current_user_digest = f"用户：{self._sanitize_text(str(input_text or '').replace(chr(10), ' '), self._RECENT_DIGEST_ITEM_MAX_CHARS)}"
        if digest_for_shift and digest_for_shift[-1] == current_user_digest:
            digest_for_shift = digest_for_shift[:-1]

        summary = self._sanitize_summary(snapshot.get("summary"))
        retrieval_summary = self._sanitize_retrieval_summary(snapshot.get("retrieval_summary"))
        repeat_signal = self._detect_repeat_question(
            str(input_text or ""),
            summary,
            digest_for_shift,
        )
        topic_shift_signal = self._detect_topic_shift(
            str(input_text or ""),
            summary,
            retrieval_summary,
            digest_for_shift,
        )
        self._log_memory_quality_metrics(
            operation="resolve_query",
            session_id=session_id,
            projection_mode="digest_only_topic_shift_deweighted"
            if bool(topic_shift_signal.get("topic_shift_detected"))
            else "digest_only",
            answered_points_count=len(self._sanitize_list(summary.get("answered_points"), max_items=5, max_len=120)),
            covered_topics_count=len(self._sanitize_list(retrieval_summary.get("covered_topics"), max_items=5, max_len=120)),
            focus_entities_count=len(self._sanitize_list(retrieval_summary.get("focus_entities"), max_items=5, max_len=80)),
            risk_notes_count=len(self._sanitize_list(summary.get("risk_notes"), max_items=5, max_len=120)),
            question_scope=str(topic_shift_signal.get("question_scope") or "unknown"),
            topic_shift_detected=bool(topic_shift_signal.get("topic_shift_detected")),
            topic_shift_reason=str(topic_shift_signal.get("reason") or "no_shift"),
            overlap_score=float(topic_shift_signal.get("overlap_score") or 0.0),
        )

        resolve_summary: dict[str, Any] = summary
        resolve_retrieval_summary: dict[str, Any] = retrieval_summary
        projection_mode = "digest_only"
        if bool(topic_shift_signal.get("topic_shift_detected")):
            safe_last_message_id = min(
                self._to_int(summary.get("last_message_id"), 0),
                self._to_int(retrieval_summary.get("last_message_id"), 0),
            )
            resolve_summary = self._empty_summary(safe_last_message_id)
            resolve_retrieval_summary = self._empty_retrieval_summary(safe_last_message_id)
            projection_mode = "digest_only_topic_shift_deweighted"

        self._log_memory_input_metrics(
            operation="resolve_query",
            summary=resolve_summary,
            retrieval_summary=resolve_retrieval_summary,
            messages_delta_count=len(digest),
            assistant_reply_chars=0,
            tool_observations_count=0,
            tool_observations_chars=0,
            projection_mode=projection_mode,
            session_id=session_id,
        )

        try:
            resolved = await self.llm.resolve_query(
                input_text=str(input_text or ""),
                recent_messages_digest=digest,
                summary=resolve_summary,
                retrieval_summary=resolve_retrieval_summary,
                session_id=session_id,
            )
            resolved_query = self._sanitize_resolved_query(resolved, input_text)
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
                operation="resolve_query",
                error=exc,
                session_id=session_id,
            )
            resolved_query = self._build_fallback_resolved_query(str(input_text or ""))

        question_scope = str(topic_shift_signal.get("question_scope") or "unknown")
        if repeat_signal.get("repeat_question_detected"):
            resolved_query["preferred_sources"] = ["memory"]
            resolved_query["question_mode"] = "repeat_medical_question" if question_scope == "medical_qa" else "memory_lookup"
        elif self._is_memory_lookup_question(str(input_text or "")):
            resolved_query["preferred_sources"] = ["memory"]
            resolved_query["question_mode"] = "memory_lookup"
        elif question_scope == "assistant_capability":
            resolved_query["preferred_sources"] = ["assistant_capability"]
            resolved_query["question_mode"] = "assistant_capability"
        elif question_scope in {"administrative", "small_talk"}:
            resolved_query["preferred_sources"] = ["direct"]
            resolved_query["question_mode"] = "generic_direct"
        elif bool(topic_shift_signal.get("topic_shift_detected")):
            resolved_query["preferred_sources"] = ["kg"]
            resolved_query["question_mode"] = "medical_new_question"
        elif not self._normalize_preferred_sources(resolved_query.get("preferred_sources")):
            resolved_query["preferred_sources"] = ["kg"]
            resolved_query["question_mode"] = "medical_new_question"

        if bool(topic_shift_signal.get("topic_shift_detected")) and bool(
            self._sanitize_list(resolved_query.get("carry_over_context"), max_items=5, max_len=120)
        ):
            logger.warning(
                "memory_quality_warning %s",
                json.dumps(
                    {
                        "session_id": str(session_id or ""),
                        "warning": "topic_shift_with_carry_over_context",
                        "topic_shift_reason": str(topic_shift_signal.get("reason") or "no_shift"),
                    },
                    ensure_ascii=False,
                ),
            )

        safe_last_message_id = min(
            self._to_int(summary.get("last_message_id"), 0),
            self._to_int(retrieval_summary.get("last_message_id"), 0),
        )

        if bool(topic_shift_signal.get("topic_shift_detected")):
            memory_context = self._build_minimal_memory_context(
                last_message_id=safe_last_message_id,
                recent_messages_digest=digest,
                topic_shift_signal=topic_shift_signal,
            )
            memory_context["metadata"] = {
                **(memory_context.get("metadata") if isinstance(memory_context.get("metadata"), dict) else {}),
                **repeat_signal,
            }
        else:
            memory_context = {
                "summary": summary,
                "retrieval_summary": retrieval_summary,
                "last_message_id": safe_last_message_id,
                "resolved_from_history": bool(digest or summary.get("conversation_goal")),
                "recent_messages_digest": digest,
                "topic_shift_signal": topic_shift_signal,
                "metadata": {
                    "memory_mode": "normal",
                    **repeat_signal,
                },
            }

        return {
            "original_input_text": str(input_text or ""),
            "resolved_query": resolved_query,
            "memory_context": memory_context,
        }

    async def refresh_after_turn(
        self,
        session_id: str,
        assistant_message_id: int,
        turn_artifacts: dict[str, Any],
    ) -> None:
        """执行refresh_after_turn流程并返回结果。
        
        Args:
            session_id: 会话标识。
            assistant_message_id: 对象标识。
            turn_artifacts: 函数输入参数。
        
        Returns:
            None: 无返回值。
        """
        try:
            target_id = max(0, int(assistant_message_id or 0))
            if target_id <= 0:
                return
            if not await self._message_exists(session_id, target_id):
                return

            row = await self._load_memory_row(session_id)
            if not row:
                snapshot = self._empty_snapshot()
            else:
                summary = self._parse_summary(str(row.get("summary_text") or ""))
                retrieval_summary = self._parse_retrieval_summary(str(row.get("retrieval_summary_text") or ""))
                updated_at = row.get("updated_at")
                snapshot = {
                    "summary": summary,
                    "retrieval_summary": retrieval_summary,
                    "updated_at": updated_at.isoformat() if updated_at else "",
                }

            known_id = min(
                self._to_int(snapshot.get("summary", {}).get("last_message_id"), 0),
                self._to_int(snapshot.get("retrieval_summary", {}).get("last_message_id"), 0),
            )
            if await self._has_stale_memory_message_id(
                session_id,
                self._to_int(snapshot.get("summary", {}).get("last_message_id"), 0),
                self._to_int(snapshot.get("retrieval_summary", {}).get("last_message_id"), 0),
            ):
                await self.clear_memory(session_id)
                snapshot = await self.rebuild_from_messages(session_id)
                known_id = min(
                    self._to_int(snapshot.get("summary", {}).get("last_message_id"), 0),
                    self._to_int(snapshot.get("retrieval_summary", {}).get("last_message_id"), 0),
                )
            if target_id <= known_id:
                return

            delta_messages = await self._get_messages_after(session_id, known_id)
            if not delta_messages:
                return
            if not any(self._to_int(row.get("id"), 0) == target_id for row in delta_messages):
                return

            normalized_turn_artifacts = self._normalize_turn_memory_projection(
                turn_artifacts if isinstance(turn_artifacts, dict) else {}
            )

            updated_snapshot = await self._fold_messages(
                snapshot,
                delta_messages,
                normalized_turn_artifacts,
                session_id=session_id,
                assistant_message_id=int(assistant_message_id or 0),
            )
            if not await self._message_exists(session_id, target_id):
                return
            await self.save_memory(session_id, updated_snapshot)
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
                operation="refresh_after_turn",
                error=exc,
                session_id=session_id,
                assistant_message_id=int(assistant_message_id or 0),
            )
            return

    async def rebuild_from_messages(self, session_id: str) -> SessionMemorySnapshot:
        """执行rebuild_from_messages流程并返回结果。
        
        Args:
            session_id: 会话标识。
        
        Returns:
            SessionMemorySnapshot: 执行结果。
        """
        snapshot = self._empty_snapshot()
        messages = await self._get_messages_after(session_id, 0)
        if messages:
            snapshot = await self._fold_messages(snapshot, messages, {}, session_id=session_id)
        await self.save_memory(session_id, snapshot)
        return snapshot
