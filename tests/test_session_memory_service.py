"""会话记忆服务单元测试。"""

import unittest

from app.backend.services.session_memory_service import SessionMemoryService


class _DummyLLM:
    async def resolve_query(self, **kwargs):
        _ = kwargs
        return {}

    async def summarize_turn(self, **kwargs):
        _ = kwargs
        return {"summary": {}, "retrieval_summary": {}}


class TestSessionMemoryService(unittest.TestCase):
    def setUp(self) -> None:
        self.service = SessionMemoryService(llm=_DummyLLM())

    def test_sanitize_summary_constraints(self) -> None:
        raw = {
            "version": "2",
            "last_message_id": "12",
            "conversation_goal": "x" * 500,
            "confirmed_facts": ["a" * 200 for _ in range(20)],
            "working_assumptions": ["w1", "w2"],
            "answered_points": ["p"],
            "open_questions": ["q"],
            "risk_notes": ["r"],
            "unknown": "drop_me",
        }

        summary = self.service._sanitize_summary(raw)
        self.assertEqual(summary["version"], 2)
        self.assertEqual(summary["last_message_id"], 12)
        self.assertEqual(len(summary["conversation_goal"]), 200)
        self.assertEqual(len(summary["confirmed_facts"]), 8)
        self.assertTrue(all(len(item) <= 160 for item in summary["confirmed_facts"]))
        self.assertNotIn("unknown", summary)

    def test_parse_summary_invalid_json_should_raise(self) -> None:
        with self.assertRaises(ValueError):
            self.service._parse_summary("{invalid_json")

    def test_build_fallback_resolved_query(self) -> None:
        resolved = self.service._build_fallback_resolved_query("那老年人呢")
        self.assertEqual(resolved["standalone_question"], "那老年人呢")
        self.assertEqual(resolved["intent"], "direct_answer")
        self.assertEqual(resolved["preferred_sources"], ["kg"])
        self.assertEqual(resolved["subqueries"], ["那老年人呢"])


if __name__ == "__main__":
    unittest.main()
