"""session_memory_llm 解析单元测试。"""

import asyncio
import json
import unittest

from app.backend.services.session_memory_llm import SessionMemoryLLM, _extract_json_object


class TestSessionMemoryLLM(unittest.TestCase):
    def test_extract_plain_json(self) -> None:
        text = '{"standalone_question":"Q1","intent":"direct_answer"}'
        data = _extract_json_object(text)
        self.assertEqual(data.get("standalone_question"), "Q1")

    def test_extract_fenced_json(self) -> None:
        text = """```json
{"standalone_question":"Q2","intent":"follow_up"}
```"""
        data = _extract_json_object(text)
        self.assertEqual(data.get("standalone_question"), "Q2")

    def test_extract_fenced_json_with_prefix_suffix_text(self) -> None:
        text = """以下是结果：
```json
{"standalone_question":"Q3","intent":"follow_up"}
```
请继续处理。"""
        data = _extract_json_object(text)
        self.assertEqual(data.get("standalone_question"), "Q3")

    def test_extract_invalid_json_should_fallback_empty_dict(self) -> None:
        text = "```json\n{invalid_json}\n```"
        data = _extract_json_object(text)
        self.assertEqual(data, {})

    def test_resolve_query_should_use_recent_messages_digest_only(self) -> None:
        class _FakeClient:
            def __init__(self) -> None:
                self.last_messages = []

            async def chat_with_tokens_async(self, **kwargs):
                self.last_messages = kwargs.get("messages") if isinstance(kwargs.get("messages"), list) else []

                class _Resp:
                    content = json.dumps(
                        {
                            "standalone_question": "Q",
                            "intent": "direct_answer",
                            "subqueries": ["Q"],
                            "must_cover": ["answer"],
                            "entities": [],
                            "filters": [],
                            "preferred_sources": ["kg"],
                            "carry_over_context": [],
                        },
                        ensure_ascii=False,
                    )

                return _Resp()

        async def _run_case() -> dict:
            client = _FakeClient()
            llm = SessionMemoryLLM(llm_client=client)
            result = await llm.resolve_query(
                input_text="那老年人呢",
                recent_messages_digest=["用户：ACEI 怎么用", "助手：已回答"],
                summary={},
                retrieval_summary={},
                session_id="s1",
            )
            payload_text = str(((client.last_messages[1] if len(client.last_messages) > 1 else {}).get("content") or ""))
            payload = json.loads(payload_text)
            return {"result": result, "payload": payload}

        case = asyncio.run(_run_case())
        payload = case.get("payload") if isinstance(case.get("payload"), dict) else {}
        self.assertIn("recent_messages_digest", payload)
        self.assertNotIn("recent_messages", payload)

    def test_summarize_turn_should_send_turn_memory_projection_only(self) -> None:
        class _FakeClient:
            def __init__(self) -> None:
                self.last_messages = []

            async def chat_with_tokens_async(self, **kwargs):
                self.last_messages = kwargs.get("messages") if isinstance(kwargs.get("messages"), list) else []

                class _Resp:
                    content = json.dumps(
                        {
                            "summary": {"version": 1, "last_message_id": 1},
                            "retrieval_summary": {"version": 1, "last_message_id": 1},
                        },
                        ensure_ascii=False,
                    )

                return _Resp()

        async def _run_case() -> dict:
            client = _FakeClient()
            llm = SessionMemoryLLM(llm_client=client)
            await llm.summarize_turn(
                previous_summary={"version": 1, "last_message_id": 1},
                previous_retrieval_summary={"version": 1, "last_message_id": 1},
                messages_delta=[{"id": 1, "role": "assistant", "content": "回答"}],
                turn_memory_projection={"answered_points": ["结论"], "question_scope": "medical_qa"},
                session_id="s1",
            )
            payload_text = str(((client.last_messages[1] if len(client.last_messages) > 1 else {}).get("content") or ""))
            return json.loads(payload_text)

        payload = asyncio.run(_run_case())
        self.assertIn("turn_memory_projection", payload)
        self.assertNotIn("turn_artifacts", payload)


if __name__ == "__main__":
    unittest.main()
