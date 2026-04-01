"""会话记忆端到端集成测试。"""

import asyncio
import json
import unittest
from datetime import datetime
from typing import Any

from app.backend.services.session_memory_service import SessionMemoryService


class _IntegrationLLM:
    def __init__(self) -> None:
        self.fail_next_summarize = False

    async def resolve_query(self, **kwargs):
        input_text = str(kwargs.get("input_text") or "").strip()
        summary_obj = kwargs.get("summary")
        summary = summary_obj if isinstance(summary_obj, dict) else {}
        facts_obj = summary.get("confirmed_facts")
        facts = facts_obj if isinstance(facts_obj, list) else []
        has_acei_context = any("ACEI" in str(item or "") for item in facts)

        standalone_question = input_text if input_text else "请补充问题"
        carry_over_context: list[str] = []
        if input_text == "那老年人呢" and has_acei_context:
            standalone_question = "老年高血压患者在 ACEI/ARB 讨论基础上如何选择降压方案？"
            carry_over_context = ["ACEI/ARB 历史讨论"]

        return {
            "standalone_question": standalone_question,
            "intent": "follow_up" if carry_over_context else "direct_answer",
            "subqueries": [standalone_question],
            "must_cover": ["answer"],
            "entities": ["高血压"],
            "filters": [],
            "preferred_sources": ["kg"],
            "carry_over_context": carry_over_context,
        }

    async def summarize_turn(self, **kwargs):
        if self.fail_next_summarize:
            self.fail_next_summarize = False
            raise RuntimeError("summarize_failed")

        previous_summary_obj = kwargs.get("previous_summary")
        previous_summary = previous_summary_obj if isinstance(previous_summary_obj, dict) else {}
        previous_retrieval_obj = kwargs.get("previous_retrieval_summary")
        previous_retrieval = previous_retrieval_obj if isinstance(previous_retrieval_obj, dict) else {}
        messages_delta_obj = kwargs.get("messages_delta")
        messages_delta = messages_delta_obj if isinstance(messages_delta_obj, list) else []

        facts_obj = previous_summary.get("confirmed_facts")
        facts = [str(item or "") for item in facts_obj] if isinstance(facts_obj, list) else []
        topics_obj = previous_retrieval.get("covered_topics")
        topics = [str(item or "") for item in topics_obj] if isinstance(topics_obj, list) else []

        last_message_id = 0
        for row in messages_delta:
            if not isinstance(row, dict):
                continue
            content = str(row.get("content") or "")
            row_id = int(row.get("id") or 0)
            last_message_id = max(last_message_id, row_id)

            if "ACEI" in content and "事实:ACEI/ARB" not in facts:
                facts.append("事实:ACEI/ARB")
            if "CCB" in content and "事实:CCB" not in facts:
                facts.append("事实:CCB")
            if "ARB" in content and "ACEI" not in content and "事实:ARB单药" not in facts:
                facts.append("事实:ARB单药")

            clean_content = content.strip()
            if clean_content and clean_content not in topics:
                topics.append(clean_content[:120])

        return {
            "summary": {
                "version": 1,
                "last_message_id": last_message_id,
                "conversation_goal": str(previous_summary.get("conversation_goal") or ""),
                "confirmed_facts": facts[:8],
                "working_assumptions": [],
                "answered_points": [],
                "open_questions": [],
                "risk_notes": [],
            },
            "retrieval_summary": {
                "version": 1,
                "last_message_id": last_message_id,
                "focus_entities": [],
                "covered_topics": topics[:8],
                "evidence_gaps": [],
                "recommended_queries": [],
                "retrieval_risk_flags": [],
            },
        }


class _InMemorySessionMemoryService(SessionMemoryService):
    def __init__(
        self,
        *,
        llm: _IntegrationLLM,
        message_store: dict[str, list[dict[str, Any]]],
        memory_store: dict[str, dict[str, Any]],
    ) -> None:
        super().__init__(llm=llm)
        self._message_store = message_store
        self._memory_store = memory_store

    @staticmethod
    def _to_message_payload(row: dict[str, Any]) -> dict[str, Any]:
        created_at = row.get("created_at")
        return {
            "id": int(row.get("id") or 0),
            "role": str(row.get("role") or ""),
            "content": str(row.get("content") or ""),
            "think_content": str(row.get("think_content") or ""),
            "created_at": created_at.isoformat() if isinstance(created_at, datetime) else None,
        }

    async def _load_memory_row(self, session_id: str) -> dict[str, Any] | None:
        row = self._memory_store.get(str(session_id or ""))
        if not row:
            return None
        return {
            "summary_text": json.dumps(row.get("summary") or {}, ensure_ascii=False),
            "retrieval_summary_text": json.dumps(row.get("retrieval_summary") or {}, ensure_ascii=False),
            "updated_at": row.get("updated_at"),
        }

    async def _get_latest_message_id(self, session_id: str) -> int:
        rows = self._message_store.get(str(session_id or ""), [])
        if not rows:
            return 0
        return int(rows[-1].get("id") or 0)

    async def _get_messages_after(self, session_id: str, last_message_id: int) -> list[dict[str, Any]]:
        rows = self._message_store.get(str(session_id or ""), [])
        return [
            self._to_message_payload(row)
            for row in rows
            if int(row.get("id") or 0) > int(last_message_id or 0)
        ]

    async def _get_recent_messages(self, session_id: str, limit: int) -> list[dict[str, Any]]:
        safe_limit = max(1, int(limit or 1))
        rows = self._message_store.get(str(session_id or ""), [])
        selected = rows[-safe_limit:]
        return [self._to_message_payload(row) for row in selected]

    async def save_memory(self, session_id: str, snapshot: dict[str, Any]) -> None:
        summary = self._sanitize_summary(snapshot.get("summary"))
        retrieval_summary = self._sanitize_retrieval_summary(snapshot.get("retrieval_summary"))
        latest_id = max(
            self._to_int(summary.get("last_message_id"), 0),
            self._to_int(retrieval_summary.get("last_message_id"), 0),
        )
        summary["last_message_id"] = latest_id
        retrieval_summary["last_message_id"] = latest_id
        self._memory_store[str(session_id or "")] = {
            "summary": summary,
            "retrieval_summary": retrieval_summary,
            "updated_at": datetime.now(),
        }

    async def clear_memory(self, session_id: str) -> None:
        self._memory_store.pop(str(session_id or ""), None)


class TestSessionMemoryIntegration(unittest.TestCase):
    def setUp(self) -> None:
        self.message_store: dict[str, list[dict[str, Any]]] = {}
        self.memory_store: dict[str, dict[str, Any]] = {}
        self.llm = _IntegrationLLM()
        self.service = _InMemorySessionMemoryService(
            llm=self.llm,
            message_store=self.message_store,
            memory_store=self.memory_store,
        )

    @staticmethod
    def _run(coro):
        return asyncio.run(coro)

    def _add_message(self, session_id: str, role: str, content: str) -> int:
        rows = self.message_store.setdefault(session_id, [])
        next_id = int(rows[-1].get("id") or 0) + 1 if rows else 1
        rows.append(
            {
                "id": next_id,
                "role": str(role or ""),
                "content": str(content or ""),
                "think_content": "",
                "created_at": datetime.now(),
            }
        )
        return next_id

    def test_followup_should_rewrite_from_history(self) -> None:
        session_id = "s-followup"
        user_1 = self._add_message(session_id, "user", "ACEI 和 ARB 联用推荐吗")
        self._run(self.service.build_runtime_context(session_id, "ACEI 和 ARB 联用推荐吗", user_1))

        assistant_1 = self._add_message(session_id, "assistant", "一般不推荐 ACEI 与 ARB 联用。")
        self._run(self.service.refresh_after_turn(session_id, assistant_1, {}))

        user_2 = self._add_message(session_id, "user", "那老年人呢")
        payload = self._run(self.service.build_runtime_context(session_id, "那老年人呢", user_2))

        standalone_question = str(((payload.get("resolved_query") or {}).get("standalone_question") or ""))
        self.assertIn("老年高血压患者", standalone_question)
        self.assertIn("ACEI", standalone_question)
        self.assertTrue(bool((payload.get("memory_context") or {}).get("resolved_from_history")))

    def test_restart_should_restore_memory_context(self) -> None:
        session_id = "s-restart"
        service_1 = self.service

        user_1 = self._add_message(session_id, "user", "ACEI 和 ARB 联用推荐吗")
        self._run(service_1.build_runtime_context(session_id, "ACEI 和 ARB 联用推荐吗", user_1))
        assistant_1 = self._add_message(session_id, "assistant", "一般不推荐 ACEI 与 ARB 联用。")
        self._run(service_1.refresh_after_turn(session_id, assistant_1, {}))

        llm_after_restart = _IntegrationLLM()
        service_2 = _InMemorySessionMemoryService(
            llm=llm_after_restart,
            message_store=self.message_store,
            memory_store=self.memory_store,
        )

        user_2 = self._add_message(session_id, "user", "那老年人呢")
        payload = self._run(service_2.build_runtime_context(session_id, "那老年人呢", user_2))
        standalone_question = str(((payload.get("resolved_query") or {}).get("standalone_question") or ""))

        self.assertIn("ACEI", standalone_question)
        self.assertGreaterEqual(int(((payload.get("memory_context") or {}).get("last_message_id") or 0)), 2)

    def test_rollback_then_rebuild_should_drop_old_facts(self) -> None:
        session_id = "s-rollback"

        user_1 = self._add_message(session_id, "user", "ACEI 和 ARB 联用推荐吗")
        self._run(self.service.build_runtime_context(session_id, "ACEI 和 ARB 联用推荐吗", user_1))
        assistant_1 = self._add_message(session_id, "assistant", "一般不推荐 ACEI 与 ARB 联用。")
        self._run(self.service.refresh_after_turn(session_id, assistant_1, {}))

        snapshot_before = self._run(self.service.load_memory(session_id))
        self.assertIn("ACEI", " ".join(snapshot_before.get("summary", {}).get("confirmed_facts", [])))

        self.message_store[session_id] = []
        self._add_message(session_id, "user", "ARB 单药在老年患者中怎么选")
        assistant_2 = self._add_message(session_id, "assistant", "ARB 单药可考虑个体化使用。")
        self._run(self.service.clear_memory(session_id))
        self._run(self.service.rebuild_from_messages(session_id))

        snapshot_after = self._run(self.service.load_memory(session_id))
        facts_after = " ".join(snapshot_after.get("summary", {}).get("confirmed_facts", []))
        self.assertNotIn("ACEI", facts_after)
        self.assertIn("ARB", facts_after)
        self.assertEqual(int(snapshot_after.get("summary", {}).get("last_message_id") or 0), assistant_2)

    def test_refresh_failure_should_be_compensated_next_turn(self) -> None:
        session_id = "s-compensate"

        user_1 = self._add_message(session_id, "user", "ACEI 和 ARB 联用推荐吗")
        self._run(self.service.build_runtime_context(session_id, "ACEI 和 ARB 联用推荐吗", user_1))
        assistant_1 = self._add_message(session_id, "assistant", "一般不推荐 ACEI 与 ARB 联用。")

        self.llm.fail_next_summarize = True
        self._run(self.service.refresh_after_turn(session_id, assistant_1, {}))

        snapshot_after_fail = self._run(self.service.load_memory(session_id))
        self.assertEqual(int(snapshot_after_fail.get("summary", {}).get("last_message_id") or 0), user_1)

        user_2 = self._add_message(session_id, "user", "那老年人呢")
        payload = self._run(self.service.build_runtime_context(session_id, "那老年人呢", user_2))
        snapshot_after_compensate = self._run(self.service.load_memory(session_id))

        self.assertEqual(int(((payload.get("memory_context") or {}).get("last_message_id") or 0)), user_2)
        self.assertEqual(int(snapshot_after_compensate.get("summary", {}).get("last_message_id") or 0), user_2)
        self.assertIn("ACEI", " ".join(snapshot_after_compensate.get("summary", {}).get("confirmed_facts", [])))


if __name__ == "__main__":
    unittest.main()
