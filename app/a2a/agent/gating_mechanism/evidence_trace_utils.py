"""
A2A agent/gating_mechanism/evidence_trace_utils.py 模块。

本模块提供 evidence_trace_utils 相关实现。
"""

import json
from typing import Any, Dict, List, Optional, Tuple

from app.a2a.agent.tool.text_parser import _extract_json_object

_UNGROUNDED_CLAIM_TRIGGERS = [
    "AHA/ACC",
    "ESC",
    "JNC",
    "NICE",
    "亚洲研究",
    "72小时",
    "风险达",
]


def _build_retrieved_corpus_text(traces: List[Dict[str, Any]]) -> str:
    """从工具轨迹中提取可供 LLM 参考的检索语料。"""
    snippets: List[str] = []

    for trace in traces:
        result = trace.get("result")

        if not isinstance(result, dict) or not result.get("ok"):
            continue

        payload = result.get("result")

        if not isinstance(payload, dict):
            continue

        for key in ("records", "entities", "relationships", "hits"):
            rows = payload.get(key)

            if not isinstance(rows, list):
                continue

            for row in rows[:20]:
                if not isinstance(row, dict):
                    continue

                parts = [
                    str(row.get("text") or "").strip(),
                    str(row.get("name") or "").strip(),
                    str(row.get("source_name") or "").strip(),
                    str(row.get("target_name") or "").strip(),
                ]

                merged = " ".join([p for p in parts if p])

                if merged:
                    snippets.append(merged)

    return "\n".join(snippets)


def _collect_community_report_lines(
    traces: List[Dict[str, Any]], limit: int = 3
) -> List[str]:
    """收集社区报告摘要文本。"""
    reports: List[str] = []
    seen: set[str] = set()

    for trace in traces:
        result = trace.get("result")

        if not isinstance(result, dict) or not result.get("ok"):
            continue

        payload = result.get("result")

        if not isinstance(payload, dict):
            continue

        rows = payload.get("community_reports")

        if not isinstance(rows, list):
            continue

        for row in rows:
            if not isinstance(row, dict):
                continue

            summary = str(row.get("summary") or "").strip()

            if not summary or summary in seen:
                continue

            seen.add(summary)
            reports.append(summary)

            if len(reports) >= limit:
                return reports

    return reports


def _collect_community_report_rows(
    traces: List[Dict[str, Any]], limit: int = 3
) -> List[Dict[str, Any]]:
    """收集社区报告结构化记录。"""
    rows: List[Dict[str, Any]] = []
    seen: set[str] = set()

    for trace in traces:
        result = trace.get("result")

        if not isinstance(result, dict) or not result.get("ok"):
            continue

        payload = result.get("result")

        if not isinstance(payload, dict):
            continue

        reports = payload.get("community_reports")

        if not isinstance(reports, list):
            continue

        for report in reports:
            if not isinstance(report, dict):
                continue

            summary = str(report.get("summary") or "").strip()

            if not summary or summary in seen:
                continue

            seen.add(summary)
            rows.append(
                {
                    "community_id": int(report.get("community_id") or 0),
                    "summary": summary,
                    "nodes": (
                        report.get("nodes")
                        if isinstance(report.get("nodes"), list)
                        else []
                    ),
                    "score": float(report.get("score") or 0.0),
                }
            )

            if len(rows) >= limit:
                return rows

    return rows


def _remove_ungrounded_claim_lines(
    answer_text: str,
    traces: List[Dict[str, Any]],
    llm: Optional[Any] = None,
) -> Tuple[str, Dict[str, int]]:
    """移除回答中的无依据表述。"""
    text = str(answer_text or "").strip()
    llm_metric = {
        "llm_calls": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }

    if not text:
        return text, llm_metric

    corpus = _build_retrieved_corpus_text(traces)

    if not corpus:
        return text, llm_metric

    lines = text.split("\n")

    if llm is not None:
        indexed_lines = [
            f"{idx + 1}. {str(line or '').strip()}"
            for idx, line in enumerate(lines)
            if str(line or "").strip()
        ]

        if indexed_lines:
            messages = [
                {
                    "role": "system",
                    "content": (
                        "你是医学回答证据审校器。"
                        "你将拿到回答分行与检索语料。"
                        "仅当某一行包含明确断言，且该断言无法在检索语料中找到支撑时，才应删除。"
                        "不要因措辞差异误删常规建议。"
                        '仅输出 JSON，格式: {"drop_line_numbers": [1,2], "reason": ""}。'
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"回答分行:\n{chr(10).join(indexed_lines)}\n\n"
                        f"检索语料:\n{corpus[:3600]}"
                    ),
                },
            ]

            try:
                response = llm.chat_with_tokens(messages, temperature=0.0)
                llm_metric = {
                    "llm_calls": 1,
                    "prompt_tokens": int(response.prompt_tokens or 0),
                    "completion_tokens": int(response.completion_tokens or 0),
                    "total_tokens": int(response.total_tokens or 0),
                }

                parsed = _extract_json_object(str(response.content or ""))
                drop_raw = parsed.get("drop_line_numbers")
                drop_set = {
                    int(item)
                    for item in drop_raw
                    if isinstance(item, int)
                    and int(item) >= 1
                    and int(item) <= len(lines)
                } if isinstance(drop_raw, list) else set()

                if drop_set:
                    kept_lines = [
                        line for idx, line in enumerate(lines, start=1) if idx not in drop_set
                    ]

                    cleaned = "\n".join(kept_lines).strip()

                    if cleaned:
                        cleaned += "\n\n注：已移除未在检索结果中明确出现的指南或时效性断言。"
                        return cleaned, llm_metric

            except (
                RuntimeError,
                ValueError,
                TypeError,
                KeyError,
                AttributeError,
                OSError,
                ImportError,
                json.JSONDecodeError,
            ):
                pass

    removed_any = False
    kept_lines: List[str] = []

    for line in lines:
        raw = str(line or "")
        stripped = raw.strip()

        if not stripped:
            kept_lines.append(raw)
            continue

        should_drop = False

        for trigger in _UNGROUNDED_CLAIM_TRIGGERS:
            if trigger in stripped and trigger not in corpus:
                should_drop = True
                break

        if should_drop:
            removed_any = True
            continue

        kept_lines.append(raw)

    cleaned = "\n".join(kept_lines).strip()

    if removed_any and cleaned:
        cleaned += "\n\n注：已移除未在检索结果中明确出现的指南或时效性断言。"

    return cleaned or text, llm_metric


def _freeze_args(arguments: Dict[str, Any]) -> Tuple[Tuple[str, str], ...]:
    """将参数字典冻结为可哈希结构。"""
    frozen: List[Tuple[str, str]] = []

    for key in sorted(arguments.keys()):
        value = arguments[key]

        try:
            value_text = json.dumps(value, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            value_text = str(value)

        frozen.append((str(key), value_text))

    return tuple(frozen)


def _result_has_signal(tool_result: Dict[str, Any]) -> bool:
    """判断工具结果是否包含有效信号。"""
    if not isinstance(tool_result, dict):
        return False

    if not tool_result.get("ok"):
        return False

    payload = tool_result.get("result", {})

    if not isinstance(payload, dict):
        return bool(payload)

    mode = str(payload.get("mode") or "").strip()

    if mode == "keyword_fallback_all_nodes":
        return False

    for key in ["records", "hits", "entities", "relationships"]:
        value = payload.get(key)

        if isinstance(value, list) and len(value) > 0:
            return True

    for value in payload.values():
        if isinstance(value, list) and len(value) > 0:
            return True

        if isinstance(value, dict) and len(value) > 0:
            return True

        if isinstance(value, str) and value.strip():
            return True

    return False


def _collect_db_balance_status(traces: List[Dict[str, Any]]) -> Dict[str, Any]:
    """统计图谱与向量检索调用平衡状态。"""
    vector_tools = {"rag_search"}
    graph_tools = {"kg_query", "kg_entity_relation_search"}
    valid_actions = {"call_tool", "subquery_parallel_call", "subquery_dependent_call"}

    vector_calls = 0
    graph_calls = 0
    vector_signals = 0
    graph_signals = 0
    last_tool = ""

    for trace in traces:
        action = str(trace.get("action") or "").strip()

        if action not in valid_actions:
            continue

        tool_name = str(trace.get("tool_name") or "").strip()

        if not tool_name:
            continue

        result = trace.get("result")
        has_signal = _result_has_signal(result if isinstance(result, dict) else {})

        if tool_name in vector_tools:
            vector_calls += 1

            if has_signal:
                vector_signals += 1

            last_tool = tool_name
            continue

        if tool_name in graph_tools:
            graph_calls += 1

            if has_signal:
                graph_signals += 1

            last_tool = tool_name

    needs_vector = vector_signals < 1
    needs_graph = graph_signals < 1

    return {
        "vector_calls": vector_calls,
        "graph_calls": graph_calls,
        "vector_signals": vector_signals,
        "graph_signals": graph_signals,
        "needs_vector": needs_vector,
        "needs_graph": needs_graph,
        "balanced": not (needs_vector or needs_graph),
        "last_tool": last_tool,
    }
