"""会话记忆服务单元测试。"""

import asyncio
import json
import unittest
from unittest.mock import patch

from app.backend.services.session_memory_service import SessionMemoryService


class _DummyLLM:
    def __init__(self) -> None:
        self.last_summarize_kwargs = {}

    async def resolve_query(self, **kwargs):
        _ = kwargs
        return {}

    async def summarize_turn(self, **kwargs):
        self.last_summarize_kwargs = kwargs
        return {"summary": {}, "retrieval_summary": {}}


class TestSessionMemoryService(unittest.TestCase):
    def setUp(self) -> None:
        self.service = SessionMemoryService(llm=_DummyLLM())

    def test_sanitize_summary_constraints(self) -> None:
        raw = {
            "version": "2",
            "last_message_id": "12",
            "conversation_goal": "x" * 500,
            "confirmed_facts": [f"fact-{idx}-" + ("a" * 200) for idx in range(20)],
            "working_assumptions": ["w1", "w2"],
            "answered_points": ["p"],
            "open_questions": ["q"],
            "risk_notes": ["r"],
            "unknown": "drop_me",
        }

        summary = self.service._sanitize_summary(raw)
        self.assertEqual(summary["version"], 2)
        self.assertEqual(summary["last_message_id"], 12)
        self.assertLessEqual(len(summary["conversation_goal"]), 120)
        self.assertEqual(len(summary["confirmed_facts"]), 5)
        self.assertTrue(all(len(item) <= 160 for item in summary["confirmed_facts"]))
        self.assertNotIn("unknown", summary)

    def test_parse_summary_invalid_json_should_raise(self) -> None:
        with self.assertRaises(ValueError):
            self.service._parse_summary("{invalid_json")

    def test_build_fallback_resolved_query(self) -> None:
        resolved = self.service._build_fallback_resolved_query("那老年人呢")
        self.assertEqual(resolved["standalone_question"], "那老年人呢")
        self.assertEqual(resolved["intent"], "direct_answer")
        self.assertEqual(resolved["question_mode"], "medical_new_question")
        self.assertEqual(resolved["preferred_sources"], ["kg"])
        self.assertEqual(resolved["subqueries"], ["那老年人呢"])

    def test_build_fallback_resolved_query_should_route_capability_question(self) -> None:
        resolved = self.service._build_fallback_resolved_query("你能做什么")
        self.assertEqual(resolved["preferred_sources"], ["assistant_capability"])
        self.assertEqual(resolved["question_mode"], "assistant_capability")

    def test_normalize_turn_memory_projection_should_drop_raw_tool_payload(self) -> None:
        normalized = self.service._normalize_turn_memory_projection(
            {
                "turn_memory_projection": {
                    "answered_points": ["A", "A"],
                    "evidence_gaps": ["G1"],
                    "focus_entities": ["E1"],
                    "covered_topics": ["T1"],
                    "risk_notes": ["R1"],
                    "assistant_reply_digest": "reply",
                    "question_scope": "medical_qa",
                },
                "tool_observations": [{"raw": "very long payload"}],
            }
        )
        projection = normalized.get("turn_memory_projection") if isinstance(normalized.get("turn_memory_projection"), dict) else {}
        self.assertEqual(projection.get("answered_points"), ["A"])
        self.assertEqual(str(normalized.get("projection_mode") or ""), "turn_memory_projection_v1")
        self.assertGreaterEqual(int(normalized.get("tool_observations_count") or 0), 1)

    def test_normalize_turn_memory_projection_should_not_backfill_answered_points_from_processing_steps(self) -> None:
        normalized = self.service._normalize_turn_memory_projection(
            {
                "assistant_reply": "结论：联合治疗具有机制互补优势。证据支持点：当前证据支持依从性更好。",
                "processing_steps": ["终答门控评估完成", "证据归档完成"],
                "evidence_summary": {"missing_points": ["头对头结局"]},
            }
        )
        projection_obj = normalized.get("turn_memory_projection")
        projection = projection_obj if isinstance(projection_obj, dict) else {}
        answered_points = projection.get("answered_points") if isinstance(projection.get("answered_points"), list) else []
        self.assertIn("联合治疗具有机制互补优势", " ".join(str(item or "") for item in answered_points))
        self.assertNotIn("终答门控评估完成", " ".join(str(item or "") for item in answered_points))

    def test_fold_messages_should_pass_projection_to_summarize_turn(self) -> None:
        async def _run_case() -> None:
            snapshot = self.service._empty_snapshot()
            messages = [
                {
                    "id": 1,
                    "role": "assistant",
                    "content": "回答",
                    "think_content": "",
                    "created_at": None,
                }
            ]
            turn_artifacts = {
                "turn_memory_projection": {
                    "answered_points": ["点1"],
                    "evidence_gaps": ["缺口1"],
                    "focus_entities": ["实体1"],
                    "covered_topics": ["主题1"],
                    "risk_notes": ["风险1"],
                    "assistant_reply_digest": "摘要",
                    "question_scope": "medical_qa",
                },
                "projection_mode": "turn_memory_projection_v1",
            }
            await self.service._fold_messages(snapshot, messages, turn_artifacts, session_id="s1")

        asyncio.run(_run_case())
        kwargs = self.service.llm.last_summarize_kwargs if isinstance(self.service.llm.last_summarize_kwargs, dict) else {}
        projection = kwargs.get("turn_memory_projection") if isinstance(kwargs.get("turn_memory_projection"), dict) else {}
        self.assertEqual(projection.get("question_scope"), "medical_qa")
        self.assertNotIn("tool_observations", kwargs)

    def test_fold_messages_should_warn_on_flow_noise_in_answered_points(self) -> None:
        async def _run_case() -> None:
            snapshot = self.service._empty_snapshot()
            messages = [{"id": 1, "role": "assistant", "content": "回答", "think_content": "", "created_at": None}]
            turn_artifacts = {
                "turn_memory_projection": {
                    "answered_points": ["证据归档完成"],
                    "evidence_gaps": [],
                    "focus_entities": [],
                    "covered_topics": [],
                    "risk_notes": [],
                    "assistant_reply_digest": "摘要",
                    "question_scope": "medical_qa",
                }
            }
            with patch("app.backend.services.session_memory_service.logger.warning") as warning_mock:
                await self.service._fold_messages(snapshot, messages, turn_artifacts, session_id="s-warning")
                return warning_mock

        warning_mock = asyncio.run(_run_case())
        joined = " ".join(str(call.args[0]) for call in warning_mock.call_args_list if call.args)
        self.assertIn("memory_projection_warning", joined)

    def test_detect_topic_shift_should_soft_shift_for_scope_mismatch(self) -> None:
        signal = self.service._detect_topic_shift(
            "后台会话列表怎么看",
            self.service._sanitize_summary({"conversation_goal": "高血压联合用药比较"}),
            self.service._sanitize_retrieval_summary({"focus_entities": ["ACEI", "CCB"]}),
            ["用户：ACEI 和 ARB 联用推荐吗", "助手：一般不推荐联用"],
        )
        self.assertTrue(bool(signal.get("topic_shift_detected")))
        self.assertEqual(str(signal.get("reason") or ""), "scope_mismatch_low_overlap")

    def test_detect_topic_shift_should_whitelist_short_followup(self) -> None:
        signal = self.service._detect_topic_shift(
            "这个怎么选",
            self.service._sanitize_summary({"conversation_goal": "高血压联合用药比较"}),
            self.service._sanitize_retrieval_summary({"focus_entities": ["ACEI", "CCB"]}),
            ["用户：ACEI 和 ARB 联用推荐吗", "助手：一般不推荐联用"],
        )
        self.assertFalse(bool(signal.get("topic_shift_detected")))
        self.assertEqual(str(signal.get("reason") or ""), "short_followup_whitelisted")

    def test_detect_repeat_question_should_reuse_answer_candidate(self) -> None:
        signal = self.service._detect_repeat_question(
            "ACEI 和 ARB 联用推荐吗",
            self.service._sanitize_summary({"answered_points": ["一般不推荐 ACEI 与 ARB 联用"]}),
            ["用户：ACEI 和 ARB 联用推荐吗", "助手：一般不推荐 ACEI 与 ARB 联用"],
        )
        self.assertTrue(bool(signal.get("repeat_question_detected")))
        self.assertGreater(float(signal.get("repeat_match_score") or 0.0), 0.7)

    def test_log_memory_quality_metrics_should_include_counts(self) -> None:
        with patch("app.backend.services.session_memory_service.logger.info") as info_mock:
            self.service._log_memory_quality_metrics(
                operation="resolve_query",
                session_id="s-metrics",
                projection_mode="digest_only",
                answered_points_count=1,
                covered_topics_count=2,
                focus_entities_count=3,
                risk_notes_count=4,
                question_scope="medical_qa",
                topic_shift_detected=True,
                topic_shift_reason="scope_mismatch_low_overlap",
                overlap_score=0.05,
            )
        payload_json = str(info_mock.call_args[0][1] or "{}")
        payload = json.loads(payload_json)
        self.assertEqual(str(payload.get("operation") or ""), "resolve_query")
        self.assertEqual(int(payload.get("focus_entities_count") or 0), 3)
        self.assertEqual(str(payload.get("topic_shift_reason") or ""), "scope_mismatch_low_overlap")


if __name__ == "__main__":
    unittest.main()
