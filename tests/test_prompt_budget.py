"""主控提示词预算与压缩行为测试。"""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace
import types

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

fake_mcp_tools = types.ModuleType("app.mcp.tools")
fake_mcp_tools.get_mcp_server = lambda: SimpleNamespace(ainvoke_batch=None)
sys.modules.setdefault("app.mcp.tools", fake_mcp_tools)

from app.a2a.orchestrator import controller as controller_module
from app.a2a.orchestrator.controller import UnifiedOrchestratorController


class DummyConf:
    A2A_SUBAGENT_MAX_TOOL_CALLS_DEFAULT = 2
    A2A_SUBAGENT_MAX_TOOL_CALLS_MAX = 4


class DummyLLMClient:
    """用于测试的最小 LLM 客户端桩。"""

    def __init__(self, content: str):
        """初始化固定返回内容并记录调用入参。"""
        self._content = content
        self.calls: list[list[dict[str, str]]] = []

    async def chat_with_tokens_async(self, messages, temperature=0.1, timeout=90):
        """模拟异步 LLM 调用并返回固定 token 使用量。"""
        self.calls.append(messages)
        return SimpleNamespace(
            content=self._content,
            prompt_tokens=11,
            completion_tokens=5,
            total_tokens=16,
        )


class DummySubAgentClient:
    """用于测试的子代理桩，禁止真实下游调用。"""

    async def send_task(self, task):
        """若被调用直接失败，确保该测试不走子代理路径。"""
        raise AssertionError("send_task should not be called in prompt budget tests")


def test_count_prompt_tokens_uses_jieba(monkeypatch):
    """验证分词计数逻辑会调用 jieba 并返回分词数量。"""
    class FakeJieba:
        @staticmethod
        def lcut(text: str):
            """模拟 jieba.lcut 返回固定词元列表。"""
            assert "system prompt" in text
            assert "user prompt" in text
            return ["高血压", "治疗", "方案"]

    monkeypatch.setattr(controller_module, "jieba", FakeJieba)
    controller = UnifiedOrchestratorController(
        DummySubAgentClient(),
        conf=DummyConf(),
        llm_client=DummyLLMClient('{"action":"final_answer","answer":"ok"}'),
    )
    count = controller._count_prompt_tokens(
        [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "user prompt"},
        ]
    )
    assert count == 3


def test_run_aborts_after_two_failed_compressions(monkeypatch):
    """验证两次压缩后仍超预算时会中止并返回保护性输出。"""
    controller = UnifiedOrchestratorController(
        DummySubAgentClient(),
        conf=DummyConf(),
        llm_client=DummyLLMClient('{"action":"final_answer","answer":"ok"}'),
    )

    async def fake_compress(**kwargs):
        """模拟压缩结果，始终不改变超预算事实。"""
        return (
            {
                "qa_memory_text": "当前暂无问答对",
                "retrieval_question_index_text": "当前暂无历史检索问题",
                "tool_observations": [],
                "compression_reason": "测试压缩",
            },
            {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
        )

    monkeypatch.setattr(controller, "_compress_prompt_sections", fake_compress)
    monkeypatch.setattr(controller, "_count_prompt_tokens", lambda messages: 10001)

    result = asyncio.run(
        controller.run(
            input_text="这个问题非常长",
            session_id="session-1",
            memory_context={
                "qa_memory_text": "Q1/A1",
                "retrieval_question_index_text": "history-1",
            },
        )
    )

    assert result["output"] == "这个问题超出软件能力范围，无法回答"
    assert result["output_type"] == "orchestrator_aborted_over_budget"
    assert result["telemetry"]["compression_count"] == 2
    assert result["telemetry"]["aborted_for_prompt_budget"] is True


def test_run_succeeds_after_single_compression(monkeypatch):
    """验证一次压缩后回到预算内可正常生成终答。"""
    llm_client = DummyLLMClient('{"action":"final_answer","reason":"enough","answer":"压缩后可以回答"}')
    controller = UnifiedOrchestratorController(
        DummySubAgentClient(),
        conf=DummyConf(),
        llm_client=llm_client,
    )

    counts = iter([12001, 8000])

    async def fake_compress(**kwargs):
        """模拟压缩后显著减少上下文内容。"""
        return (
            {
                "qa_memory_text": "压缩后的相关问答",
                "retrieval_question_index_text": "压缩后的相关检索问题",
                "tool_observations": [],
                "compression_reason": "只保留相关内容",
            },
            {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
        )

    monkeypatch.setattr(controller, "_compress_prompt_sections", fake_compress)
    monkeypatch.setattr(controller, "_count_prompt_tokens", lambda messages: next(counts))

    result = asyncio.run(
        controller.run(
            input_text="当前问题",
            session_id="session-2",
            memory_context={
                "qa_memory_text": "原始问答",
                "retrieval_question_index_text": "原始检索",
            },
        )
    )

    assert result["output"] == "压缩后可以回答"
    assert result["output_type"] == "orchestrator_final"
    assert result["telemetry"]["compression_count"] == 1
    assert result["telemetry"]["aborted_for_prompt_budget"] is False
    assert llm_client.calls
