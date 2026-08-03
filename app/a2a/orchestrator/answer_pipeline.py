"""结构化证据上下文驱动的终答生成管线。"""

from __future__ import annotations

from typing import Any, Callable

from app.a2a.models import AnswerGenerationContext


class AnswerPipeline:
    """只负责终答提示、流式生成和 token 统计。"""

    def __init__(self, *, llm_client: Any, timeout: float) -> None:
        self._llm_client = llm_client
        self._timeout = timeout

    @staticmethod
    def system_prompt() -> str:
        return (
            "你是高血压问答系统的最终回答生成器。\n"
            "请严格依据 AnswerGenerationContext 输出自然、完整、可直接展示给用户的最终答案。\n"
            "要求：\n"
            "1. 直接回答问题，不要输出 JSON，不要解释推理过程。\n"
            "2. 按 must_cover 数组顺序逐项回答，不得遗漏或合并问题点。\n"
            "3. 每项结论只能使用该项 supporting_evidence，并在同一句末尾附带对应 citation。\n"
            "4. 明确写出 evidence_policy.required_applicability_fields 中的适用条件。\n"
            "5. 数值、比较符、单位和测量场景必须与所引 Evidence 完全一致。\n"
            "6. 不得编造事实、来源、引用或确定性；存在未解决冲突时不得选择结论。\n"
            "7. 使用 Markdown 正常排版即可，允许列表和简短小标题。"
        )

    @staticmethod
    def user_prompt(*, question: str, answer_context: AnswerGenerationContext) -> str:
        return (
            f"用户问题：{question}\n\n"
            "AnswerGenerationContext：\n"
            + answer_context.model_dump_json(indent=2)
            + "\n\n请输出最终回答。"
        )

    async def stream(
        self,
        *,
        question: str,
        answer_context: AnswerGenerationContext,
        image_data: str,
        file_id: str,
        progress_callback: Callable[[dict[str, Any]], None] | None,
    ) -> tuple[str, dict[str, int]]:
        messages = [
            {"role": "system", "content": self.system_prompt()},
            {
                "role": "user",
                "content": self.user_prompt(
                    question=question,
                    answer_context=answer_context,
                ),
            },
        ]
        if progress_callback is not None:
            progress_callback(
                {
                    "event": "answer_stream_start",
                    "detail": "答案生成中",
                    "tool_name": "answer_stream",
                }
            )
        if image_data or file_id:
            event_iter = self._llm_client.stream_chat_with_vision_async(
                messages=messages,
                image_data=image_data,
                file_id=file_id,
                temperature=0.1,
                timeout=self._timeout,
                actor_role="controller",
                actor_name="orchestrator_final_answer",
            )
        else:
            event_iter = self._llm_client.stream_chat_with_tokens_async(
                messages=messages,
                temperature=0.1,
                timeout=self._timeout,
                actor_role="controller",
                actor_name="orchestrator_final_answer",
            )
        chunks: list[str] = []
        tokens = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        async for event in event_iter:
            if str(event.type or "") == "delta":
                delta = str(event.delta or "")
                if not delta:
                    continue
                chunks.append(delta)
                if progress_callback is not None:
                    progress_callback(
                        {
                            "event": "answer_delta",
                            "detail": delta,
                            "tool_name": "answer_stream",
                            "metadata": {"delta": delta},
                        }
                    )
            elif str(event.type or "") == "done":
                tokens = {
                    "prompt_tokens": int(getattr(event, "prompt_tokens", 0) or 0),
                    "completion_tokens": int(
                        getattr(event, "completion_tokens", 0) or 0
                    ),
                    "total_tokens": int(getattr(event, "total_tokens", 0) or 0),
                }
        return "".join(chunks).strip(), tokens
