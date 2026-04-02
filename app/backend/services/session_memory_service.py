"""会话记忆核心服务。"""

from __future__ import annotations

import asyncio
import json
import logging
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
)

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


class SessionMemoryService:
    """会话级记忆构建、更新与透传服务。"""

    _BATCH_SIZE = 10
    _RECENT_WINDOW_ROUNDS = 4

    def __init__(self, llm: SessionMemoryLLM | None = None) -> None:
        self.llm = llm if llm is not None else SessionMemoryLLM()

    def _empty_summary(self, last_message_id: int = 0) -> SessionSummary:
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
        return {
            "summary": self._empty_summary(last_message_id),
            "retrieval_summary": self._empty_retrieval_summary(last_message_id),
            "updated_at": str(updated_at or ""),
        }

    @staticmethod
    def _to_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return int(default)

    @staticmethod
    def _sanitize_text(value: Any, max_len: int) -> str:
        return str(value or "").strip()[:max_len]

    def _sanitize_list(self, value: Any, max_items: int = 8, max_len: int = 160) -> list[str]:
        if not isinstance(value, list):
            return []
        rows: list[str] = []
        for item in value:
            text = self._sanitize_text(item, max_len)
            if not text:
                continue
            rows.append(text)
            if len(rows) >= max_items:
                break
        return rows

    def _sanitize_summary(self, data: Any) -> SessionSummary:
        source = data if isinstance(data, dict) else {}
        return {
            "version": max(1, self._to_int(source.get("version"), 1)),
            "last_message_id": max(0, self._to_int(source.get("last_message_id"), 0)),
            "conversation_goal": self._sanitize_text(source.get("conversation_goal"), 200),
            "confirmed_facts": self._sanitize_list(source.get("confirmed_facts")),
            "working_assumptions": self._sanitize_list(source.get("working_assumptions")),
            "answered_points": self._sanitize_list(source.get("answered_points")),
            "open_questions": self._sanitize_list(source.get("open_questions")),
            "risk_notes": self._sanitize_list(source.get("risk_notes")),
        }

    def _sanitize_retrieval_summary(self, data: Any) -> RetrievalSummary:
        source = data if isinstance(data, dict) else {}
        return {
            "version": max(1, self._to_int(source.get("version"), 1)),
            "last_message_id": max(0, self._to_int(source.get("last_message_id"), 0)),
            "focus_entities": self._sanitize_list(source.get("focus_entities")),
            "covered_topics": self._sanitize_list(source.get("covered_topics")),
            "evidence_gaps": self._sanitize_list(source.get("evidence_gaps")),
            "recommended_queries": self._sanitize_list(source.get("recommended_queries")),
            "retrieval_risk_flags": self._sanitize_list(source.get("retrieval_risk_flags")),
        }

    def _parse_summary(self, raw: str) -> SessionSummary:
        text = str(raw or "").strip()
        if not text:
            return self._empty_summary()
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("summary_json_not_object")
        return self._sanitize_summary(data)

    def _parse_retrieval_summary(self, raw: str) -> RetrievalSummary:
        text = str(raw or "").strip()
        if not text:
            return self._empty_retrieval_summary()
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("retrieval_summary_json_not_object")
        return self._sanitize_retrieval_summary(data)

    def _sanitize_resolved_query(self, data: Any, input_text: str) -> ResolvedQuery:
        source = data if isinstance(data, dict) else {}
        standalone_question = self._sanitize_text(source.get("standalone_question"), 300)
        if not standalone_question:
            standalone_question = self._sanitize_text(input_text, 300) or "请补充问题"

        subqueries = self._sanitize_list(source.get("subqueries"), max_items=8, max_len=200)
        if not subqueries:
            subqueries = [standalone_question]

        preferred_sources: list[str] = []
        for row in self._sanitize_list(source.get("preferred_sources"), max_items=2, max_len=8):
            if row in {"kg", "rag"} and row not in preferred_sources:
                preferred_sources.append(row)
        if not preferred_sources:
            preferred_sources = ["kg"]

        return {
            "standalone_question": standalone_question,
            "intent": self._sanitize_text(source.get("intent") or "direct_answer", 64) or "direct_answer",
            "subqueries": subqueries,
            "must_cover": self._sanitize_list(source.get("must_cover")),
            "entities": self._sanitize_list(source.get("entities")),
            "filters": self._sanitize_list(source.get("filters")),
            "preferred_sources": preferred_sources,
            "carry_over_context": self._sanitize_list(source.get("carry_over_context")),
        }

    async def _load_memory_row(self, session_id: str) -> dict[str, Any] | None:
        return await fetch_one(
            """
            SELECT summary_text, retrieval_summary_text, updated_at
            FROM session_memory
            WHERE session_id=:session_id
            """,
            {"session_id": str(session_id or "")},
        )

    async def _get_latest_message_id(self, session_id: str) -> int:
        row = await fetch_one(
            "SELECT MAX(id) AS max_id FROM messages WHERE session_id=:session_id",
            {"session_id": str(session_id or "")},
        )
        return max(0, self._to_int((row or {}).get("max_id"), 0))

    async def _get_messages_after(self, session_id: str, last_message_id: int) -> list[dict[str, Any]]:
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
        rows: list[str] = []
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            role = str(msg.get("role") or "").strip().lower()
            prefix = "用户" if role == "user" else "助手"
            content = self._sanitize_text(str(msg.get("content") or "").replace("\n", " "), 140)
            if not content:
                continue
            rows.append(f"{prefix}：{content}")
            if len(rows) >= self._RECENT_WINDOW_ROUNDS * 2:
                break
        return rows

    def _build_fallback_resolved_query(self, input_text: str) -> ResolvedQuery:
        safe_input = self._sanitize_text(input_text, 300)
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

    async def _fold_messages(
        self,
        snapshot: SessionMemorySnapshot,
        messages_delta: list[dict[str, Any]],
        turn_artifacts: dict[str, Any],
        *,
        session_id: str = "",
        assistant_message_id: int = 0,
    ) -> SessionMemorySnapshot:
        if not messages_delta:
            return snapshot

        current_summary = self._sanitize_summary(snapshot.get("summary"))
        current_retrieval = self._sanitize_retrieval_summary(snapshot.get("retrieval_summary"))

        total = len(messages_delta)
        for start in range(0, total, self._BATCH_SIZE):
            end = min(total, start + self._BATCH_SIZE)
            batch = messages_delta[start:end]
            batch_artifacts = turn_artifacts if end >= total else {}

            llm_result = await self.llm.summarize_turn(
                previous_summary=current_summary,
                previous_retrieval_summary=current_retrieval,
                messages_delta=batch,
                turn_artifacts=batch_artifacts,
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
        await SessionMemoryManager.clear_summary(str(session_id or ""))

    async def ensure_memory_current(self, session_id: str, current_message_id: int) -> SessionMemorySnapshot:
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

        known_id = min(
            self._to_int(summary.get("last_message_id"), 0),
            self._to_int(retrieval_summary.get("last_message_id"), 0),
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
        snapshot = await self.ensure_memory_current(session_id, current_message_id)
        recent_messages = await self._get_recent_messages(
            session_id,
            self._RECENT_WINDOW_ROUNDS * 2,
        )
        digest = self._build_recent_messages_digest(recent_messages)

        try:
            resolved = await self.llm.resolve_query(
                input_text=str(input_text or ""),
                recent_messages=recent_messages,
                summary=snapshot.get("summary") if isinstance(snapshot.get("summary"), dict) else {},
                retrieval_summary=(
                    snapshot.get("retrieval_summary")
                    if isinstance(snapshot.get("retrieval_summary"), dict)
                    else {}
                ),
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

        summary = self._sanitize_summary(snapshot.get("summary"))
        retrieval_summary = self._sanitize_retrieval_summary(snapshot.get("retrieval_summary"))
        memory_context: MemoryContext = {
            "summary": summary,
            "retrieval_summary": retrieval_summary,
            "last_message_id": min(
                self._to_int(summary.get("last_message_id"), 0),
                self._to_int(retrieval_summary.get("last_message_id"), 0),
            ),
            "resolved_from_history": bool(digest or summary.get("conversation_goal")),
            "recent_messages_digest": digest,
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
        try:
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
            target_id = max(0, int(assistant_message_id or 0))
            if target_id <= known_id:
                return

            delta_messages = await self._get_messages_after(session_id, known_id)
            if not delta_messages:
                return

            updated_snapshot = await self._fold_messages(
                snapshot,
                delta_messages,
                turn_artifacts if isinstance(turn_artifacts, dict) else {},
                session_id=session_id,
                assistant_message_id=int(assistant_message_id or 0),
            )
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
        snapshot = self._empty_snapshot()
        messages = await self._get_messages_after(session_id, 0)
        if messages:
            snapshot = await self._fold_messages(snapshot, messages, {}, session_id=session_id)
        await self.save_memory(session_id, snapshot)
        return snapshot
