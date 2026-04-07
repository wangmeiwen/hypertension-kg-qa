"""记忆优先与通用直答写作器。"""

from __future__ import annotations

from typing import Any


_CAPABILITY_LINES = [
    "我可以优先基于当前会话记住你刚刚说过的内容，回答重复追问、澄清问题和“你还记得吗”这类问题。",
    "如果你问的是高血压及相关心血管问题，我会先复用当前会话里已经答过的内容；记忆不够时，再补充知识图谱和文本检索。",
    "我还可以帮你总结前文、指出还缺哪些证据或哪些点还没回答完整。",
]


def build_memory_answer_text(answer_text: str, *, support_type: str = "", confidence: float = 0.0) -> str:
    """构建memory_answer_text相关信息并返回结果。
    
    Args:
        answer_text: 终答文本内容。
        support_type: 函数输入参数。
        confidence: 函数输入参数。
    
    Returns:
        str: 文本结果。
    """
    text = str(answer_text or "").strip()
    if not text:
        return "我先按当前会话记忆理解你的问题，但现在记忆里还没有足够信息直接回答。"

    basis = "这是基于当前会话记忆和前文信息整理的。"
    if support_type == "confirmed_fact":
        basis = "这是基于你前面明确告诉过我的信息。"
    elif support_type == "answered_point":
        basis = "这是基于我们前面已经讨论过的结论。"
    elif support_type == "recent_digest":
        basis = "这是基于刚才这段对话内容。"
    elif support_type == "carry_over_context":
        basis = "这是基于当前会话延续下来的上下文。"

    if confidence and confidence < 0.65:
        basis = basis + " 如果你愿意，也可以再补一句，我再按新的信息修正。"
    return f"{text}\n{basis}"


def build_assistant_capability_answer() -> str:
    """构建assistant_capability_answer相关信息并返回结果。
    
    Args:
        无。
    
    Returns:
        str: 文本结果。
    """
    return "我现在最擅长三类事：" + "".join(
        [f"\n{idx}. {line}" for idx, line in enumerate(_CAPABILITY_LINES, start=1)]
    )


def build_generic_direct_answer(question: str, memory_context: dict[str, Any]) -> str:
    """构建generic_direct_answer相关信息并返回结果。
    
    Args:
        question: 用户输入问题文本。
        memory_context: 会话记忆上下文。
    
    Returns:
        str: 文本结果。
    """
    recent_messages_digest = (
        memory_context.get("recent_messages_digest") if isinstance(memory_context.get("recent_messages_digest"), list) else []
    )
    if recent_messages_digest:
        return (
            f"这个问题我可以先按当前会话上下文直接接着答。你刚才这段对话里最近在聊的是："
            f"{str(recent_messages_digest[-1] or '').strip()}"
        )
    return f"这个问题我先不走医学检索，直接按当前会话上下文来回答。你可以继续具体说说：{str(question or '').strip()}"
