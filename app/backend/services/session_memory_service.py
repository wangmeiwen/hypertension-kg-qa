"""显式 QA 记忆与检索历史服务。"""

from __future__ import annotations

import json
import logging
from difflib import SequenceMatcher
from typing import Any

from app.backend.repositories.database import fetch_all, fetch_one
from app.backend.repositories.session_memory_manager import SessionMemoryManager
from app.backend.services.session_memory_types import (
    MemoryContext,
    QAMemoryEntry,
    QAMemorySnapshot,
    RetrievalHistoryEntry,
    RetrievalMemorySnapshot,
    RetrievalRecord,
    RuntimeMemoryPayload,
    SessionMemorySnapshot,
)

logger = logging.getLogger(__name__)


class SessionMemoryService:
    """维护显式问答记忆与按问题分组的检索历史。"""

    _QA_WINDOW = 6
    _RETRIEVAL_HISTORY_LIMIT = 30
    _QUESTION_MAX_LEN = 300
    _ANSWER_MAX_LEN = 2000
    _RESULT_MAX_LEN = 1600
    _INDEX_PREVIEW_MAX_LEN = 100

    @staticmethod
    def _to_int(value: Any, default: int = 0) -> int:
        """安全转整型，失败时回退默认值。"""
        try:
            return int(value)
        except (TypeError, ValueError):
            return int(default)

    @staticmethod
    def _sanitize_text(value: Any, max_len: int) -> str:
        """清洗文本并按上限截断。"""
        return str(value or "").replace("\n", " ").strip()[:max_len]

    def _sanitize_list(self, value: Any, *, max_items: int, max_len: int) -> list[str]:
        """规范化字符串列表，去重并限制长度与数量。"""
        if not isinstance(value, list):
            return []
        rows: list[str] = []
        seen: set[str] = set()
        for item in value:
            text = self._sanitize_text(item, max_len)
            if not text or text in seen:
                continue
            seen.add(text)
            rows.append(text)
            if len(rows) >= max_items:
                break
        return rows

    def _empty_qa_memory(self, last_message_id: int = 0) -> QAMemorySnapshot:
        """构造空的问答记忆快照。"""
        return {
            "version": 2,
            "last_message_id": max(0, int(last_message_id or 0)),
            "qa_pairs": [],
        }

    def _empty_retrieval_memory(self, last_message_id: int = 0) -> RetrievalMemorySnapshot:
        """构造空的检索历史记忆快照。"""
        return {
            "version": 2,
            "last_message_id": max(0, int(last_message_id or 0)),
            "questions": [],
        }

    def _empty_snapshot(self, last_message_id: int = 0, updated_at: str = "") -> SessionMemorySnapshot:
        """构造空的会话记忆总快照。"""
        return {
            "qa_memory": self._empty_qa_memory(last_message_id),
            "retrieval_memory": self._empty_retrieval_memory(last_message_id),
            "updated_at": str(updated_at or ""),
        }

    def _sanitize_qa_entry(self, row: Any) -> QAMemoryEntry | None:
        """标准化单条问答记忆记录。"""
        if not isinstance(row, dict):
            return None
        question = self._sanitize_text(row.get("user_question"), self._QUESTION_MAX_LEN)
        answer = self._sanitize_text(row.get("assistant_answer"), self._ANSWER_MAX_LEN)
        if not question or not answer:
            return None
        return {
            "user_question": question,
            "assistant_answer": answer,
            "user_message_id": max(0, self._to_int(row.get("user_message_id"), 0)),
            "assistant_message_id": max(0, self._to_int(row.get("assistant_message_id"), 0)),
            "created_at": self._sanitize_text(row.get("created_at"), 64),
        }

    def _sanitize_qa_memory(self, data: Any) -> QAMemorySnapshot:
        """标准化问答记忆对象并应用窗口裁剪。"""
        source = data if isinstance(data, dict) else {}
        rows: list[QAMemoryEntry] = []
        for item in source.get("qa_pairs") if isinstance(source.get("qa_pairs"), list) else []:
            normalized = self._sanitize_qa_entry(item)
            if normalized is not None:
                rows.append(normalized)
        return {
            "version": max(2, self._to_int(source.get("version"), 2)),
            "last_message_id": max(0, self._to_int(source.get("last_message_id"), 0)),
            "qa_pairs": rows[-self._QA_WINDOW :],
        }

    def _sanitize_retrieval_record(self, row: Any) -> RetrievalRecord | None:
        """标准化单条检索记录。"""
        if not isinstance(row, dict):
            return None
        query = self._sanitize_text(row.get("query"), self._QUESTION_MAX_LEN)
        result_content = self._sanitize_text(row.get("result_content"), self._RESULT_MAX_LEN)
        if not query or not result_content:
            return None
        record: RetrievalRecord = {
            "agent": self._sanitize_text(row.get("agent"), 24) or "unknown",
            "query": query,
            "result_content": result_content,
            "captured_at": self._sanitize_text(row.get("captured_at"), 64),
            "citations": self._sanitize_list(row.get("citations"), max_items=6, max_len=120),
        }
        for key in ("page_num", "start_page", "end_page"):
            value = row.get(key)
            if value not in (None, ""):
                record[key] = self._to_int(value, 0)
        chunk_id = self._sanitize_text(row.get("chunk_id"), 120)
        if chunk_id:
            record["chunk_id"] = chunk_id
        return record
    def _sanitize_retrieval_entry(self, row: Any) -> RetrievalHistoryEntry | None:
        """标准化单条历史检索问题记录。"""
        if not isinstance(row, dict):
            return None
        history_id = self._sanitize_text(row.get("history_id"), 64)
        user_question = self._sanitize_text(row.get("user_question"), self._QUESTION_MAX_LEN)
        if not history_id or not user_question:
            return None
        records: list[RetrievalRecord] = []
        for item in row.get("records") if isinstance(row.get("records"), list) else []:
            normalized = self._sanitize_retrieval_record(item)
            if normalized is not None:
                records.append(normalized)
        if not records:
            return None
        agent_types = self._sanitize_list(row.get("agent_types"), max_items=3, max_len=24)
        if not agent_types:
            agent_types = sorted({str(item.get("agent") or "unknown") for item in records})
        return {
            "history_id": history_id,
            "user_question": user_question,
            "created_at": self._sanitize_text(row.get("created_at"), 64),
            "agent_types": agent_types,
            "records": records,
        }

    def _sanitize_retrieval_memory(self, data: Any) -> RetrievalMemorySnapshot:
        """标准化检索记忆对象并限制历史长度。"""
        source = data if isinstance(data, dict) else {}
        rows: list[RetrievalHistoryEntry] = []
        for item in source.get("questions") if isinstance(source.get("questions"), list) else []:
            normalized = self._sanitize_retrieval_entry(item)
            if normalized is not None:
                rows.append(normalized)
        return {
            "version": max(2, self._to_int(source.get("version"), 2)),
            "last_message_id": max(0, self._to_int(source.get("last_message_id"), 0)),
            "questions": rows[-self._RETRIEVAL_HISTORY_LIMIT :],
        }

    def _parse_qa_memory(self, raw: str) -> QAMemorySnapshot:
        """解析并清洗数据库中的问答记忆 JSON 文本。"""
        text = str(raw or "").strip()
        if not text:
            return self._empty_qa_memory()
        try:
            data = json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            return self._empty_qa_memory()
        return self._sanitize_qa_memory(data)

    def _parse_retrieval_memory(self, raw: str) -> RetrievalMemorySnapshot:
        """解析并清洗数据库中的检索记忆 JSON 文本。"""
        text = str(raw or "").strip()
        if not text:
            return self._empty_retrieval_memory()
        try:
            data = json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            return self._empty_retrieval_memory()
        return self._sanitize_retrieval_memory(data)

    async def _load_memory_row(self, session_id: str) -> dict[str, Any] | None:
        """读取会话记忆原始数据库行。"""
        row = await fetch_one(
            """
            SELECT summary_text, retrieval_summary_text, updated_at
            FROM session_memory
            WHERE session_id=:session_id
            """,
            {"session_id": str(session_id or "")},
        )
        return row if isinstance(row, dict) else None

    async def _get_messages(self, session_id: str) -> list[dict[str, Any]]:
        """读取会话全部消息并转换为统一结构。"""
        rows = await fetch_all(
            """
            SELECT id, role, content, think_content, created_at
            FROM messages
            WHERE session_id=:session_id
            ORDER BY id ASC
            """,
            {"session_id": str(session_id or "")},
        )
        output: list[dict[str, Any]] = []
        for row in rows:
            output.append(
                {
                    "id": self._to_int(row.get("id"), 0),
                    "role": self._sanitize_text(row.get("role"), 20).lower(),
                    "content": str(row.get("content") or ""),
                    "think_content": str(row.get("think_content") or ""),
                    "created_at": row.get("created_at").isoformat() if row.get("created_at") else "",
                }
            )
        return output

    def _build_qa_pairs_from_messages(self, messages: list[dict[str, Any]]) -> QAMemorySnapshot:
        """从消息序列重建最近问答对记忆。"""
        pending_users: list[dict[str, Any]] = []
        pairs: list[QAMemoryEntry] = []
        last_message_id = 0
        for row in messages:
            if not isinstance(row, dict):
                continue
            message_id = self._to_int(row.get("id"), 0)
            last_message_id = max(last_message_id, message_id)
            role = self._sanitize_text(row.get("role"), 20).lower()
            content = str(row.get("content") or "")
            if role == "user":
                pending_users.append(row)
                continue
            if role != "assistant" or not pending_users:
                continue
            user_row = pending_users.pop(0)
            question = self._sanitize_text(user_row.get("content"), self._QUESTION_MAX_LEN)
            answer = self._sanitize_text(content, self._ANSWER_MAX_LEN)
            if not question or not answer:
                continue
            pairs.append(
                {
                    "user_question": question,
                    "assistant_answer": answer,
                    "user_message_id": self._to_int(user_row.get("id"), 0),
                    "assistant_message_id": message_id,
                    "created_at": self._sanitize_text(row.get("created_at") or user_row.get("created_at"), 64),
                }
            )
        return {
            "version": 2,
            "last_message_id": last_message_id,
            "qa_pairs": pairs[-self._QA_WINDOW :],
        }

    def _build_retrieval_question_index(self, retrieval_memory: RetrievalMemorySnapshot) -> list[dict[str, object]]:
        """从检索记忆构建轻量问题索引。"""
        rows: list[dict[str, object]] = []
        for item in retrieval_memory.get("questions", []):
            if not isinstance(item, dict):
                continue
            records = item.get("records") if isinstance(item.get("records"), list) else []
            rows.append(
                {
                    "history_id": self._sanitize_text(item.get("history_id"), 64),
                    "user_question": self._sanitize_text(item.get("user_question"), self._QUESTION_MAX_LEN),
                    "created_at": self._sanitize_text(item.get("created_at"), 64),
                    "agent_types": self._sanitize_list(item.get("agent_types"), max_items=3, max_len=24),
                    "record_count": len(records),
                }
            )
        return rows[-self._RETRIEVAL_HISTORY_LIMIT :]

    def _format_qa_memory_text(self, qa_memory: QAMemorySnapshot) -> str:
        """将问答记忆快照格式化为提示词文本。"""
        rows = qa_memory.get("qa_pairs") if isinstance(qa_memory.get("qa_pairs"), list) else []
        if not rows:
            return "当前暂无问答对"
        parts: list[str] = []
        for idx, row in enumerate(rows[-self._QA_WINDOW :], start=1):
            if not isinstance(row, dict):
                continue
            question = self._sanitize_text(row.get("user_question"), self._QUESTION_MAX_LEN)
            answer = self._sanitize_text(row.get("assistant_answer"), self._ANSWER_MAX_LEN)
            parts.append(f"{idx}. 用户：{question}\n助手：{answer}")
        return "\n".join(parts) if parts else "当前暂无问答对"

    def _format_retrieval_question_index_text(self, rows: list[dict[str, object]]) -> str:
        """将检索问题索引格式化为提示词文本。"""
        if not rows:
            return "当前暂无历史检索问题"
        output: list[str] = []
        for item in rows:
            history_id = self._sanitize_text(item.get("history_id"), 64)
            question = self._sanitize_text(item.get("user_question"), self._INDEX_PREVIEW_MAX_LEN)
            agents = self._sanitize_list(item.get("agent_types"), max_items=3, max_len=24)
            record_count = max(0, self._to_int(item.get("record_count"), 0))
            output.append(
                f"- history_id={history_id} | 问题={question} | agent={','.join(agents) or 'unknown'} | records={record_count}"
            )
        return "\n".join(output)

    def _build_history_id(self, user_message_id: int, assistant_message_id: int) -> str:
        """根据消息 ID 生成稳定历史检索记录 ID。"""
        return f"h-{max(0, int(user_message_id or 0))}-{max(0, int(assistant_message_id or 0))}"

    def _sanitize_query_records(self, rows: Any) -> list[RetrievalRecord]:
        """批量清洗 query_records。"""
        if not isinstance(rows, list):
            return []
        records: list[RetrievalRecord] = []
        for item in rows:
            normalized = self._sanitize_retrieval_record(item)
            if normalized is not None:
                records.append(normalized)
        return records

    def _append_retrieval_history(
        self,
        retrieval_memory: RetrievalMemorySnapshot,
        *,
        user_question: str,
        user_message_id: int,
        assistant_message_id: int,
        query_records: list[RetrievalRecord],
        captured_at: str,
    ) -> RetrievalMemorySnapshot:
        """将当前轮检索结果追加到检索记忆。"""
        if not query_records:
            return retrieval_memory
        questions = retrieval_memory.get("questions") if isinstance(retrieval_memory.get("questions"), list) else []
        updated_rows = [item for item in questions if isinstance(item, dict)]
        new_entry: RetrievalHistoryEntry = {
            "history_id": self._build_history_id(user_message_id, assistant_message_id),
            "user_question": self._sanitize_text(user_question, self._QUESTION_MAX_LEN),
            "created_at": self._sanitize_text(captured_at, 64),
            "agent_types": sorted({self._sanitize_text(item.get("agent"), 24) or "unknown" for item in query_records}),
            "records": query_records,
        }
        updated_rows.append(new_entry)
        return {
            "version": 2,
            "last_message_id": max(
                self._to_int(retrieval_memory.get("last_message_id"), 0),
                max(0, int(assistant_message_id or 0)),
            ),
            "questions": updated_rows[-self._RETRIEVAL_HISTORY_LIMIT :],
        }

    async def load_memory(self, session_id: str) -> SessionMemorySnapshot:
        """加载并返回会话记忆快照。

        Args:
            session_id: 会话标识。

        Returns:
            SessionMemorySnapshot: 问答记忆、检索记忆和更新时间组成的快照。
        """
        row = await self._load_memory_row(session_id)
        if not row:
            return self._empty_snapshot()
        updated_at = row.get("updated_at")
        updated_text = updated_at.isoformat() if updated_at else ""
        return {
            "qa_memory": self._parse_qa_memory(str(row.get("summary_text") or "")),
            "retrieval_memory": self._parse_retrieval_memory(str(row.get("retrieval_summary_text") or "")),
            "updated_at": updated_text,
        }

    async def save_memory(self, session_id: str, snapshot: SessionMemorySnapshot) -> None:
        """将会话记忆快照写回数据库。

        Args:
            session_id: 会话标识。
            snapshot: 待持久化的会话记忆快照。

        Returns:
            None: 无返回值。
        """
        qa_memory = self._sanitize_qa_memory(snapshot.get("qa_memory"))
        retrieval_memory = self._sanitize_retrieval_memory(snapshot.get("retrieval_memory"))
        await SessionMemoryManager.upsert_summary(
            str(session_id or ""),
            json.dumps(qa_memory, ensure_ascii=False),
        )
        await SessionMemoryManager.upsert_retrieval_summary(
            str(session_id or ""),
            json.dumps(retrieval_memory, ensure_ascii=False),
        )

    async def clear_memory(self, session_id: str) -> None:
        """清空指定会话的记忆数据。

        Args:
            session_id: 会话标识。

        Returns:
            None: 无返回值。
        """
        await SessionMemoryManager.clear_summary(str(session_id or ""))

    async def ensure_memory_current(self, session_id: str, current_message_id: int) -> SessionMemorySnapshot:
        """基于最新消息重建问答记忆并合并已有检索记忆。

        Args:
            session_id: 会话标识。
            current_message_id: 当前用户消息 ID，用于推进记忆游标。

        Returns:
            SessionMemorySnapshot: 与消息表同步后的记忆快照。
        """
        snapshot = await self.load_memory(session_id)
        messages = await self._get_messages(session_id)
        qa_memory = self._build_qa_pairs_from_messages(messages)
        qa_memory["last_message_id"] = max(qa_memory.get("last_message_id", 0), int(current_message_id or 0))
        return {
            "qa_memory": qa_memory,
            "retrieval_memory": self._sanitize_retrieval_memory(snapshot.get("retrieval_memory")),
            "updated_at": snapshot.get("updated_at", ""),
        }

    async def build_runtime_context(
        self,
        session_id: str,
        input_text: str,
        current_message_id: int,
    ) -> RuntimeMemoryPayload:
        """构建主控运行时所需的显式记忆上下文。

        Args:
            session_id: 会话标识。
            input_text: 当前用户输入文本。
            current_message_id: 当前用户消息 ID。

        Returns:
            RuntimeMemoryPayload: 原始输入文本和可直接放入主控提示词的记忆上下文。
        """
        snapshot = await self.ensure_memory_current(session_id, current_message_id)
        qa_memory = self._sanitize_qa_memory(snapshot.get("qa_memory"))
        retrieval_memory = self._sanitize_retrieval_memory(snapshot.get("retrieval_memory"))
        retrieval_index = self._build_retrieval_question_index(retrieval_memory)
        memory_context: MemoryContext = {
            "qa_pairs": qa_memory.get("qa_pairs", []),
            "retrieval_question_index": retrieval_index,
            "qa_memory_text": self._format_qa_memory_text(qa_memory),
            "retrieval_question_index_text": self._format_retrieval_question_index_text(retrieval_index),
        }
        return {
            "original_input_text": str(input_text or ""),
            "memory_context": memory_context,
        }

    async def refresh_after_turn(
        self,
        session_id: str,
        assistant_message_id: int,
        turn_artifacts: dict[str, Any],
    ) -> SessionMemorySnapshot:
        """在单轮结束后刷新问答记忆和检索历史，并持久化快照。

        Args:
            session_id: 会话标识。
            assistant_message_id: 本轮助手消息 ID。
            turn_artifacts: 本轮问题、检索记录和时间等产物。

        Returns:
            SessionMemorySnapshot: 写回数据库后的最新记忆快照。
        """
        snapshot = await self.load_memory(session_id)
        messages = await self._get_messages(session_id)
        qa_memory = self._build_qa_pairs_from_messages(messages)
        retrieval_memory = self._sanitize_retrieval_memory(snapshot.get("retrieval_memory"))

        user_question = self._sanitize_text(turn_artifacts.get("user_question"), self._QUESTION_MAX_LEN)
        user_message_id = self._to_int(turn_artifacts.get("user_message_id"), 0)
        captured_at = self._sanitize_text(turn_artifacts.get("captured_at"), 64)
        query_records = self._sanitize_query_records(turn_artifacts.get("query_records"))
        retrieval_memory = self._append_retrieval_history(
            retrieval_memory,
            user_question=user_question,
            user_message_id=user_message_id,
            assistant_message_id=assistant_message_id,
            query_records=query_records,
            captured_at=captured_at,
        )

        latest_message_id = max(
            qa_memory.get("last_message_id", 0),
            self._to_int(retrieval_memory.get("last_message_id"), 0),
            self._to_int(assistant_message_id, 0),
        )
        qa_memory["last_message_id"] = latest_message_id
        retrieval_memory["last_message_id"] = latest_message_id
        updated_snapshot: SessionMemorySnapshot = {
            "qa_memory": qa_memory,
            "retrieval_memory": retrieval_memory,
            "updated_at": captured_at,
        }
        await self.save_memory(session_id, updated_snapshot)
        return updated_snapshot

    async def rebuild_from_messages(self, session_id: str) -> SessionMemorySnapshot:
        """仅基于消息表重建会话记忆。

        Args:
            session_id: 会话标识。

        Returns:
            SessionMemorySnapshot: 由消息表重建出的记忆快照，检索历史会被置空。
        """
        messages = await self._get_messages(session_id)
        qa_memory = self._build_qa_pairs_from_messages(messages)
        snapshot: SessionMemorySnapshot = {
            "qa_memory": qa_memory,
            "retrieval_memory": self._empty_retrieval_memory(qa_memory.get("last_message_id", 0)),
            "updated_at": "",
        }
        await self.save_memory(session_id, snapshot)
        return snapshot

    async def read_retrieval_history(
        self,
        *,
        session_id: str,
        history_id: str = "",
        user_question: str = "",
    ) -> dict[str, Any]:
        """按 history_id 或相似问题读取历史检索记录。

        Args:
            session_id: 会话标识。
            history_id: 可选历史检索记录 ID，优先精确匹配。
            user_question: 可选用户问题，在没有 history_id 时按文本相似度匹配。

        Returns:
            dict[str, Any]: 匹配到的检索记录，未匹配时返回空记录列表。
        """
        snapshot = await self.load_memory(session_id)
        retrieval_memory = self._sanitize_retrieval_memory(snapshot.get("retrieval_memory"))
        rows = retrieval_memory.get("questions") if isinstance(retrieval_memory.get("questions"), list) else []
        safe_history_id = self._sanitize_text(history_id, 64)
        safe_question = self._sanitize_text(user_question, self._QUESTION_MAX_LEN)

        matched: RetrievalHistoryEntry | None = None
        if safe_history_id:
            for item in rows:
                if isinstance(item, dict) and str(item.get("history_id") or "") == safe_history_id:
                    matched = item
                    break
        elif safe_question:
            best_score = 0.0
            for item in rows:
                if not isinstance(item, dict):
                    continue
                candidate = self._sanitize_text(item.get("user_question"), self._QUESTION_MAX_LEN)
                score = SequenceMatcher(None, safe_question.lower(), candidate.lower()).ratio()
                if score > best_score:
                    best_score = score
                    matched = item
            if best_score < 0.45:
                matched = None
        if not matched:
            return {"history_id": safe_history_id, "matched": False, "records": [], "user_question": safe_question}

        records = matched.get("records") if isinstance(matched.get("records"), list) else []
        return {
            "matched": True,
            "history_id": self._sanitize_text(matched.get("history_id"), 64),
            "user_question": self._sanitize_text(matched.get("user_question"), self._QUESTION_MAX_LEN),
            "created_at": self._sanitize_text(matched.get("created_at"), 64),
            "agent_types": self._sanitize_list(matched.get("agent_types"), max_items=3, max_len=24),
            "records": records,
        }
