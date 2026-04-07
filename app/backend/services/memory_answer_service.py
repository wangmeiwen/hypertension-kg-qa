"""基于当前会话记忆的直答服务。"""

from __future__ import annotations

import re
from typing import Any


class MemoryAnswerService:
    """基于会话记忆生成用户问题的直接答案。"""

    _NAME_PATTERNS = (
        re.compile(r"(?:我叫|我是|名字是)\s*([A-Za-z\u4e00-\u9fff·]{1,20})"),
        re.compile(r"(?:叫我)\s*([A-Za-z\u4e00-\u9fff·]{1,20})"),
    )
    _NAME_QUESTION_HINTS = ("我叫什么", "我的名字", "记得我名字", "你记得我吗")
    _RECALL_HINTS = ("刚才你说什么", "总结一下", "说到哪了", "刚刚聊到哪", "你记得我刚才说什么")
    _GAP_HINTS = ("还缺什么", "还有什么没回答", "还差什么证据", "还有什么证据缺口")
    _REMEMBER_HINTS = ("记住", "记一下", "记下来")

    @staticmethod
    def _sanitize_list(value: Any, *, max_items: int = 5) -> list[str]:
        """清洗输入并返回可用结果。
        
        Args:
            value: 待处理输入值。
            max_items: 函数输入参数。
        
        Returns:
            list[str]: 处理后的列表结果。
        """
        if not isinstance(value, list):
            return []
        rows: list[str] = []
        seen: set[str] = set()
        for item in value:
            text = str(item or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            rows.append(text)
            if len(rows) >= max_items:
                break
        return rows

    @classmethod
    def _extract_name(cls, rows: list[str]) -> str:
        """提取并返回关键信息。
        
        Args:
            rows: 输入数据列表。
        
        Returns:
            str: 文本结果。
        """
        for row in rows:
            text = str(row or "").strip()
            if not text:
                continue
            for pattern in cls._NAME_PATTERNS:
                matched = pattern.search(text)
                if matched:
                    return str(matched.group(1) or "").strip()
        return ""

    def answer(
        self,
        *,
        question: str,
        resolved_query: dict[str, Any],
        memory_context: dict[str, Any],
    ) -> dict[str, Any]:
        """执行answer流程并返回结果。
        
        Args:
            question: 用户输入问题文本。
            resolved_query: 问题改写后的结构化数据。
            memory_context: 会话记忆上下文。
        
        Returns:
            dict[str, Any]: 结构化结果数据。
        """
        summary = memory_context.get("summary") if isinstance(memory_context.get("summary"), dict) else {}
        retrieval_summary = (
            memory_context.get("retrieval_summary") if isinstance(memory_context.get("retrieval_summary"), dict) else {}
        )
        recent_messages_digest = (
            memory_context.get("recent_messages_digest") if isinstance(memory_context.get("recent_messages_digest"), list) else []
        )
        metadata = memory_context.get("metadata") if isinstance(memory_context.get("metadata"), dict) else {}

        confirmed_facts = self._sanitize_list(summary.get("confirmed_facts"))
        answered_points = self._sanitize_list(summary.get("answered_points"))
        open_questions = self._sanitize_list(summary.get("open_questions"))
        focus_entities = self._sanitize_list(retrieval_summary.get("focus_entities"))
        covered_topics = self._sanitize_list(retrieval_summary.get("covered_topics"))
        evidence_gaps = self._sanitize_list(retrieval_summary.get("evidence_gaps"))
        carry_over_context = self._sanitize_list(resolved_query.get("carry_over_context"))

        lowered = str(question or "").strip().lower()
        name = self._extract_name([*confirmed_facts, *carry_over_context, *recent_messages_digest])
        if any(hint in lowered for hint in self._REMEMBER_HINTS) and name:
            return {
                "can_answer": True,
                "confidence": 0.95,
                "answer_text": f"记住了，这个会话里我会把你记作{name}。",
                "support_type": "confirmed_fact",
                "used_memory_fields": ["summary.confirmed_facts", "carry_over_context", "recent_messages_digest"],
            }
        if any(hint in lowered for hint in self._NAME_QUESTION_HINTS) and name:
            return {
                "can_answer": True,
                "confidence": 0.92,
                "answer_text": f"你叫{name}。",
                "support_type": "confirmed_fact",
                "used_memory_fields": ["summary.confirmed_facts", "recent_messages_digest"],
            }

        if any(hint in lowered for hint in self._RECALL_HINTS):
            assistant_rows = [str(row).replace("助手：", "", 1).strip() for row in recent_messages_digest if str(row).startswith("助手：")]
            if assistant_rows:
                return {
                    "can_answer": True,
                    "confidence": 0.76,
                    "answer_text": f"我主要说明的是：{assistant_rows[-1]}",
                    "support_type": "recent_digest",
                    "used_memory_fields": ["recent_messages_digest"],
                }

        if any(hint in lowered for hint in self._GAP_HINTS):
            gaps = open_questions or evidence_gaps
            if gaps:
                return {
                    "can_answer": True,
                    "confidence": 0.82,
                    "answer_text": "还没完全补齐的点主要有：" + "；".join(gaps[:3]) + "。",
                    "support_type": "answered_point",
                    "used_memory_fields": ["summary.open_questions", "retrieval_summary.evidence_gaps"],
                }

        repeat_candidate = str(metadata.get("repeat_answer_candidate") or "").strip()
        if answered_points or repeat_candidate:
            parts = answered_points[:2]
            if repeat_candidate and repeat_candidate not in parts:
                parts.insert(0, repeat_candidate)
            return {
                "can_answer": True,
                "confidence": 0.84,
                "answer_text": "这个问题我们前面已经说到过，核心结论是：" + "；".join(parts[:2]) + "。",
                "support_type": "answered_point",
                "used_memory_fields": ["summary.answered_points", "memory_context.metadata"],
            }

        if focus_entities or covered_topics:
            rows = [*focus_entities[:2], *covered_topics[:2], *carry_over_context[:1]]
            return {
                "can_answer": True,
                "confidence": 0.58,
                "answer_text": "按当前会话记忆，我们一直在围绕这些内容继续："
                + "；".join([row for row in rows if row][:3])
                + "。",
                "support_type": "carry_over_context" if carry_over_context else "recent_digest",
                "used_memory_fields": ["retrieval_summary.focus_entities", "retrieval_summary.covered_topics", "carry_over_context"],
            }

        return {
            "can_answer": False,
            "confidence": 0.0,
            "answer_text": "",
            "support_type": "none",
            "used_memory_fields": [],
        }
