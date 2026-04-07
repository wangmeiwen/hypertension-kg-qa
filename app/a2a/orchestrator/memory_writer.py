"""记忆优先与通用直答写作器。"""

from __future__ import annotations

from typing import Any


_CAPABILITY_LINES = [
    "回答高血压及相关心血管问题，并在需要时调用知识图谱和文本检索补充依据。",
    "处理明确的记忆查询，例如姓名、偏好、已确认事实等可用信息。",
    "回答轻量通用问题，尽量用直接、自然的方式给出结果。",
]


def build_memory_direct_text(answer_text: str, *, support_type: str = "", confidence: float = 0.0) -> str:
    """构建会话记忆直答文本并返回结果。
    
    Args:
        answer_text: 终答文本内容。
        support_type: 函数输入参数。
        confidence: 函数输入参数。
    
    Returns:
        str: 文本结果。
    """
    text = str(answer_text or "").strip()
    if not text:
        return "我暂时没有足够信息直接回答这个问题。"
    if confidence and confidence < 0.65:
        return f"{text}\n如果需要，我可以根据你补充的新信息修正答案。"
    _ = support_type
    return text


def build_capability_direct_answer() -> str:
    """构建助手能力直答文本并返回结果。
    
    Args:
        无。
    
    Returns:
        str: 文本结果。
    """
    return "我可以帮你做这些事：" + "".join(
        [f"\n{idx}. {line}" for idx, line in enumerate(_CAPABILITY_LINES, start=1)]
    )


def build_context_direct_answer(question: str, memory_context: dict[str, Any]) -> str:
    """构建轻量上下文直答文本并返回结果。
    
    Args:
        question: 用户输入问题文本。
        memory_context: 会话记忆上下文。
    
    Returns:
        str: 文本结果。
    """
    _ = memory_context
    text = str(question or "").strip()
    if not text:
        return "可以，请直接告诉我你想处理的问题。"
    return f"可以。关于“{text}”，我会按通用问题直接处理；如果你需要更具体的结果，可以补充目标或格式要求。"
