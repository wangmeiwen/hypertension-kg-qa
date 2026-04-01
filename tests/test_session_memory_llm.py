"""session_memory_llm 解析单元测试。"""

import unittest

from app.backend.services.session_memory_llm import _extract_json_object


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


if __name__ == "__main__":
    unittest.main()
