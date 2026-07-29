"""会话流式输出服务。"""

from __future__ import annotations

import asyncio
from datetime import datetime
import json
import logging
from pathlib import Path
import re
import time
from typing import Any, AsyncIterator, Dict, List, Optional

from pydantic import ValidationError

from app.a2a.models import Evidence
from app.backend.domain.stream_events import (
    DoneStreamEvent,
    ErrorStreamEvent,
    PersistedStreamEvent,
    ProgressStreamEvent,
    ReplyStreamEvent,
    SessionStreamEvent,
    stream_event_json_line,
    validate_stream_event,
)
from app.backend.repositories.session_manager import MessageManager
from app.backend.services.a2a_event_stream_service import A2AEventStreamService
from app.backend.services.session_log_repository import (
    read_request_records,
    safe_log_id,
)
from app.backend.services.session_memory_service import SessionMemoryService
from app.core.perf_logger import log_perf_event
from app.backend.observability.session_logger import (
    append_jsonl_atomic,
    ensure_session_meta,
    log_session_op,
    log_tool_event,
    reset_request_context,
    reset_session_context,
    set_request_context,
    set_session_context,
)

logger = logging.getLogger(__name__)

MEDICAL_DISCLAIMER_TEXT = "以上内容仅供参考，不构成诊断或治疗建议，不能替代专业医生意见；如有不适或紧急情况，请及时就医。"
MEDICAL_DISCLAIMER_REQUIRED_MARKERS = ("仅供参考", "不构成诊断")
MEDICAL_DISCLAIMER_SUPPORT_MARKERS = (
    "不能替代专业医生意见",
    "不能替代",
    "及时就医",
    "紧急情况",
)


def _sanitize_text(value: Any, max_len: int) -> str:
    """清洗并截断用于日志、摘要和前端展示的文本。

    Args:
        value: 待处理输入值。
        max_len: 最多保留的字符数量。

    Returns:
        str: 去除换行并按长度裁剪后的文本。
    """
    return str(value or "").replace("\n", " ").strip()[:max_len]


def _json_text_len(value: Any) -> int:
    """计算对象 JSON 序列化后的文本长度。

    Args:
        value: 待处理输入值。

    Returns:
        int: JSON 文本长度，无法序列化时返回普通字符串长度。
    """
    try:
        return len(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError) as exc:
        logger.debug(
            "session_stream_json_text_len_fallback: value_type=%s error_type=%s error=%s",
            type(value).__name__,
            type(exc).__name__,
            exc,
        )
        return len(str(value or ""))


def _to_int_metric(value: Any) -> int:
    """将宽松指标值转换为非负整数。"""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float):
        return max(0, int(value))
    if isinstance(value, str):
        try:
            return max(0, int(float(value)))
        except ValueError as exc:
            logger.debug(
                "session_stream_int_metric_parse_failed: raw=%s error_type=%s error=%s",
                value,
                type(exc).__name__,
                exc,
            )
            return 0
    return 0


def _build_reply_digest(reply: str, max_len: int = 180) -> str:
    """构建助手回复摘要，优先提取结构化回答段落。

    Args:
        reply: 完整助手回复。
        max_len: 摘要最长字符数。

    Returns:
        str: 去除固定医疗免责文本后的短摘要。
    """
    text = _strip_medical_disclaimer(reply).replace("\n", " ").strip()
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    segments: list[str] = []
    for marker in ("结论：", "证据支持点：", "不确定性与适用边界："):
        pos = text.find(marker)
        if pos >= 0:
            snippet = text[pos : pos + 48].strip()
            if snippet:
                segments.append(snippet)
    if segments:
        return _sanitize_text(" | ".join(segments), max_len)
    return _sanitize_text(f"{text[:80]}...{text[-28:]}", max_len)


def _strip_medical_disclaimer(reply: Any) -> str:
    """移除回答末尾固定医疗免责文本，便于摘要提取和重复检测。"""
    text = str(reply or "").rstrip()
    if not text:
        return ""
    if text.endswith(MEDICAL_DISCLAIMER_TEXT):
        return text[: -len(MEDICAL_DISCLAIMER_TEXT)].rstrip()
    return text


def _normalize_disclaimer_text(reply: Any) -> str:
    """去掉 Markdown 包裹和空白，便于识别模型自带的免责文本。"""
    text = str(reply or "")
    text = re.sub(r"[*_`>#\-\s]+", "", text)
    return text.replace("，", ",").replace("；", ";").replace("。", ".")


def _contains_medical_disclaimer(reply: Any) -> bool:
    """判断回答末尾是否已经包含医学免责语义，避免兜底文本重复出现。"""
    normalized_tail = _normalize_disclaimer_text(str(reply or "")[-700:])
    if not normalized_tail:
        return False
    has_required = all(
        marker in normalized_tail for marker in MEDICAL_DISCLAIMER_REQUIRED_MARKERS
    )
    has_support = any(
        marker in normalized_tail for marker in MEDICAL_DISCLAIMER_SUPPORT_MARKERS
    )
    return has_required and has_support


def append_medical_disclaimer_if_needed(reply: Any, question_scope: Any) -> str:
    """仅对医疗回答统一追加固定免责文本，并避免重复追加。"""
    text = str(reply or "").rstrip()
    if not text:
        return ""
    if _normalize_question_scope(question_scope) != "medical_qa":
        return text
    if _contains_medical_disclaimer(text):
        return text
    base_text = _strip_medical_disclaimer(text).rstrip()
    if not base_text:
        return MEDICAL_DISCLAIMER_TEXT
    return f"{base_text}\n\n{MEDICAL_DISCLAIMER_TEXT}"


def _sanitize_list(value: Any, max_items: int, max_len: int) -> List[str]:
    """清洗字符串列表并执行去重、截断和数量限制。

    Args:
        value: 待处理输入值。
        max_items: 最多保留的列表项数量。
        max_len: 单项最多保留的字符数量。

    Returns:
        List[str]: 清洗后的字符串列表。
    """
    rows_obj = value if isinstance(value, list) else []
    rows: List[str] = []
    seen: set[str] = set()
    for item in rows_obj:
        text = _sanitize_text(item, max_len)
        if not text:
            continue
        if text in seen:
            continue
        seen.add(text)
        rows.append(text)
        if len(rows) >= max_items:
            break
    return rows


def _extract_section_sentences(
    reply: str, title: str, next_titles: tuple[str, ...], max_items: int = 2
) -> List[str]:
    """从结构化回答段落中提取若干句子。

    Args:
        reply: 完整回答文本。
        title: 当前段落标题。
        next_titles: 后续段落标题，用于确定当前段落边界。
        max_items: 最多提取的句子数量。

    Returns:
        List[str]: 段落中的短句列表。
    """
    text = str(reply or "").replace("**", "").strip()
    if not text:
        return []
    marker = f"{title}："
    start = text.find(marker)
    if start < 0:
        return []
    start += len(marker)
    end = len(text)
    for next_title in next_titles:
        next_marker = f"{next_title}："
        pos = text.find(next_marker, start)
        if pos >= 0:
            end = min(end, pos)
    section = text[start:end].replace("\n", " ").strip()
    if not section:
        return []
    rows: List[str] = []
    seen: set[str] = set()
    for item in re.split(r"[。；;!！?\?]\s*", section):
        sentence = _sanitize_text(item, 120)
        if not sentence or sentence in seen:
            continue
        seen.add(sentence)
        rows.append(sentence)
        if len(rows) >= max_items:
            break
    return rows


def _extract_answered_points(
    evidence_summary: Dict[str, Any], full_reply: str
) -> List[str]:
    """提取本轮回答覆盖的关键点。

    Args:
        evidence_summary: A2A 产出的证据摘要。
        full_reply: 完整助手回复。

    Returns:
        List[str]: 用于记忆投影的关键回答点。
    """
    supported_points = _sanitize_list(
        evidence_summary.get("supported_points"), max_items=5, max_len=120
    )
    if supported_points:
        return supported_points
    conclusion_points = _extract_section_sentences(
        full_reply,
        "结论",
        ("证据支持点", "不确定性与适用边界"),
        max_items=2,
    )
    evidence_points = _extract_section_sentences(
        full_reply,
        "证据支持点",
        ("不确定性与适用边界",),
        max_items=3,
    )
    return _sanitize_list(
        [*conclusion_points, *evidence_points], max_items=5, max_len=120
    )


def _infer_question_scope(input_text: str) -> str:
    """基于简单关键词推断问题范围。

    Args:
        input_text: 用户输入文本。

    Returns:
        str: 问题范围枚举值。
    """
    lowered = str(input_text or "").strip().lower()
    if not lowered:
        return "unknown"
    if any(
        hint in lowered
        for hint in (
            "你能做什么",
            "你是谁",
            "功能",
            "能力",
            "what can you do",
            "who are you",
            "capability",
        )
    ):
        return "capability_query"
    if any(
        hint in lowered
        for hint in (
            "登录",
            "退出",
            "会话列表",
            "管理后台",
            "admin",
            "session",
            "status",
            "readiness",
        )
    ):
        return "administrative"
    if any(
        hint in lowered
        for hint in (
            "你好",
            "您好",
            "谢谢",
            "在吗",
            "早上好",
            "晚上好",
            "hello",
            "hi",
            "thanks",
        )
    ):
        return "small_talk"
    if re.search(r"血压|用药|疗效|安全|指南|acei|arb|ccb", lowered):
        return "medical_qa"
    return "unknown"


def _normalize_question_scope(scope: Any) -> str:
    """规范化问题范围枚举值。

    Args:
        scope: 原始问题范围值。

    Returns:
        str: 当前支持的问题范围枚举，未知值返回 unknown。
    """
    value = str(scope or "").strip()
    if value in {
        "medical_qa",
        "capability_query",
        "administrative",
        "small_talk",
        "unknown",
    }:
        return value
    return "unknown"


def _project_root() -> Path:
    """定位项目根目录。

    Args:
        无。

    Returns:
        Path: 当前仓库根目录路径。
    """
    return Path(__file__).resolve().parents[3]


def _append_jsonl(file_path: Path, payload: Dict[str, Any]) -> None:
    """以原子追加方式写入一行 JSONL 日志。

    Args:
        file_path: JSONL 文件路径。
        payload: 待写入的日志对象。

    Returns:
        None: 无返回值。
    """
    append_jsonl_atomic(file_path, payload)


def _safe_session_log_id(raw: Any, fallback: str = "unknown") -> str:
    """将会话标识转换为可安全用于日志目录名的文本。

    Args:
        raw: 原始会话标识。
        fallback: 清洗后为空时使用的兜底文本。

    Returns:
        str: 仅包含字母、数字、短横线和下划线的目录名片段。
    """
    return safe_log_id(raw, fallback)


def _iso_now() -> str:
    """返回当前本地时间的 ISO 文本。

    Args:
        无。

    Returns:
        str: 秒级精度 ISO 时间文本。
    """
    return datetime.now().isoformat(timespec="seconds")


def _to_iso(value: Any) -> str:
    """把外部时间值规范化为 ISO 文本，为空时使用当前时间。

    Args:
        value: 待处理输入值。

    Returns:
        str: 输入时间文本或当前时间文本。
    """
    text = str(value or "").strip()
    return text or _iso_now()


def _sanitize_preview(value: Any, max_len: int = 160) -> str:
    """清洗用于预览展示的短文本。

    Args:
        value: 待处理输入值。
        max_len: 最多保留的字符数量。

    Returns:
        str: 预览文本。
    """
    return _sanitize_text(value, max_len)


def _summarize_tool_result(result_payload: Any) -> str:
    """从工具结果中提取可展示摘要。

    Args:
        result_payload: 工具返回 payload。

    Returns:
        str: 优先提取错误、summary、message、answer 或列表规模的摘要。
    """
    result = result_payload if isinstance(result_payload, dict) else {}
    if isinstance(result.get("error"), str) and str(result.get("error")).strip():
        return _sanitize_preview(f"返回错误：{result.get('error')}", 180)
    result_obj = result.get("result")
    if isinstance(result_obj, dict):
        for key in ("summary", "message", "answer", "reason"):
            text = _sanitize_preview(result_obj.get(key), 180)
            if text:
                return text
        for key in ("items", "evidence", "entities", "relationships"):
            rows = result_obj.get(key)
            if isinstance(rows, list) and rows:
                return f"{key} 共 {len(rows)} 项"
    return "已返回结果"


def _normalize_timestamp(value: Any, fallback: str) -> str:
    """规范化日志时间戳，为空时使用兜底时间。

    Args:
        value: 待处理输入值。
        fallback: 时间为空时使用的兜底文本。

    Returns:
        str: 规范化后的时间文本。
    """
    text = str(value or "").strip()
    return text or fallback


def _extract_llm_role_from_compact(messages_compact: str) -> str:
    """从压缩后的 LLM 输入日志中推断代理泳道。

    Args:
        messages_compact: LLM 输入日志压缩文本。

    Returns:
        str: 主控、图谱、检索或记忆压缩泳道名称。
    """
    text = str(messages_compact or "")
    if "会话问题改写器" in text:
        return "主控编排"
    if "医疗问答终答写作器" in text:
        return "主控编排"
    if "会话记忆更新器" in text:
        return "记忆压缩"
    if "Graph Agent" in text or "图谱" in text:
        return "图谱代理"
    if "Retrieval Agent" in text or "检索" in text:
        return "检索代理"
    return "主控编排"


def _read_session_llm_records(session_id: str, request_id: str) -> list[dict[str, Any]]:
    """读取指定会话和请求的 LLM 调用日志。

    Args:
        session_id: 会话标识。
        request_id: 请求标识。

    Returns:
        list[dict[str, Any]]: 按时间排序的 LLM 日志记录。
    """
    return read_request_records(
        project_root=_project_root(),
        session_id=session_id,
        request_id=request_id,
        category="llm",
        logger=logger,
        warning_event="session_stream_skip_invalid_llm_log_file",
    )


def _build_lane_card_from_tool_observation(
    observation: Dict[str, Any],
    fallback_time: str,
) -> Dict[str, Any]:
    """根据工具观测构建一个代理泳道展示卡片。

    Args:
        observation: 单条工具观测记录。
        fallback_time: 工具日志缺失时间时使用的兜底时间。

    Returns:
        Dict[str, Any]: 管理端可展示的泳道卡片。
    """
    tool_name = str(
        observation.get("tool_name") or observation.get("requested_tool_name") or ""
    ).strip()
    question = str(
        observation.get("subquery") or observation.get("question") or ""
    ).strip()
    action = str(observation.get("action") or "").strip()
    agent = str(observation.get("agent") or "").strip().lower()
    if agent == "kg" or "kg_" in tool_name:
        lane_name = "图谱代理"
    elif agent == "rag" or "rag" in tool_name.lower():
        lane_name = "检索代理"
    elif agent == "history" or "history" in tool_name.lower():
        lane_name = "主控编排"
    else:
        lane_name = "主控编排"
    result_value = observation.get("result")
    result_payload = result_value if isinstance(result_value, dict) else {}
    return {
        "lane_name": lane_name,
        "title": "执行工具调用",
        "occurred_at": fallback_time,
        "prompt_preview": _sanitize_preview(question or f"执行 {tool_name}", 140),
        "decision_preview": _summarize_tool_result(result_payload),
        "full_prompt": question or f"执行动作：{action}；工具：{tool_name}",
        "full_decision": _sanitize_preview(
            json.dumps(result_payload, ensure_ascii=False), 1200
        ),
        "status": "成功" if bool(result_payload.get("ok", True)) else "失败",
    }


def _build_orchestrator_cards(
    input_text: str,
    memory_context: Dict[str, Any],
    telemetry: Dict[str, Any],
    llm_rows: list[dict[str, Any]],
    started_at: str,
    finished_at: str,
) -> list[Dict[str, Any]]:
    """构建主控编排泳道的展示卡片。

    Args:
        input_text: 用户输入文本。
        memory_context: 本轮进入主控前的记忆上下文。
        telemetry: A2A 主控返回的遥测信息。
        llm_rows: 本次请求关联的 LLM 日志记录。
        started_at: 本轮开始时间。
        finished_at: 本轮结束时间。

    Returns:
        list[Dict[str, Any]]: 主控决策和主控 LLM 调用卡片列表。
    """
    cards: list[Dict[str, Any]] = [
        {
            "title": "主控统一决策",
            "started_at": started_at,
            "finished_at": finished_at,
            "prompt_preview": _sanitize_preview(input_text, 140),
            "decision_preview": _sanitize_preview(
                (
                    f"controller_mode={telemetry.get('controller_mode') or 'explicit_memory_with_subagents'}；"
                    f"retrieval_round={telemetry.get('retrieval_round') or 0}；"
                    f"compression_count={telemetry.get('compression_count') or 0}；"
                    f"aborted_for_prompt_budget={'yes' if bool(telemetry.get('aborted_for_prompt_budget')) else 'no'}"
                ),
                180,
            ),
            "full_prompt": _sanitize_preview(
                json.dumps(
                    {
                        "question": input_text,
                        "qa_memory_text": memory_context.get("qa_memory_text"),
                        "retrieval_question_index_text": memory_context.get(
                            "retrieval_question_index_text"
                        ),
                    },
                    ensure_ascii=False,
                ),
                1200,
            ),
            "full_decision": _sanitize_preview(
                json.dumps(telemetry, ensure_ascii=False), 1200
            ),
            "status": "完成",
        }
    ]
    for row in llm_rows:
        lane_name = _extract_llm_role_from_compact(
            str(row.get("messages_compact") or "")
        )
        if lane_name != "主控编排":
            continue
        cards.append(
            {
                "title": "模型输入与输出",
                "started_at": _normalize_timestamp(row.get("ts"), started_at),
                "finished_at": _normalize_timestamp(row.get("ts"), finished_at),
                "prompt_preview": _sanitize_preview(
                    row.get("messages_compact") or "", 160
                ),
                "decision_preview": _sanitize_preview(
                    row.get("response_compact") or row.get("error") or "", 160
                ),
                "full_prompt": str(row.get("messages_compact") or ""),
                "full_decision": str(
                    row.get("response_compact") or row.get("error") or ""
                ),
                "status": "失败" if str(row.get("error") or "").strip() else "完成",
            }
        )
    return cards


def _build_memory_compression_stages(
    *,
    input_text: str,
    full_reply: str,
    memory_context: Dict[str, Any],
    turn_projection: Dict[str, Any],
    post_memory: Optional[Dict[str, Any]],
    started_at: str,
    finished_at: str,
    compressed_at: str,
) -> list[Dict[str, Any]]:
    """构建管理端展示的记忆压缩/写回阶段说明。

    Args:
        input_text: 用户输入文本。
        full_reply: 完整助手回复。
        memory_context: 会话记忆上下文。
        turn_projection: 从本轮回答和工具结果中提取的记忆投影。
        post_memory: 写回后的记忆快照，异步写回未完成时可为空。
        started_at: 本轮开始时间。
        finished_at: 本轮结束时间。
        compressed_at: 记忆写回完成时间。

    Returns:
        list[Dict[str, Any]]: 管理端可展示的记忆阶段列表。
    """
    qa_before_value = memory_context.get("qa_pairs")
    qa_before = qa_before_value if isinstance(qa_before_value, list) else []
    retrieval_before_value = memory_context.get("retrieval_question_index")
    retrieval_before = (
        retrieval_before_value if isinstance(retrieval_before_value, list) else []
    )
    post_memory_value = post_memory if isinstance(post_memory, dict) else {}
    qa_memory_value = post_memory_value.get("qa_memory")
    qa_memory = qa_memory_value if isinstance(qa_memory_value, dict) else {}
    retrieval_memory_value = post_memory_value.get("retrieval_memory")
    retrieval_memory = (
        retrieval_memory_value if isinstance(retrieval_memory_value, dict) else {}
    )
    qa_after = qa_memory.get("qa_pairs")
    retrieval_after = retrieval_memory.get("questions")
    qa_after_rows = qa_after if isinstance(qa_after, list) else []
    retrieval_after_rows = retrieval_after if isinstance(retrieval_after, list) else []
    return [
        {
            "stage_name": "原始消息增量",
            "event_time": started_at,
            "description": "本轮新进入系统的用户问题与最终回答摘要。",
            "content_lines": [
                f"用户问题：{_sanitize_preview(input_text, 180)}",
                f"助手回答摘要：{_sanitize_preview(_build_reply_digest(full_reply), 180)}",
            ],
        },
        {
            "stage_name": "本轮记忆投影",
            "event_time": finished_at,
            "description": "从本轮对话中提炼出的显式 QA 与检索记录写回内容。",
            "content_lines": [
                f"本轮问题：{_sanitize_preview(turn_projection.get('user_question') or input_text, 180)}",
                f"写入 Evidence 数：{len(turn_projection.get('evidence') or [])}",
                f"检索预览：{'；'.join(turn_projection.get('query_preview') or []) or '无'}",
            ],
        },
        {
            "stage_name": "写回前记忆",
            "event_time": started_at,
            "description": "进入本轮前，会话中已有的显式问答记忆与历史检索索引。",
            "content_lines": [
                f"QA 条数：{len(qa_before)}",
                f"最近问题：{_sanitize_preview((qa_before[-1] if qa_before else {}).get('user_question') or '无', 180)}",
                f"历史检索问题数：{len(retrieval_before)}",
            ],
        },
        {
            "stage_name": "写回后记忆",
            "event_time": compressed_at,
            "description": "后台写回完成后，会话中的显式 QA 与历史检索状态。",
            "content_lines": [
                f"QA 条数：{len(qa_after_rows)}",
                f"最近问题：{_sanitize_preview((qa_after_rows[-1] if qa_after_rows else {}).get('user_question') or '写回结果暂不可用', 180)}",
                f"历史检索问题数：{len(retrieval_after_rows)}",
            ],
        },
    ]


def _build_turn_memory_projection(
    *,
    input_text: str,
    user_message_id: int,
    full_reply: str,
    processing_steps: List[str],
    tool_observations: List[Dict[str, Any]],
    evidence_summary: Dict[str, Any],
) -> Dict[str, Any]:
    """从本轮回答和工具观测中构建记忆写回投影。

    Args:
        input_text: 用户输入文本。
        user_message_id: 用户消息 ID。
        full_reply: 完整助手回复。
        processing_steps: A2A 主控处理步骤。
        tool_observations: A2A 工具观测列表。
        evidence_summary: 证据摘要。

    Returns:
        Dict[str, Any]: 可传给 SessionMemoryService 的 turn artifacts。
    """
    _ = processing_steps
    _ = evidence_summary
    evidence: List[Dict[str, Any]] = []
    query_preview: List[str] = []
    seen_evidence: set[tuple[str, str, str, str]] = set()
    captured_at = _iso_now()
    for observation in tool_observations:
        if not isinstance(observation, dict):
            continue
        raw_evidence = observation.get("evidence")
        if not isinstance(raw_evidence, list):
            result_value = observation.get("result")
            result_obj = result_value if isinstance(result_value, dict) else {}
            payload_value = result_obj.get("result")
            payload = payload_value if isinstance(payload_value, dict) else {}
            evidence_value = payload.get("evidence")
            raw_evidence = evidence_value if isinstance(evidence_value, list) else []
        for item in raw_evidence if isinstance(raw_evidence, list) else []:
            if not isinstance(item, dict):
                continue
            try:
                normalized = Evidence.model_validate(item).model_dump()
            except ValidationError:
                continue
            fingerprint = (
                str(normalized.get("source_type") or ""),
                str(normalized.get("source_id") or ""),
                str(normalized.get("chunk_id") or ""),
                str(normalized.get("content") or "")[:160],
            )
            if fingerprint in seen_evidence:
                continue
            seen_evidence.add(fingerprint)
            evidence.append(normalized)
        agent = _sanitize_text(observation.get("agent"), 24) or "unknown"
        subquery = _sanitize_text(observation.get("subquery"), 100)
        if subquery:
            preview = f"{agent}:{subquery}"
            if preview not in query_preview:
                query_preview.append(preview)
    return {
        "user_question": str(input_text or ""),
        "user_message_id": int(user_message_id or 0),
        "captured_at": captured_at,
        "evidence": evidence,
        "query_preview": query_preview[:5],
        "assistant_reply_digest": _build_reply_digest(full_reply),
        "question_scope": _normalize_question_scope(_infer_question_scope(input_text)),
        "projection_mode": "explicit_memory_v2",
        "assistant_reply_chars": len(str(full_reply or "")),
        "tool_observations_count": len(tool_observations),
        "tool_observations_chars": _json_text_len(tool_observations),
    }


def _collect_request_llm_metrics(session_id: str, request_id: str) -> Dict[str, Any]:
    """收集单次请求的 LLM 调用次数、token 和错误信息。

    Args:
        session_id: 会话标识。
        request_id: 请求标识。

    Returns:
        Dict[str, Any]: LLM 调用记录、调用次数、token 总量和错误计数。
    """
    rows = _read_session_llm_records(session_id, request_id)
    total_tokens = 0
    llm_calls = 0
    error_types: dict[str, int] = {}
    for row in rows:
        llm_calls += 1
        try:
            total_tokens += int(row.get("total_tokens") or 0)
        except (TypeError, ValueError):
            total_tokens += 0
        error_type = str(row.get("error") or "").strip()
        if error_type:
            error_types[error_type] = int(error_types.get(error_type) or 0) + 1
    return {
        "llm_calls": llm_calls,
        "total_tokens": total_tokens,
        "llm_rows": rows,
        "error_types": error_types,
    }


def _merge_llm_metrics_with_telemetry(
    llm_metrics: Dict[str, Any], telemetry: Dict[str, Any]
) -> Dict[str, Any]:
    """LLM 明细日志缺失时，用 A2A telemetry 兜底 token 指标。"""
    metrics = dict(llm_metrics)
    telemetry_tokens = _to_int_metric(telemetry.get("total_tokens"))
    telemetry_calls = _to_int_metric(telemetry.get("llm_calls"))
    if _to_int_metric(metrics.get("total_tokens")) <= 0 and telemetry_tokens > 0:
        metrics["total_tokens"] = telemetry_tokens
    if _to_int_metric(metrics.get("llm_calls")) <= 0:
        if telemetry_calls > 0:
            metrics["llm_calls"] = telemetry_calls
        elif _to_int_metric(metrics.get("total_tokens")) > 0:
            metrics["llm_calls"] = 1
    return metrics


def _derive_tool_metrics(tool_observations: List[Dict[str, Any]]) -> Dict[str, Any]:
    """从工具观测中派生工具调用成功率和错误分类。

    Args:
        tool_observations: A2A 工具观测列表。

    Returns:
        Dict[str, Any]: 工具调用数、阻断数、成功率和错误计数。
    """
    attempted_tool_actions = 0
    blocked_tool_actions = 0
    tool_calls = 0
    success_count = 0
    strategy_error_types: dict[str, int] = {}
    execution_error_types: dict[str, int] = {}

    for row in tool_observations:
        if not isinstance(row, dict):
            continue
        attempted_tool_actions += 1
        if str(row.get("blocked_reason") or "").strip():
            blocked_tool_actions += 1
            key = str(row.get("blocked_reason") or "blocked")
            strategy_error_types[key] = int(strategy_error_types.get(key) or 0) + 1
            continue
        tool_calls += 1
        result_value = row.get("result")
        result_obj = result_value if isinstance(result_value, dict) else {}
        ok = bool(result_obj.get("ok", True))
        if ok:
            success_count += 1
        else:
            key = str(result_obj.get("error") or "tool_error")
            execution_error_types[key] = int(execution_error_types.get(key) or 0) + 1

    success_rate = (
        round(float(success_count / tool_calls), 4) if tool_calls > 0 else 0.0
    )
    attempted_success_rate = (
        round(float(success_count / attempted_tool_actions), 4)
        if attempted_tool_actions > 0
        else 0.0
    )
    return {
        "tool_calls": tool_calls,
        "attempted_tool_actions": attempted_tool_actions,
        "blocked_tool_actions": blocked_tool_actions,
        "tool_success_rate": success_rate,
        "execution_tool_success_rate": success_rate,
        "attempted_action_success_rate": attempted_success_rate,
        "strategy_error_types": strategy_error_types,
        "execution_error_types": execution_error_types,
    }


def _build_turn_observability_payload(
    *,
    session_id: str,
    user_id: str,
    request_id: str,
    user_message_id: int,
    assistant_message_id: int,
    input_text: str,
    full_reply: str,
    memory_context: Dict[str, Any],
    processing_steps: List[str],
    tool_observations: List[Dict[str, Any]],
    evidence_summary: Dict[str, Any],
    telemetry: Dict[str, Any],
    started_at: str,
    finished_at: str,
    duration_ms: float,
    status: str,
    turn_artifacts: Dict[str, Any],
) -> Dict[str, Any]:
    """构建单轮会话可观测性日志 payload。

    Args:
        session_id: 会话标识。
        user_id: 用户标识。
        request_id: 请求标识。
        user_message_id: 用户消息 ID。
        assistant_message_id: 助手消息 ID。
        input_text: 用户输入文本。
        full_reply: 完整助手回复。
        memory_context: 会话记忆上下文。
        processing_steps: A2A 主控处理步骤。
        tool_observations: A2A 工具观测列表。
        evidence_summary: 证据摘要。
        telemetry: A2A 主控遥测信息。
        started_at: 本轮开始时间。
        finished_at: 本轮结束时间。
        duration_ms: 本轮耗时毫秒数。
        status: 本轮最终状态。
        turn_artifacts: 本轮记忆投影数据。

    Returns:
        Dict[str, Any]: 可写入 turn JSONL 的观测日志 payload。
    """
    llm_metrics = _merge_llm_metrics_with_telemetry(
        _collect_request_llm_metrics(session_id, request_id), telemetry
    )
    tool_metrics = _derive_tool_metrics(tool_observations)
    llm_rows_value = llm_metrics.get("llm_rows")
    llm_rows = llm_rows_value if isinstance(llm_rows_value, list) else []
    qa_pairs_value = memory_context.get("qa_pairs")
    retrieval_index_value = memory_context.get("retrieval_question_index")

    memory_before = {
        "qa_pair_count": len(
            qa_pairs_value if isinstance(qa_pairs_value, list) else []
        ),
        "retrieval_question_count": len(
            retrieval_index_value if isinstance(retrieval_index_value, list) else []
        ),
        "qa_memory_text": str(memory_context.get("qa_memory_text") or ""),
        "retrieval_question_index_text": str(
            memory_context.get("retrieval_question_index_text") or ""
        ),
    }
    orchestrator_decision = {
        "controller_mode": str(
            telemetry.get("controller_mode") or "explicit_memory_with_subagents"
        ),
        "retrieval_round": int(telemetry.get("retrieval_round") or 0),
        "final_source": str(telemetry.get("final_source") or "controller"),
        "answer_mode": str(evidence_summary.get("answer_mode") or ""),
        "prompt_budget_limit": int(telemetry.get("prompt_budget_limit") or 0),
        "compression_count": int(telemetry.get("compression_count") or 0),
        "prompt_token_count_before_last_decision": int(
            telemetry.get("prompt_token_count_before_last_decision") or 0
        ),
        "prompt_token_count_after_last_compression": int(
            telemetry.get("prompt_token_count_after_last_compression") or 0
        ),
        "aborted_for_prompt_budget": bool(telemetry.get("aborted_for_prompt_budget")),
    }

    agent_lanes: dict[str, list[dict[str, Any]]] = {
        "主控编排": _build_orchestrator_cards(
            input_text, memory_context, telemetry, llm_rows, started_at, finished_at
        ),
        "图谱代理": [],
        "检索代理": [],
    }
    for observation in tool_observations:
        card = _build_lane_card_from_tool_observation(observation, finished_at)
        lane_name = str(card.get("lane_name") or "")
        agent_lanes.setdefault(lane_name, []).append(card)

    return {
        "record_type": "turn_observability",
        "request_id": str(request_id or ""),
        "session_id": str(session_id or ""),
        "user_id": str(user_id or ""),
        "turn_id": f"{session_id}:{user_message_id}",
        "user_message_id": int(user_message_id or 0),
        "assistant_message_id": int(assistant_message_id or 0),
        "question": str(input_text or ""),
        "answer_digest": _build_reply_digest(full_reply),
        "started_at": started_at,
        "finished_at": finished_at,
        "status": str(status or "done"),
        "duration_ms": round(float(duration_ms), 2),
        "memory_before": memory_before,
        "orchestrator_decision": orchestrator_decision,
        "agent_lanes": agent_lanes,
        "memory_compression": _build_memory_compression_stages(
            input_text=input_text,
            full_reply=full_reply,
            memory_context=memory_context,
            turn_projection=turn_artifacts,
            post_memory=None,
            started_at=started_at,
            finished_at=finished_at,
            compressed_at=finished_at,
        ),
        "timeline": [
            {
                "title": "开始处理用户问题",
                "event_time": started_at,
                "detail": _sanitize_preview(input_text, 140),
            },
            {
                "title": "完成会话记忆读取",
                "event_time": started_at,
                "detail": "已加载显式 QA 记忆与历史检索问题索引",
            },
            *[
                {
                    "title": "主控过程事件",
                    "event_time": finished_at,
                    "detail": _sanitize_preview(item, 160),
                }
                for item in processing_steps[:8]
            ],
            {
                "title": "终答生成完成",
                "event_time": finished_at,
                "detail": _sanitize_preview(_build_reply_digest(full_reply), 160),
            },
        ],
        "processing_steps": processing_steps,
        "tool_observations": tool_observations,
        "llm_calls": int(llm_metrics.get("llm_calls") or 0),
        "total_tokens": int(llm_metrics.get("total_tokens") or 0),
        "tool_calls": int(tool_metrics.get("tool_calls") or 0),
        "attempted_tool_actions": int(tool_metrics.get("attempted_tool_actions") or 0),
        "blocked_tool_actions": int(tool_metrics.get("blocked_tool_actions") or 0),
        "tool_success_rate": float(tool_metrics.get("tool_success_rate") or 0.0),
        "execution_tool_success_rate": float(
            tool_metrics.get("execution_tool_success_rate") or 0.0
        ),
        "attempted_action_success_rate": float(
            tool_metrics.get("attempted_action_success_rate") or 0.0
        ),
        "error_types": (
            llm_metrics.get("error_types")
            if isinstance(llm_metrics.get("error_types"), dict)
            else {}
        ),
        "strategy_error_types": (
            tool_metrics.get("strategy_error_types")
            if isinstance(tool_metrics.get("strategy_error_types"), dict)
            else {}
        ),
        "execution_error_types": (
            tool_metrics.get("execution_error_types")
            if isinstance(tool_metrics.get("execution_error_types"), dict)
            else {}
        ),
        "reflection_events": [],
        "turn_metrics": [],
    }


def _build_turn_memory_update_payload(
    *,
    session_id: str,
    request_id: str,
    user_message_id: int,
    assistant_message_id: int,
    updated_at: str,
    snapshot: Dict[str, Any],
    llm_metrics: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """构建单轮记忆写回完成后的 JSONL 日志 payload。

    Args:
        session_id: 会话标识。
        request_id: 请求标识。
        user_message_id: 用户消息 ID。
        assistant_message_id: 助手消息 ID。
        updated_at: 记忆更新时间。
        snapshot: 写回后的记忆快照。
        llm_metrics: 可选的本轮 LLM 指标。

    Returns:
        Dict[str, Any]: 可写入 turn JSONL 的记忆更新 payload。
    """
    metrics = llm_metrics if isinstance(llm_metrics, dict) else {}
    return {
        "record_type": "turn_memory_update",
        "request_id": str(request_id or ""),
        "session_id": str(session_id or ""),
        "turn_id": f"{session_id}:{user_message_id}",
        "user_message_id": int(user_message_id or 0),
        "assistant_message_id": int(assistant_message_id or 0),
        "updated_at": updated_at,
        "snapshot": snapshot if isinstance(snapshot, dict) else {},
        "llm_calls": int(metrics.get("llm_calls") or 0),
        "total_tokens": int(metrics.get("total_tokens") or 0),
        "error_types": (
            metrics.get("error_types")
            if isinstance(metrics.get("error_types"), dict)
            else {}
        ),
    }


class SessionStreamService:
    """封装会话流式输出、assistant 落库与 memory refresh 调度。"""

    def __init__(
        self,
        await_call: Any,
        get_session_memory_service: Any,
        log_api_error: Any,
        event_stream_service: A2AEventStreamService,
        message_manager: Any = MessageManager,
        create_task: Any = asyncio.create_task,
    ) -> None:
        """初始化实例并注入运行依赖。

        Args:
            await_call: 兼容同步/异步仓储和服务调用的执行器。
            get_session_memory_service: 延迟获取会话记忆服务的回调。
            log_api_error: 统一记录会话流式接口错误的回调。
            event_stream_service: A2A 事件流服务实例。
            message_manager: 消息仓储实现。
            create_task: 后台任务创建器，允许测试记忆刷新调度失败。

        Returns:
            None: 无返回值。
        """
        self._await_call = await_call
        self._get_session_memory_service = get_session_memory_service
        self._log_api_error = log_api_error
        self._event_stream_service = event_stream_service
        self._message_manager = message_manager
        self._create_task = create_task

    async def generate_data_with_session_lines(
        self,
        input_text: str,
        user_id: str,
        session_id: str,
        image_data: Any = None,
        file_id: str = "",
        user_message_id: int = 0,
        request_id: str = "",
        original_input_text: str = "",
        memory_context: Optional[Dict[str, Any]] = None,
    ) -> AsyncIterator[str]:
        """基于 A2A 生成流式响应、保存助手回复并调度记忆刷新。

        Args:
            input_text: 当前进入 Agent 的输入文本。
            user_id: 用户标识。
            session_id: 会话标识。
            image_data: 可选图片数据，透传给 A2A 事件流。
            file_id: 可选阿里 File API 文件 ID，透传给 A2A 事件流。
            user_message_id: 本轮用户消息 ID。
            request_id: 请求追踪标识。
            original_input_text: 原始用户输入文本。
            memory_context: 本轮主控可见的会话记忆上下文。

        Yields:
            str: JSON Lines 格式的流式事件。
        """
        full_reply = ""
        full_think = ""
        done_status = "done"
        done_processing_steps: List[str] = []
        done_tool_observations: List[Dict[str, Any]] = []
        done_evidence_summary: Dict[str, Any] = {}
        done_telemetry: Dict[str, Any] = {}
        question_scope = _normalize_question_scope(
            _infer_question_scope(original_input_text or input_text)
        )
        assistant_message_persisted = False
        assistant_message_id = 0
        assistant_persist_attempts = 0
        memory_refresh_scheduled = False
        done_emitted = False
        turn_observability_written = False
        started_at = _iso_now()
        started_perf = time.perf_counter()
        session_tokens = set_session_context(str(session_id or ""), str(user_id or ""))
        request_token = set_request_context(str(request_id or ""))
        ensure_session_meta(str(session_id or ""), str(user_id or ""))

        def build_session_event() -> SessionStreamEvent:
            """构建会话持久化元数据事件。"""
            return SessionStreamEvent(
                session_id=str(session_id or ""),
                user_message_id=int(user_message_id or 0),
            )

        def build_persisted_event(
            persisted_assistant_message_id: int,
        ) -> PersistedStreamEvent:
            """构建助手消息持久化完成事件。"""
            return PersistedStreamEvent(
                session_id=str(session_id or ""),
                user_message_id=int(user_message_id or 0),
                assistant_message_id=int(persisted_assistant_message_id or 0),
            )

        def ensure_medical_disclaimer() -> str:
            """统一为医疗回答补充免责文本，并返回新增片段。"""
            nonlocal full_reply
            updated_reply = append_medical_disclaimer_if_needed(
                full_reply, question_scope
            )
            if updated_reply == full_reply:
                return ""
            added = (
                updated_reply[len(full_reply) :]
                if updated_reply.startswith(full_reply)
                else updated_reply
            )
            full_reply = updated_reply
            return added

        def _turn_log_path() -> Path:
            """返回当前会话当天的 turn JSONL 日志路径。"""
            sid = _safe_session_log_id(session_id)
            return (
                _project_root()
                / "logs"
                / "sessions"
                / sid
                / "turns"
                / f"{datetime.now().strftime('%Y-%m-%d')}.jsonl"
            )

        async def persist_assistant_message_once() -> int:
            """确保助手消息最多落库一次，并返回助手消息 ID。"""
            nonlocal assistant_message_id, assistant_message_persisted
            nonlocal assistant_persist_attempts
            if assistant_message_persisted:
                return int(assistant_message_id or 0)
            if not full_reply:
                return 0
            assistant_persist_attempts += 1
            ensure_medical_disclaimer()
            assistant_message = await self._await_call(
                self._message_manager.add_message,
                session_id,
                "assistant",
                full_reply,
                full_think,
            )
            assistant_message_id = int((assistant_message or {}).get("id") or 0)
            assistant_message_persisted = assistant_message_id > 0
            if not assistant_message_persisted:
                raise RuntimeError("assistant_message_id_missing")
            return int(assistant_message_id or 0)

        async def persist_assistant_message_with_retry(log_stage: str) -> int:
            """在终态前最多尝试两次助手消息落库。"""
            while not assistant_message_persisted and assistant_persist_attempts < 2:
                try:
                    return await persist_assistant_message_once()
                except Exception as exc:
                    self._log_api_error(
                        "/process_with_session",
                        log_stage,
                        exc,
                        session_id=session_id,
                        user_id=user_id,
                        request_id=request_id,
                        attempt=assistant_persist_attempts,
                    )
            return int(assistant_message_id or 0)

        def write_turn_observability_once() -> None:
            """确保本轮观测日志最多写入一次。"""
            nonlocal turn_observability_written
            if turn_observability_written:
                return
            turn_observability_written = True
            try:
                turn_artifacts = _build_turn_memory_projection(
                    input_text=str(input_text or ""),
                    user_message_id=int(user_message_id or 0),
                    full_reply=full_reply,
                    processing_steps=done_processing_steps,
                    tool_observations=done_tool_observations,
                    evidence_summary=done_evidence_summary,
                )
                payload = _build_turn_observability_payload(
                    session_id=str(session_id or ""),
                    user_id=str(user_id or ""),
                    request_id=str(request_id or ""),
                    user_message_id=int(user_message_id or 0),
                    assistant_message_id=int(assistant_message_id or 0),
                    input_text=str(input_text or ""),
                    full_reply=full_reply,
                    memory_context=(
                        memory_context if isinstance(memory_context, dict) else {}
                    ),
                    processing_steps=done_processing_steps,
                    tool_observations=done_tool_observations,
                    evidence_summary=done_evidence_summary,
                    telemetry=done_telemetry,
                    started_at=started_at,
                    finished_at=_iso_now(),
                    duration_ms=(time.perf_counter() - started_perf) * 1000.0,
                    status=done_status,
                    turn_artifacts=turn_artifacts,
                )
                _append_jsonl(_turn_log_path(), payload)
                log_perf_event(
                    "iterative_agent_run",
                    timestamp=payload.get("finished_at"),
                    session_id=payload.get("session_id"),
                    user_id=payload.get("user_id"),
                    duration_ms=payload.get("duration_ms"),
                    llm_calls=payload.get("llm_calls"),
                    tool_calls=payload.get("tool_calls"),
                    attempted_tool_actions=payload.get("attempted_tool_actions"),
                    blocked_tool_actions=payload.get("blocked_tool_actions"),
                    tool_success_rate=payload.get("tool_success_rate"),
                    execution_tool_success_rate=payload.get(
                        "execution_tool_success_rate"
                    ),
                    attempted_action_success_rate=payload.get(
                        "attempted_action_success_rate"
                    ),
                    total_tokens=payload.get("total_tokens"),
                    error_types=payload.get("error_types"),
                    strategy_error_types=payload.get("strategy_error_types"),
                    execution_error_types=payload.get("execution_error_types"),
                    tool_observations=payload.get("tool_observations"),
                    reflection_events=payload.get("reflection_events"),
                    turn_metrics=payload.get("turn_metrics"),
                    processing_steps=payload.get("processing_steps"),
                )
            except Exception as exc:
                self._log_api_error(
                    "/process_with_session",
                    "write_turn_observability",
                    exc,
                    session_id=session_id,
                    user_id=user_id,
                    request_id=request_id,
                )

        def schedule_memory_refresh(persisted_assistant_message_id: int) -> None:
            """在助手消息落库后调度后台记忆刷新任务。

            Args:
                persisted_assistant_message_id: 已落库的助手消息 ID。

            Returns:
                None: 无返回值。
            """
            nonlocal memory_refresh_scheduled
            if (
                memory_refresh_scheduled
                or int(persisted_assistant_message_id or 0) <= 0
            ):
                return

            turn_artifacts = _build_turn_memory_projection(
                input_text=str(input_text or ""),
                user_message_id=int(user_message_id or 0),
                full_reply=full_reply,
                processing_steps=done_processing_steps,
                tool_observations=done_tool_observations,
                evidence_summary=done_evidence_summary,
            )

            async def refresh_memory_task() -> None:
                """刷新会话记忆并写入记忆更新观测日志。"""
                tokens = set_session_context(str(session_id or ""), str(user_id or ""))
                request_ctx = set_request_context(str(request_id or ""))
                try:
                    memory_service: SessionMemoryService = (
                        self._get_session_memory_service()
                    )
                    snapshot = await self._await_call(
                        memory_service.refresh_after_turn,
                        session_id,
                        int(persisted_assistant_message_id or 0),
                        turn_artifacts,
                    )
                    request_llm_metrics = _collect_request_llm_metrics(
                        str(session_id or ""),
                        str(request_id or ""),
                    )
                    update_payload = _build_turn_memory_update_payload(
                        session_id=str(session_id or ""),
                        request_id=str(request_id or ""),
                        user_message_id=int(user_message_id or 0),
                        assistant_message_id=int(persisted_assistant_message_id or 0),
                        updated_at=_iso_now(),
                        snapshot=snapshot if isinstance(snapshot, dict) else {},
                        llm_metrics=request_llm_metrics,
                    )
                    _append_jsonl(_turn_log_path(), update_payload)
                finally:
                    reset_request_context(request_ctx)
                    reset_session_context(tokens)

            def on_refresh_done(task: asyncio.Task) -> None:
                """消费后台记忆刷新任务异常，避免未取异常告警。

                Args:
                    task: 当前处理任务。

                Returns:
                    None: 无返回值。
                """
                try:
                    task.result()
                except Exception as exc:
                    self._log_api_error(
                        "/process_with_session",
                        "memory_refresh_failed",
                        exc,
                        session_id=session_id,
                        user_id=user_id,
                        assistant_message_id=int(persisted_assistant_message_id or 0),
                        request_id=request_id,
                    )

            refresh_coro = refresh_memory_task()
            try:
                refresh_task = self._create_task(
                    refresh_coro,
                    name=f"memory-refresh-{session_id}-{persisted_assistant_message_id}",
                )
                refresh_task.add_done_callback(on_refresh_done)
                memory_refresh_scheduled = True
            except Exception as exc:
                refresh_coro.close()
                self._log_api_error(
                    "/process_with_session",
                    "memory_refresh_schedule_failed",
                    exc,
                    session_id=session_id,
                    user_id=user_id,
                    assistant_message_id=int(persisted_assistant_message_id or 0),
                    request_id=request_id,
                )

        try:
            yield stream_event_json_line(build_session_event())
            log_session_op(
                session_id=str(session_id or ""),
                turn=int(user_message_id or 0),
                stage="orchestrator",
                action="run_start",
                status="started",
                input_summary=_sanitize_preview(input_text, 160),
            )

            async for raw_event in self._event_stream_service.iter_a2a_stream_events(
                input_text=input_text,
                user_id=user_id,
                session_id=session_id,
                request_id=request_id,
                image_data=image_data,
                file_id=file_id,
                original_input_text=original_input_text,
                memory_context=memory_context,
            ):
                event = validate_stream_event(raw_event)
                if isinstance(event, ReplyStreamEvent):
                    full_reply += event.delta
                elif isinstance(event, ProgressStreamEvent):
                    full_think += event.msg
                elif isinstance(event, DoneStreamEvent):
                    done_status = event.status
                    done_processing_steps = event.processing_steps
                    done_tool_observations = event.tool_observations
                    done_evidence_summary = event.evidence_summary
                    done_telemetry = event.telemetry
                elif isinstance(event, ErrorStreamEvent):
                    done_status = "failed"
                    done_emitted = True
                    yield stream_event_json_line(event)
                    break

                if not isinstance(event, DoneStreamEvent):
                    yield stream_event_json_line(event)
                    continue

                disclaimer_chunk = ensure_medical_disclaimer()
                if disclaimer_chunk:
                    yield stream_event_json_line(
                        ReplyStreamEvent(delta=disclaimer_chunk, task_id=event.task_id)
                    )

                if full_reply:
                    persisted_id = await persist_assistant_message_with_retry(
                        "assistant_persist_failed_after_done"
                    )
                    if persisted_id <= 0:
                        done_status = "failed"
                        done_emitted = True
                        yield stream_event_json_line(
                            ErrorStreamEvent(
                                code="assistant_persist_failed",
                                message="回答已生成，但会话保存失败，请稍后重试",
                                task_id=event.task_id,
                                recoverable=True,
                            )
                        )
                        break
                    try:
                        for index, row in enumerate(done_tool_observations, start=1):
                            tool_name = str(
                                row.get("tool_name")
                                or row.get("requested_tool_name")
                                or ""
                            )
                            arguments_value = row.get("arguments")
                            arguments = (
                                arguments_value
                                if isinstance(arguments_value, dict)
                                else {}
                            )
                            result_value = row.get("result")
                            result_obj = (
                                result_value if isinstance(result_value, dict) else {}
                            )
                            log_tool_event(
                                session_id=str(session_id or ""),
                                turn=int(user_message_id or index),
                                tool_name=tool_name or "unknown_tool",
                                arguments=arguments,
                                result_ok=bool(result_obj.get("ok", True)),
                                has_signal=bool(result_obj.get("result")),
                                duration_ms=0.0,
                                fallback_used=bool(row.get("used_fallback")),
                                blocked_reason=str(row.get("blocked_reason") or ""),
                            )
                        log_session_op(
                            session_id=str(session_id or ""),
                            turn=int(user_message_id or 0),
                            stage="orchestrator",
                            action="run_end",
                            status=done_status,
                            output_summary=_build_reply_digest(full_reply),
                        )
                    except Exception as exc:
                        self._log_api_error(
                            "/process_with_session",
                            "assistant_post_persist_observability_failed",
                            exc,
                            session_id=session_id,
                            user_id=user_id,
                            request_id=request_id,
                            done_emitted=done_emitted,
                        )
                    write_turn_observability_once()
                    schedule_memory_refresh(persisted_id)
                    yield stream_event_json_line(build_persisted_event(persisted_id))
                done_emitted = True
                yield stream_event_json_line(event)
                break
        except asyncio.CancelledError as exc:
            done_status = "cancelled"
            self._log_api_error(
                "/process_with_session",
                "generate_data_with_session_cancelled",
                exc,
                session_id=session_id,
                user_id=user_id,
            )
            raise
        except Exception as exc:
            self._log_api_error(
                "/process_with_session",
                "generate_data_with_session_exception",
                exc,
                session_id=session_id,
                user_id=user_id,
            )
            if not done_emitted:
                yield stream_event_json_line(
                    ErrorStreamEvent(
                        code="session_stream_failed",
                        message="流式输出异常终止",
                    )
                )
                done_emitted = True
        finally:
            if full_reply and not assistant_message_persisted:
                persisted_id = await persist_assistant_message_with_retry(
                    "persist_assistant_turn_finally"
                )
                if persisted_id > 0:
                    try:
                        log_session_op(
                            session_id=str(session_id or ""),
                            turn=int(user_message_id or 0),
                            stage="orchestrator",
                            action="run_end",
                            status=done_status,
                            output_summary=_build_reply_digest(full_reply),
                        )
                        write_turn_observability_once()
                        schedule_memory_refresh(persisted_id)
                    except Exception as exc:
                        self._log_api_error(
                            "/process_with_session",
                            "assistant_finally_observability_failed",
                            exc,
                            session_id=session_id,
                            user_id=user_id,
                            request_id=request_id,
                            done_emitted=done_emitted,
                        )
            reset_request_context(request_token)
            reset_session_context(session_tokens)
