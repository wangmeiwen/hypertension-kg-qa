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

from app.backend.repositories.database import MessageManager
from app.backend.services.a2a_event_stream_service import A2AEventStreamService
from app.backend.services.session_memory_service import SessionMemoryService
from app.common.perf_logger import log_perf_event
from app.common.session_logger import (
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


def _sanitize_text(value: Any, max_len: int) -> str:
    """清洗输入并返回可用结果。
    
    Args:
        value: 待处理输入值。
        max_len: 函数输入参数。
    
    Returns:
        str: 文本结果。
    """
    return str(value or "").replace("\n", " ").strip()[:max_len]


def _json_text_len(value: Any) -> int:
    """执行json_text_len流程并返回结果。
    
    Args:
        value: 待处理输入值。
    
    Returns:
        int: 数值结果。
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


def _build_reply_digest(reply: str, max_len: int = 180) -> str:
    """构建并返回目标数据。
    
    Args:
        reply: 函数输入参数。
        max_len: 函数输入参数。
    
    Returns:
        str: 文本结果。
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


def append_medical_disclaimer_if_needed(reply: Any, question_scope: Any) -> str:
    """仅对医疗回答统一追加固定免责文本，并避免重复追加。"""
    text = str(reply or "").rstrip()
    if not text:
        return ""
    if _normalize_question_scope(question_scope) != "medical_qa":
        return text
    if text.endswith(MEDICAL_DISCLAIMER_TEXT):
        return text
    base_text = _strip_medical_disclaimer(text).rstrip()
    if not base_text:
        return MEDICAL_DISCLAIMER_TEXT
    return f"{base_text}\n\n{MEDICAL_DISCLAIMER_TEXT}"


def _sanitize_list(value: Any, max_items: int, max_len: int) -> List[str]:
    """清洗输入并返回可用结果。
    
    Args:
        value: 待处理输入值。
        max_items: 函数输入参数。
        max_len: 函数输入参数。
    
    Returns:
        List[str]: 处理后的列表结果。
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


def _extract_section_sentences(reply: str, title: str, next_titles: tuple[str, ...], max_items: int = 2) -> List[str]:
    """提取并返回关键信息。
    
    Args:
        reply: 函数输入参数。
        title: 函数输入参数。
        next_titles: 函数输入参数。
        max_items: 函数输入参数。
    
    Returns:
        List[str]: 处理后的列表结果。
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


def _extract_answered_points(evidence_summary: Dict[str, Any], full_reply: str) -> List[str]:
    """提取并返回关键信息。
    
    Args:
        evidence_summary: 函数输入参数。
        full_reply: 函数输入参数。
    
    Returns:
        List[str]: 处理后的列表结果。
    """
    supported_points = _sanitize_list(evidence_summary.get("supported_points"), max_items=5, max_len=120)
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
    return _sanitize_list([*conclusion_points, *evidence_points], max_items=5, max_len=120)


def _infer_question_scope(input_text: str) -> str:
    """推断question_scope相关信息并返回结果。
    
    Args:
        input_text: 函数输入参数。
    
    Returns:
        str: 文本结果。
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
    """规范化输入并返回标准化结果。
    
    Args:
        scope: 函数输入参数。
    
    Returns:
        str: 文本结果。
    """
    value = str(scope or "").strip()
    if value in {"medical_qa", "capability_query", "administrative", "small_talk", "unknown"}:
        return value
    return "unknown"


def _project_root() -> Path:
    """执行project_root流程并返回结果。
    
    Args:
        无。
    
    Returns:
        Path: 执行结果。
    """
    return Path(__file__).resolve().parents[3]


def _append_jsonl(file_path: Path, payload: Dict[str, Any]) -> None:
    """执行append_jsonl流程并返回结果。
    
    Args:
        file_path: 函数输入参数。
        payload: 函数输入参数。
    
    Returns:
        None: 无返回值。
    """
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with file_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")


def _safe_session_log_id(raw: Any, fallback: str = "unknown") -> str:
    """执行safe_session_log_id流程并返回结果。
    
    Args:
        raw: 函数输入参数。
        fallback: 函数输入参数。
    
    Returns:
        str: 文本结果。
    """
    text = str(raw or "").strip()
    if not text:
        return fallback
    allowed = []
    for ch in text:
        allowed.append(ch if (ch.isalnum() or ch in {"-", "_"}) else "_")
    cleaned = "".join(allowed).strip("_")
    return cleaned or fallback


def _iso_now() -> str:
    """执行iso_now流程并返回结果。
    
    Args:
        无。
    
    Returns:
        str: 文本结果。
    """
    return datetime.now().isoformat(timespec="seconds")


def _to_iso(value: Any) -> str:
    """执行to_iso流程并返回结果。
    
    Args:
        value: 待处理输入值。
    
    Returns:
        str: 文本结果。
    """
    text = str(value or "").strip()
    return text or _iso_now()


def _sanitize_preview(value: Any, max_len: int = 160) -> str:
    """清洗输入并返回可用结果。
    
    Args:
        value: 待处理输入值。
        max_len: 函数输入参数。
    
    Returns:
        str: 文本结果。
    """
    return _sanitize_text(value, max_len)


def _summarize_tool_result(result_payload: Any) -> str:
    """执行summarize_tool_result流程并返回结果。
    
    Args:
        result_payload: 函数输入参数。
    
    Returns:
        str: 文本结果。
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
        for key in ("items", "hits", "entities", "relationships"):
            rows = result_obj.get(key)
            if isinstance(rows, list) and rows:
                return f"{key} 共 {len(rows)} 项"
    return "已返回结果"


def _normalize_timestamp(value: Any, fallback: str) -> str:
    """规范化输入并返回标准化结果。
    
    Args:
        value: 待处理输入值。
        fallback: 函数输入参数。
    
    Returns:
        str: 文本结果。
    """
    text = str(value or "").strip()
    return text or fallback


def _extract_llm_role_from_compact(messages_compact: str) -> str:
    """提取并返回关键信息。
    
    Args:
        messages_compact: 函数输入参数。
    
    Returns:
        str: 文本结果。
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
    """读取session_llm_records相关信息并返回结果。
    
    Args:
        session_id: 会话标识。
        request_id: 请求标识。
    
    Returns:
        list[dict[str, Any]]: 处理后的列表结果。
    """
    sid = _safe_session_log_id(session_id)
    if not request_id:
        return []
    llm_dir = _project_root() / "logs" / "sessions" / sid / "llm"
    if not llm_dir.exists():
        return []
    rows: list[dict[str, Any]] = []
    for file_path in sorted(llm_dir.glob("*.jsonl")):
        try:
            for line in file_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                if not line.strip():
                    continue
                payload = json.loads(line)
                if str(payload.get("request_id") or "") != str(request_id):
                    continue
                if not isinstance(payload, dict):
                    continue
                rows.append(payload)
        except (OSError, ValueError, TypeError) as exc:
            logger.warning(
                "session_stream_skip_invalid_llm_log_file: session_id=%s request_id=%s file=%s error_type=%s error=%s",
                sid,
                str(request_id or ""),
                str(file_path),
                type(exc).__name__,
                exc,
            )
            continue
    rows.sort(key=lambda item: str(item.get("ts") or ""))
    return rows


def _build_lane_card_from_tool_observation(
    observation: Dict[str, Any],
    fallback_time: str,
) -> Dict[str, Any]:
    """构建并返回目标数据。
    
    Args:
        observation: 函数输入参数。
        fallback_time: 函数输入参数。
    
    Returns:
        Dict[str, Any]: 结构化结果数据。
    """
    tool_name = str(observation.get("tool_name") or observation.get("requested_tool_name") or "").strip()
    question = str(observation.get("subquery") or observation.get("question") or "").strip()
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
    result_payload = observation.get("result") if isinstance(observation.get("result"), dict) else {}
    return {
        "lane_name": lane_name,
        "title": "执行工具调用",
        "occurred_at": fallback_time,
        "prompt_preview": _sanitize_preview(question or f"执行 {tool_name}", 140),
        "decision_preview": _summarize_tool_result(result_payload),
        "full_prompt": question or f"执行动作：{action}；工具：{tool_name}",
        "full_decision": _sanitize_preview(json.dumps(result_payload, ensure_ascii=False), 1200),
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
    """构建并返回目标数据。
    """
    cards: list[Dict[str, Any]] = [
        {
            "title": "主控统一决策",
            "started_at": started_at,
            "finished_at": finished_at,
            "prompt_preview": _sanitize_preview(input_text, 140),
            "decision_preview": _sanitize_preview(
                (
                    f"controller_mode={telemetry.get('controller_mode') or 'explicit_memory_with_history_read'}；"
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
                        "retrieval_question_index_text": memory_context.get("retrieval_question_index_text"),
                    },
                    ensure_ascii=False,
                ),
                1200,
            ),
            "full_decision": _sanitize_preview(json.dumps(telemetry, ensure_ascii=False), 1200),
            "status": "完成",
        }
    ]
    for row in llm_rows:
        lane_name = _extract_llm_role_from_compact(str(row.get("messages_compact") or ""))
        if lane_name != "主控编排":
            continue
        cards.append(
            {
                "title": "模型输入与输出",
                "started_at": _normalize_timestamp(row.get("ts"), started_at),
                "finished_at": _normalize_timestamp(row.get("ts"), finished_at),
                "prompt_preview": _sanitize_preview(row.get("messages_compact") or "", 160),
                "decision_preview": _sanitize_preview(row.get("response_compact") or row.get("error") or "", 160),
                "full_prompt": str(row.get("messages_compact") or ""),
                "full_decision": str(row.get("response_compact") or row.get("error") or ""),
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
    """构建并返回目标数据。
    
    Args:
        input_text: 函数输入参数。
        full_reply: 函数输入参数。
        memory_context: 会话记忆上下文。
        turn_projection: 函数输入参数。
        post_memory: 函数输入参数。
        started_at: 函数输入参数。
        finished_at: 函数输入参数。
        compressed_at: 函数输入参数。
    
    Returns:
        list[Dict[str, Any]]: 处理后的列表结果。
    """
    qa_before = memory_context.get("qa_pairs") if isinstance(memory_context.get("qa_pairs"), list) else []
    retrieval_before = (
        memory_context.get("retrieval_question_index")
        if isinstance(memory_context.get("retrieval_question_index"), list)
        else []
    )
    qa_after = ((post_memory or {}).get("qa_memory") if isinstance((post_memory or {}).get("qa_memory"), dict) else {}).get("qa_pairs")
    retrieval_after = (
        ((post_memory or {}).get("retrieval_memory") if isinstance((post_memory or {}).get("retrieval_memory"), dict) else {}).get("questions")
    )
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
                f"写入检索记录数：{len(turn_projection.get('query_records') or [])}",
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
    """构建并返回目标数据。
    
    Args:
        input_text: 函数输入参数。
        full_reply: 函数输入参数。
        processing_steps: 函数输入参数。
        tool_observations: 函数输入参数。
        evidence_summary: 函数输入参数。
    """
    _ = processing_steps
    _ = evidence_summary
    query_records: List[Dict[str, Any]] = []
    query_preview: List[str] = []
    captured_at = _iso_now()
    for observation in tool_observations:
        if not isinstance(observation, dict):
            continue
        raw_records = observation.get("query_records")
        if not isinstance(raw_records, list):
            result_obj = observation.get("result") if isinstance(observation.get("result"), dict) else {}
            payload = result_obj.get("result") if isinstance(result_obj.get("result"), dict) else {}
            raw_records = payload.get("query_records") if isinstance(payload.get("query_records"), list) else []
        for item in raw_records if isinstance(raw_records, list) else []:
            if not isinstance(item, dict) or not bool(item.get("has_signal")):
                continue
            record = {
                "agent": _sanitize_text(item.get("agent"), 24) or _sanitize_text(observation.get("agent"), 24) or "unknown",
                "query": _sanitize_text(item.get("query"), 300),
                "result_content": _sanitize_text(item.get("result_content"), 1600),
                "captured_at": _sanitize_text(item.get("captured_at"), 64) or captured_at,
                "citations": _sanitize_list(item.get("citations"), max_items=6, max_len=120),
                "has_signal": True,
            }
            for key in ("page_num", "start_page", "end_page"):
                value = item.get(key)
                if value not in (None, ""):
                    record[key] = value
            chunk_id = _sanitize_text(item.get("chunk_id"), 120)
            if chunk_id:
                record["chunk_id"] = chunk_id
            if not record["query"] or not record["result_content"]:
                continue
            query_records.append(record)
            preview = _sanitize_text(f"{record['agent']}:{record['query']}", 120)
            if preview and preview not in query_preview:
                query_preview.append(preview)
    return {
        "user_question": str(input_text or ""),
        "user_message_id": int(user_message_id or 0),
        "captured_at": captured_at,
        "query_records": query_records,
        "query_preview": query_preview[:5],
        "assistant_reply_digest": _build_reply_digest(full_reply),
        "question_scope": _normalize_question_scope(_infer_question_scope(input_text)),
        "projection_mode": "explicit_memory_v2",
        "assistant_reply_chars": len(str(full_reply or "")),
        "tool_observations_count": len(tool_observations),
        "tool_observations_chars": _json_text_len(tool_observations),
    }


def _collect_request_llm_metrics(session_id: str, request_id: str) -> Dict[str, Any]:
    """收集request_llm_metrics相关信息并返回结果。
    
    Args:
        session_id: 会话标识。
        request_id: 请求标识。
    
    Returns:
        Dict[str, Any]: 结构化结果数据。
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


def _derive_tool_metrics(tool_observations: List[Dict[str, Any]]) -> Dict[str, Any]:
    """执行derive_tool_metrics流程并返回结果。
    
    Args:
        tool_observations: 函数输入参数。
    
    Returns:
        Dict[str, Any]: 结构化结果数据。
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
        result_obj = row.get("result") if isinstance(row.get("result"), dict) else {}
        ok = bool(result_obj.get("ok", True))
        if ok:
            success_count += 1
        else:
            key = str(result_obj.get("error") or "tool_error")
            execution_error_types[key] = int(execution_error_types.get(key) or 0) + 1

    success_rate = round(float(success_count / tool_calls), 4) if tool_calls > 0 else 0.0
    attempted_success_rate = round(float(success_count / attempted_tool_actions), 4) if attempted_tool_actions > 0 else 0.0
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
    """构建并返回目标数据。
    
    Args:
        session_id: 会话标识。
        user_id: 用户标识。
        request_id: 请求标识。
        user_message_id: 对象标识。
        assistant_message_id: 对象标识。
        input_text: 函数输入参数。
        full_reply: 函数输入参数。
        memory_context: 会话记忆上下文。
    """
    llm_metrics = _collect_request_llm_metrics(session_id, request_id)
    tool_metrics = _derive_tool_metrics(tool_observations)
    llm_rows = llm_metrics.get("llm_rows") if isinstance(llm_metrics.get("llm_rows"), list) else []

    memory_before = {
        "qa_pair_count": len(memory_context.get("qa_pairs") if isinstance(memory_context.get("qa_pairs"), list) else []),
        "retrieval_question_count": len(
            memory_context.get("retrieval_question_index")
            if isinstance(memory_context.get("retrieval_question_index"), list)
            else []
        ),
        "qa_memory_text": str(memory_context.get("qa_memory_text") or ""),
        "retrieval_question_index_text": str(memory_context.get("retrieval_question_index_text") or ""),
    }
    orchestrator_decision = {
        "controller_mode": str(telemetry.get("controller_mode") or "explicit_memory_with_history_read"),
        "retrieval_round": int(telemetry.get("retrieval_round") or 0),
        "final_source": str(telemetry.get("final_source") or "controller"),
        "answer_mode": str(evidence_summary.get("answer_mode") or ""),
        "prompt_budget_limit": int(telemetry.get("prompt_budget_limit") or 0),
        "compression_count": int(telemetry.get("compression_count") or 0),
        "prompt_token_count_before_last_decision": int(telemetry.get("prompt_token_count_before_last_decision") or 0),
        "prompt_token_count_after_last_compression": int(telemetry.get("prompt_token_count_after_last_compression") or 0),
        "aborted_for_prompt_budget": bool(telemetry.get("aborted_for_prompt_budget")),
    }

    agent_lanes = {
        "主控编排": _build_orchestrator_cards(input_text, memory_context, telemetry, llm_rows, started_at, finished_at),
        "图谱代理": [],
        "检索代理": [],
    }
    for observation in tool_observations:
        card = _build_lane_card_from_tool_observation(observation, finished_at)
        lane_name = str(card.get("lane_name") or "")
        agent_lanes[lane_name] = agent_lanes.get(lane_name) if isinstance(agent_lanes.get(lane_name), list) else []
        agent_lanes[lane_name].append(card)

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
            {"title": "开始处理用户问题", "event_time": started_at, "detail": _sanitize_preview(input_text, 140)},
            {"title": "完成会话记忆读取", "event_time": started_at, "detail": "已加载显式 QA 记忆与历史检索问题索引"},
            *[
                {"title": "主控过程事件", "event_time": finished_at, "detail": _sanitize_preview(item, 160)}
                for item in processing_steps[:8]
            ],
            {"title": "终答生成完成", "event_time": finished_at, "detail": _sanitize_preview(_build_reply_digest(full_reply), 160)},
        ],
        "processing_steps": processing_steps,
        "tool_observations": tool_observations,
        "llm_calls": int(llm_metrics.get("llm_calls") or 0),
        "total_tokens": int(llm_metrics.get("total_tokens") or 0),
        "tool_calls": int(tool_metrics.get("tool_calls") or 0),
        "attempted_tool_actions": int(tool_metrics.get("attempted_tool_actions") or 0),
        "blocked_tool_actions": int(tool_metrics.get("blocked_tool_actions") or 0),
        "tool_success_rate": float(tool_metrics.get("tool_success_rate") or 0.0),
        "execution_tool_success_rate": float(tool_metrics.get("execution_tool_success_rate") or 0.0),
        "attempted_action_success_rate": float(tool_metrics.get("attempted_action_success_rate") or 0.0),
        "error_types": llm_metrics.get("error_types") if isinstance(llm_metrics.get("error_types"), dict) else {},
        "strategy_error_types": tool_metrics.get("strategy_error_types") if isinstance(tool_metrics.get("strategy_error_types"), dict) else {},
        "execution_error_types": tool_metrics.get("execution_error_types") if isinstance(tool_metrics.get("execution_error_types"), dict) else {},
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
    """构建并返回目标数据。
    
    Args:
        session_id: 会话标识。
        request_id: 请求标识。
        user_message_id: 对象标识。
        assistant_message_id: 对象标识。
        updated_at: 函数输入参数。
        snapshot: 函数输入参数。
        llm_metrics: 函数输入参数。
    
    Returns:
        Dict[str, Any]: 结构化结果数据。
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
        "error_types": metrics.get("error_types") if isinstance(metrics.get("error_types"), dict) else {},
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
    ) -> None:
        """初始化实例并注入运行依赖。
        
        Args:
            await_call: 函数输入参数。
            get_session_memory_service: 函数输入参数。
            log_api_error: 函数输入参数。
            event_stream_service: 函数输入参数。
            message_manager: 函数输入参数。
        
        Returns:
            None: 无返回值。
        """
        self._await_call = await_call
        self._get_session_memory_service = get_session_memory_service
        self._log_api_error = log_api_error
        self._event_stream_service = event_stream_service
        self._message_manager = message_manager

    async def generate_data_with_session_lines(
        self,
        input_text: str,
        user_id: str,
        session_id: str,
        image_data: Any = None,
        user_message_id: int = 0,
        request_id: str = "",
        original_input_text: str = "",
        memory_context: Optional[Dict[str, Any]] = None,
    ) -> AsyncIterator[str]:
        """基于 A2A 生成流式响应并保存助手回复。"""
        _ = image_data
        full_reply = ""
        full_think = ""
        done_status = "done"
        done_processing_steps: List[str] = []
        done_tool_observations: List[Dict[str, Any]] = []
        done_evidence_summary: Dict[str, Any] = {}
        done_telemetry: Dict[str, Any] = {}
        question_scope = _normalize_question_scope(_infer_question_scope(original_input_text or input_text))
        assistant_message_persisted = False
        assistant_message_id = 0
        memory_refresh_scheduled = False
        done_emitted = False
        turn_observability_written = False
        started_at = _iso_now()
        started_perf = time.perf_counter()
        session_tokens = set_session_context(str(session_id or ""), str(user_id or ""))
        request_token = set_request_context(str(request_id or ""))
        ensure_session_meta(str(session_id or ""), str(user_id or ""))

        def build_session_event() -> Dict[str, Any]:
            """构建会话持久化元数据事件。"""
            return {
                "type": "session",
                "session_id": str(session_id or ""),
                "user_message_id": int(user_message_id or 0),
            }

        def build_persisted_event(persisted_assistant_message_id: int) -> Dict[str, Any]:
            """构建助手消息持久化完成事件。"""
            return {
                "type": "persisted",
                "session_id": str(session_id or ""),
                "user_message_id": int(user_message_id or 0),
                "assistant_message_id": int(persisted_assistant_message_id or 0),
            }

        def ensure_medical_disclaimer() -> str:
            """统一为医疗回答补充免责文本，并返回新增片段。"""
            nonlocal full_reply
            updated_reply = append_medical_disclaimer_if_needed(full_reply, question_scope)
            if updated_reply == full_reply:
                return ""
            added = updated_reply[len(full_reply):] if updated_reply.startswith(full_reply) else updated_reply
            full_reply = updated_reply
            return added

        def _turn_log_path() -> Path:
            """执行turn_log_path流程并返回结果。
            
            Args:
                无。
            
            Returns:
                Path: 执行结果。
            """
            sid = _safe_session_log_id(session_id)
            return _project_root() / "logs" / "sessions" / sid / "turns" / f"{datetime.now().strftime('%Y-%m-%d')}.jsonl"

        async def persist_assistant_message_once() -> int:
            """执行persist_assistant_message_once流程并返回结果。
            
            Args:
                无。
            
            Returns:
                int: 数值结果。
            """
            nonlocal assistant_message_persisted, assistant_message_id
            if assistant_message_persisted:
                return int(assistant_message_id or 0)
            if not full_reply:
                return 0
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
            return int(assistant_message_id or 0)

        def write_turn_observability_once() -> None:
            """写入turn_observability_once相关信息并返回结果。
            
            Args:
                无。
            
            Returns:
                None: 无返回值。
            """
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
                    memory_context=memory_context if isinstance(memory_context, dict) else {},
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
                    execution_tool_success_rate=payload.get("execution_tool_success_rate"),
                    attempted_action_success_rate=payload.get("attempted_action_success_rate"),
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
            """执行schedule_memory_refresh流程并返回结果。
            
            Args:
                persisted_assistant_message_id: 对象标识。
            
            Returns:
                None: 无返回值。
            """
            nonlocal memory_refresh_scheduled
            if memory_refresh_scheduled or int(persisted_assistant_message_id or 0) <= 0:
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
                """执行refresh_memory_task流程并返回结果。
                
                Args:
                    无。
                
                Returns:
                    None: 无返回值。
                """
                tokens = set_session_context(str(session_id or ""), str(user_id or ""))
                request_ctx = set_request_context(str(request_id or ""))
                try:
                    memory_service: SessionMemoryService = self._get_session_memory_service()
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
                """执行on_refresh_done流程并返回结果。
                
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

            try:
                refresh_task = asyncio.create_task(
                    refresh_memory_task(),
                    name=f"memory-refresh-{session_id}-{persisted_assistant_message_id}",
                )
                refresh_task.add_done_callback(on_refresh_done)
                memory_refresh_scheduled = True
            except Exception as exc:
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
            yield json.dumps(build_session_event(), ensure_ascii=False) + "\n"
            log_session_op(
                session_id=str(session_id or ""),
                turn=int(user_message_id or 0),
                stage="orchestrator",
                action="run_start",
                status="started",
                input_summary=_sanitize_preview(input_text, 160),
            )

            async for msg in self._event_stream_service.iter_a2a_stream_events(
                input_text=input_text,
                user_id=user_id,
                session_id=session_id,
                request_id=request_id,
                image_data=image_data,
                original_input_text=original_input_text,
                memory_context=memory_context,
            ):
                if msg.get("type") == "reply":
                    full_reply += str(msg.get("msg") or "")
                elif msg.get("type") == "msg":
                    full_think += str(msg.get("msg") or "")
                elif msg.get("type") == "done":
                    done_status = str(msg.get("status") or "done")
                    processing_steps_obj = msg.get("processing_steps")
                    if isinstance(processing_steps_obj, list):
                        done_processing_steps = [str(item or "") for item in processing_steps_obj]
                    tool_observations_obj = msg.get("tool_observations")
                    if isinstance(tool_observations_obj, list):
                        done_tool_observations = [
                            item if isinstance(item, dict) else {"raw": str(item or "")}
                            for item in tool_observations_obj
                        ]
                    evidence_summary_obj = msg.get("evidence_summary")
                    if isinstance(evidence_summary_obj, dict):
                        done_evidence_summary = evidence_summary_obj
                    telemetry_obj = msg.get("telemetry")
                    if isinstance(telemetry_obj, dict):
                        done_telemetry = telemetry_obj

                if msg.get("type") == "done" and not full_reply:
                    full_reply = str(msg.get("msg") or "").strip()

                if msg.get("type") == "done":
                    disclaimer_chunk = ensure_medical_disclaimer()
                    if disclaimer_chunk:
                        yield json.dumps({"type": "reply", "msg": disclaimer_chunk}, ensure_ascii=False) + "\n"

                yield json.dumps(msg, ensure_ascii=False) + "\n"

                if msg.get("type") == "done":
                    done_emitted = True
                    if full_reply:
                        try:
                            persisted_id = await persist_assistant_message_once()
                            if persisted_id > 0:
                                for index, row in enumerate(done_tool_observations, start=1):
                                    tool_name = str(row.get("tool_name") or row.get("requested_tool_name") or "")
                                    result_obj = row.get("result") if isinstance(row.get("result"), dict) else {}
                                    log_tool_event(
                                        session_id=str(session_id or ""),
                                        turn=int(user_message_id or index),
                                        tool_name=tool_name or "unknown_tool",
                                        arguments=row.get("arguments") if isinstance(row.get("arguments"), dict) else {},
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
                                write_turn_observability_once()
                                schedule_memory_refresh(persisted_id)
                                yield json.dumps(build_persisted_event(persisted_id), ensure_ascii=False) + "\n"
                        except Exception as exc:
                            self._log_api_error(
                                "/process_with_session",
                                "assistant_persist_failed_after_done",
                                exc,
                                session_id=session_id,
                                user_id=user_id,
                                request_id=request_id,
                                done_emitted=done_emitted,
                            )
                    break
        except asyncio.CancelledError as exc:
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
                yield json.dumps({"type": "done", "status": "error", "msg": "流式输出异常终止"}, ensure_ascii=False) + "\n"
                done_emitted = True
        finally:
            if full_reply and not assistant_message_persisted:
                try:
                    persisted_id = await persist_assistant_message_once()
                    if persisted_id > 0:
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
                        "persist_assistant_turn_finally",
                        exc,
                        session_id=session_id,
                        user_id=user_id,
                        request_id=request_id,
                        done_emitted=done_emitted,
                    )
            reset_request_context(request_token)
            reset_session_context(session_tokens)
