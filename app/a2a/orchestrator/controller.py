"""统一主控决策循环。"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

try:
    import jieba  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - dependency is enforced at runtime
    jieba = None

from app.a2a.agent.llm_client import LLMClient
from app.a2a.client.subagent_client import SubAgentClientFactory
from app.a2a.mcp.tool_gateway import MCPToolGateway
from app.a2a.models import SubAgentConstraints, SubTask
from app.common.config import Config

logger = logging.getLogger(__name__)


def _sanitize_text(value: Any, max_len: int = 800) -> str:
    return str(value or "").replace("\n", " ").strip()[:max_len]


def _safe_json(value: Any, max_len: int = 2400) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        text = str(value or "")
    return text[:max_len]


def _extract_json_object(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            parsed = json.loads(raw[start : end + 1])
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}


def _coerce_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _coerce_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


class PromptBudgetExceededError(RuntimeError):
    """主控提示词压缩两次后仍超限时抛出。"""


class UnifiedOrchestratorController:
    """基于单一提示词驱动的主控调度器。"""

    _PROMPT_TOKEN_LIMIT = 10000
    _MAX_PROMPT_COMPRESSIONS = 2

    def __init__(
        self,
        subagent_client: SubAgentClientFactory,
        *,
        conf: Config | None = None,
        llm_client: LLMClient | None = None,
        mcp_gateway: MCPToolGateway | None = None,
    ) -> None:
        self._conf = conf if conf is not None else Config()
        self._client = llm_client if llm_client is not None else LLMClient(self._conf)
        self._subagent_client = subagent_client
        self._mcp_gateway = mcp_gateway if mcp_gateway is not None else MCPToolGateway(agent_type="orchestrator")

    def _build_system_prompt(self) -> str:
        return (
            "你是高血压问答系统的主控agent。\n"
            "你的职责是基于当前用户问题、会话问答记忆、历史检索问题索引，以及已经拿到的工具结果，自主决定下一步动作。\n"
            "你只能选择四种动作之一：\n"
            "1. final_answer：直接给出最终回答。\n"
            "2. call_kg：调用图谱子agent。\n"
            "3. call_rag：调用检索子agent。\n"
            "4. call_history_read：调用 MCP 工具 history_read 读取某个历史检索问题的结果。\n"
            "如果历史检索里已经有高度相关的问题，优先先读 history_read，再决定是否补检索。\n"
            "不要输出解释性文字，不要输出 Markdown，只返回一个 JSON 对象。\n"
            "JSON 格式必须满足：\n"
            "{"
            "\"action\":\"final_answer|call_kg|call_rag|call_history_read\","
            "\"reason\":\"简短原因\","
            "\"question\":\"当需要检索时要处理的问题，可为空\","
            "\"answer\":\"仅 final_answer 时填写\","
            "\"tool_name\":\"仅 call_kg 时可填写 kg_entity_relation_search 或 kg_query\","
            "\"arguments\":{},"
            "\"history_id\":\"仅 call_history_read 时可填写\","
            "\"user_question\":\"仅 call_history_read 时可填写\""
            "}\n"
            "若信息仍不足且已拿到一些证据，也要基于现有证据给出谨慎回答，不要卡住。"
        )

    def _build_compression_system_prompt(self) -> str:
        return (
            "你是主控提示词压缩器。\n"
            "你的任务是根据当前问题，压缩会话问答记忆、历史检索问题索引和工具结果，只保留与当前问题高度相关的内容。\n"
            "只允许删除、合并、缩写信息，禁止编造新事实，禁止引入新的 section 标题。\n"
            "返回 JSON 对象，不要输出任何额外解释。\n"
            "JSON 格式必须满足：\n"
            "{"
            "\"qa_memory_text\":\"压缩后的问答记忆文本\","
            "\"retrieval_question_index_text\":\"压缩后的历史检索问题索引文本\","
            "\"tool_observations\":["
            "{"
            "\"agent\":\"kg|rag|history\","
            "\"action\":\"call_kg|call_rag|call_history_read\","
            "\"subquery\":\"相关的子问题\","
            "\"summary\":\"该工具结果的压缩摘要\","
            "\"query_records\":["
            "{"
            "\"agent\":\"kg|rag|history\","
            "\"query\":\"相关检索query\","
            "\"result_content\":\"和当前问题最相关的结果内容\","
            "\"captured_at\":\"可为空\","
            "\"citations\":[],"
            "\"has_signal\":true"
            "}"
            "]"
            "}"
            "],"
            "\"compression_reason\":\"可选，简述这次压缩策略\""
            "}\n"
            "要求：\n"
            "1. qa_memory_text 只保留和当前问题直接相关的问答。\n"
            "2. retrieval_question_index_text 只保留和当前问题相关的历史检索问题。\n"
            "3. tool_observations 只保留和当前问题相关的工具结果，优先保留 query_records。\n"
            "4. 如果某一部分没有相关内容，可以返回“当前暂无问答对”“当前暂无历史检索问题”或空数组。\n"
            "5. 输出内容越短越好，但必须保留当前问题作答所需的关键信息。"
        )

    def _summarize_observation_result(self, row: dict[str, Any]) -> str:
        result_obj = _coerce_dict(row.get("result"))
        result_payload = _coerce_dict(result_obj.get("result"))
        return _sanitize_text(
            result_payload.get("summary")
            or result_payload.get("message")
            or result_payload.get("reason")
            or row.get("summary")
            or _safe_json(result_payload, 500),
            500,
        )

    def _build_observation_lines(self, tool_observations: list[dict[str, Any]]) -> list[str]:
        if not tool_observations:
            return ["当前还没有新的工具结果。"]
        lines: list[str] = []
        for index, row in enumerate(tool_observations[-6:], start=1):
            if not isinstance(row, dict):
                continue
            agent = _sanitize_text(row.get("agent"), 32)
            action = _sanitize_text(row.get("action"), 32)
            subquery = _sanitize_text(row.get("subquery") or row.get("question"), 180)
            lines.append(
                f"{index}. agent={agent or 'unknown'} | action={action or 'unknown'} | subquery={subquery} | result={self._summarize_observation_result(row)}"
            )
        return lines or ["当前还没有新的工具结果。"]

    def _count_prompt_tokens(self, messages: list[dict[str, Any]]) -> int:
        if jieba is None:
            raise RuntimeError("jieba_not_installed")
        text = "\n".join(_sanitize_text(_coerce_dict(row).get("content"), 200000) for row in messages)
        if not text.strip():
            return 0
        return sum(1 for token in jieba.lcut(text) if str(token).strip())

    def _normalize_query_record(self, row: Any, default_agent: str = "unknown") -> dict[str, Any] | None:
        if not isinstance(row, dict):
            return None
        query = _sanitize_text(row.get("query"), 300)
        result_content = _sanitize_text(row.get("result_content"), 1200)
        if not query or not result_content:
            return None
        return {
            "agent": _sanitize_text(row.get("agent"), 24) or default_agent or "unknown",
            "query": query,
            "result_content": result_content,
            "captured_at": _sanitize_text(row.get("captured_at"), 64),
            "citations": [
                _sanitize_text(item, 120)
                for item in _coerce_list(row.get("citations"))[:6]
                if _sanitize_text(item, 120)
            ],
            "has_signal": bool(row.get("has_signal", True)),
        }

    def _normalize_compressed_tool_observations(self, rows: Any) -> list[dict[str, Any]]:
        if not isinstance(rows, list):
            return []
        normalized_rows: list[dict[str, Any]] = []
        for item in rows[:6]:
            if not isinstance(item, dict):
                continue
            agent = _sanitize_text(item.get("agent"), 32) or "unknown"
            action = _sanitize_text(item.get("action"), 32) or "call_history_read"
            result_payload = _coerce_dict(_coerce_dict(item.get("result")).get("result"))
            summary = _sanitize_text(
                item.get("summary")
                or item.get("result_summary")
                or result_payload.get("summary"),
                500,
            )
            query_records: list[dict[str, Any]] = []
            for record in _coerce_list(item.get("query_records"))[:4]:
                normalized = self._normalize_query_record(record, default_agent=agent)
                if normalized is not None:
                    query_records.append(normalized)
            if summary and not _sanitize_text(result_payload.get("summary"), 500):
                result_payload["summary"] = summary
            if query_records and not _sanitize_text(result_payload.get("summary"), 500):
                result_payload["summary"] = f"保留 {len(query_records)} 条相关检索记录"
            for key in ("matched", "history_id", "user_question"):
                if key in item and key not in result_payload:
                    result_payload[key] = item.get(key)
            normalized_rows.append(
                {
                    "agent": agent,
                    "action": action,
                    "tool_name": _sanitize_text(item.get("tool_name"), 64) or (
                        "history_read" if action == "call_history_read" else f"{agent}_subagent"
                    ),
                    "subquery": _sanitize_text(item.get("subquery") or item.get("question"), 240),
                    "arguments": _coerce_dict(item.get("arguments")),
                    "query_records": query_records,
                    "result": {
                        "ok": bool(item.get("ok", True)),
                        "error": _sanitize_text(item.get("error"), 240),
                        "result": result_payload,
                    },
                }
            )
        return normalized_rows

    def _compact_tool_observations_for_compression(self, tool_observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        compact_rows: list[dict[str, Any]] = []
        for row in tool_observations[-8:]:
            if not isinstance(row, dict):
                continue
            compact_rows.append(
                {
                    "agent": _sanitize_text(row.get("agent"), 32),
                    "action": _sanitize_text(row.get("action"), 32),
                    "subquery": _sanitize_text(row.get("subquery") or row.get("question"), 240),
                    "summary": self._summarize_observation_result(row),
                    "query_records": [
                        normalized
                        for normalized in (
                            self._normalize_query_record(record, default_agent=_sanitize_text(row.get("agent"), 24) or "unknown")
                            for record in _coerce_list(row.get("query_records"))[:4]
                        )
                        if normalized is not None
                    ],
                    "history_id": _sanitize_text(_coerce_dict(_coerce_dict(row.get("result")).get("result")).get("history_id"), 64),
                    "user_question": _sanitize_text(
                        _coerce_dict(_coerce_dict(row.get("result")).get("result")).get("user_question"),
                        240,
                    ),
                    "matched": bool(_coerce_dict(_coerce_dict(row.get("result")).get("result")).get("matched")),
                }
            )
        return compact_rows

    def _build_compression_user_prompt(
        self,
        *,
        question: str,
        qa_memory_text: str,
        retrieval_index_text: str,
        tool_observations: list[dict[str, Any]],
        compression_count: int,
    ) -> str:
        return (
            "请根据当前问题压缩主控提示词里的动态内容。\n"
            "压缩后不能改变主控 prompt 的 section 结构，只能缩短其中内容。\n"
            "输入如下：\n"
            + _safe_json(
                {
                    "question": question,
                    "compression_count": compression_count,
                    "qa_memory_text": qa_memory_text,
                    "retrieval_question_index_text": retrieval_index_text,
                    "tool_observations": self._compact_tool_observations_for_compression(tool_observations),
                },
                12000,
            )
        )

    def _build_user_prompt(
        self,
        *,
        question: str,
        session_id: str,
        qa_memory_text: str,
        retrieval_index_text: str,
        tool_observations: list[dict[str, Any]],
        turn_index: int,
        max_turns: int,
        force_final: bool = False,
    ) -> str:
        observation_lines = self._build_observation_lines(tool_observations)
        final_rule = (
            "本轮必须输出 final_answer。"
            if force_final
            else "如果你认为现有问答记忆或历史检索已经足够，允许直接输出 final_answer。"
        )
        return (
            f"当前会话ID：{session_id}\n"
            f"当前轮次：{turn_index}/{max_turns}\n"
            f"用户问题：{question}\n\n"
            f"这是之前的问答情况：\n{qa_memory_text}\n\n"
            f"这是之前检索过的问题：\n{retrieval_index_text}\n\n"
            "你可以直接回答，调用 kg/rag 工具，或调用 MCP 工具 history_read 读取某个历史检索问题的结果。\n"
            f"{final_rule}\n\n"
            "这是你目前已经拿到的工具/历史读取结果：\n"
            + "\n".join(observation_lines)
        )

    async def _compress_prompt_sections(
        self,
        *,
        question: str,
        qa_memory_text: str,
        retrieval_index_text: str,
        tool_observations: list[dict[str, Any]],
        compression_count: int,
    ) -> tuple[dict[str, Any], dict[str, int]]:
        messages = [
            {"role": "system", "content": self._build_compression_system_prompt()},
            {
                "role": "user",
                "content": self._build_compression_user_prompt(
                    question=question,
                    qa_memory_text=qa_memory_text,
                    retrieval_index_text=retrieval_index_text,
                    tool_observations=tool_observations,
                    compression_count=compression_count,
                ),
            },
        ]
        response = await self._client.chat_with_tokens_async(messages=messages, temperature=0.0, timeout=90)
        parsed = _extract_json_object(str(getattr(response, "content", "") or ""))
        tokens = {
            "prompt_tokens": int(getattr(response, "prompt_tokens", 0) or 0),
            "completion_tokens": int(getattr(response, "completion_tokens", 0) or 0),
            "total_tokens": int(getattr(response, "total_tokens", 0) or 0),
        }
        return {
            "qa_memory_text": _sanitize_text(parsed.get("qa_memory_text"), 6000) or "当前暂无问答对",
            "retrieval_question_index_text": _sanitize_text(parsed.get("retrieval_question_index_text"), 6000)
            or "当前暂无历史检索问题",
            "tool_observations": self._normalize_compressed_tool_observations(parsed.get("tool_observations")),
            "compression_reason": _sanitize_text(parsed.get("compression_reason"), 240)
            or "删除了和当前问题无关的记忆与工具结果",
        }, tokens

    async def _prepare_budgeted_messages(
        self,
        *,
        question: str,
        session_id: str,
        qa_memory_text: str,
        retrieval_index_text: str,
        tool_observations: list[dict[str, Any]],
        turn_index: int,
        max_turns: int,
        force_final: bool,
        compression_state: dict[str, Any],
        processing_steps: list[str],
        progress_callback: Callable[[dict[str, Any]], None] | None,
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        current_qa_memory_text = str(qa_memory_text or "当前暂无问答对")
        current_retrieval_index_text = str(retrieval_index_text or "当前暂无历史检索问题")
        current_tool_observations = tool_observations if isinstance(tool_observations, list) else []
        compression_tokens = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        while True:
            messages = [
                {"role": "system", "content": self._build_system_prompt()},
                {
                    "role": "user",
                    "content": self._build_user_prompt(
                        question=question,
                        session_id=session_id,
                        qa_memory_text=current_qa_memory_text,
                        retrieval_index_text=current_retrieval_index_text,
                        tool_observations=current_tool_observations,
                        turn_index=turn_index,
                        max_turns=max_turns,
                        force_final=force_final,
                    ),
                },
            ]
            prompt_token_count = self._count_prompt_tokens(messages)
            compression_state["prompt_budget_limit"] = self._PROMPT_TOKEN_LIMIT
            compression_state["prompt_token_count_before_last_decision"] = int(prompt_token_count)
            compression_state["prompt_token_count_after_last_compression"] = int(prompt_token_count)

            if prompt_token_count <= self._PROMPT_TOKEN_LIMIT:
                return messages, compression_tokens

            current_compression_count = int(compression_state.get("compression_count") or 0)
            if current_compression_count >= self._MAX_PROMPT_COMPRESSIONS:
                compression_state["aborted_for_prompt_budget"] = True
                processing_steps.append(
                    f"主控提示词分词数={prompt_token_count}，已超过预算 {self._PROMPT_TOKEN_LIMIT} 且压缩次数达到上限，终止任务"
                )
                raise PromptBudgetExceededError("prompt_budget_exceeded")

            next_compression_index = current_compression_count + 1
            processing_steps.append(
                f"主控提示词分词数={prompt_token_count}，超过预算 {self._PROMPT_TOKEN_LIMIT}，开始第 {next_compression_index} 次压缩"
            )
            if progress_callback is not None:
                progress_callback(
                    {
                        "event": "prompt_compression",
                        "detail": f"主控提示词超过 {self._PROMPT_TOKEN_LIMIT}，开始第 {next_compression_index} 次压缩",
                        "tool_name": "prompt_compressor",
                    }
                )

            compressed_sections, tokens = await self._compress_prompt_sections(
                question=question,
                qa_memory_text=current_qa_memory_text,
                retrieval_index_text=current_retrieval_index_text,
                tool_observations=current_tool_observations,
                compression_count=next_compression_index,
            )
            compression_tokens["prompt_tokens"] += int(tokens.get("prompt_tokens") or 0)
            compression_tokens["completion_tokens"] += int(tokens.get("completion_tokens") or 0)
            compression_tokens["total_tokens"] += int(tokens.get("total_tokens") or 0)

            compression_state["compression_count"] = next_compression_index
            current_qa_memory_text = str(compressed_sections.get("qa_memory_text") or "当前暂无问答对")
            current_retrieval_index_text = str(
                compressed_sections.get("retrieval_question_index_text") or "当前暂无历史检索问题"
            )
            current_tool_observations = (
                compressed_sections.get("tool_observations")
                if isinstance(compressed_sections.get("tool_observations"), list)
                else []
            )
            messages = [
                {"role": "system", "content": self._build_system_prompt()},
                {
                    "role": "user",
                    "content": self._build_user_prompt(
                        question=question,
                        session_id=session_id,
                        qa_memory_text=current_qa_memory_text,
                        retrieval_index_text=current_retrieval_index_text,
                        tool_observations=current_tool_observations,
                        turn_index=turn_index,
                        max_turns=max_turns,
                        force_final=force_final,
                    ),
                },
            ]
            compressed_prompt_token_count = self._count_prompt_tokens(messages)
            compression_state["prompt_token_count_after_last_compression"] = int(compressed_prompt_token_count)
            compression_events = compression_state.get("compression_events")
            if not isinstance(compression_events, list):
                compression_events = []
                compression_state["compression_events"] = compression_events
            compression_events.append(
                {
                    "attempt": next_compression_index,
                    "before_tokens": int(prompt_token_count),
                    "after_tokens": int(compressed_prompt_token_count),
                    "reason": _sanitize_text(compressed_sections.get("compression_reason"), 240),
                }
            )
            processing_steps.append(
                f"第 {next_compression_index} 次压缩完成：压缩后分词数={compressed_prompt_token_count}；原因：{_sanitize_text(compressed_sections.get('compression_reason'), 120) or '无'}"
            )
            if progress_callback is not None:
                progress_callback(
                    {
                        "event": "prompt_compression_done",
                        "detail": f"第 {next_compression_index} 次压缩后分词数={compressed_prompt_token_count}",
                        "tool_name": "prompt_compressor",
                    }
                )

            if compressed_prompt_token_count <= self._PROMPT_TOKEN_LIMIT:
                return messages, compression_tokens

            if next_compression_index >= self._MAX_PROMPT_COMPRESSIONS:
                compression_state["aborted_for_prompt_budget"] = True
                processing_steps.append(
                    f"第 {next_compression_index} 次压缩后分词数={compressed_prompt_token_count}，仍超过预算 {self._PROMPT_TOKEN_LIMIT}，终止任务"
                )
                raise PromptBudgetExceededError("prompt_budget_exceeded")

    async def _decide_action(
        self,
        *,
        question: str,
        session_id: str,
        qa_memory_text: str,
        retrieval_index_text: str,
        tool_observations: list[dict[str, Any]],
        turn_index: int,
        max_turns: int,
        force_final: bool = False,
        compression_state: dict[str, Any],
        processing_steps: list[str],
        progress_callback: Callable[[dict[str, Any]], None] | None,
    ) -> tuple[dict[str, Any], dict[str, int]]:
        messages, compression_tokens = await self._prepare_budgeted_messages(
            question=question,
            session_id=session_id,
            qa_memory_text=qa_memory_text,
            retrieval_index_text=retrieval_index_text,
            tool_observations=tool_observations,
            turn_index=turn_index,
            max_turns=max_turns,
            force_final=force_final,
            compression_state=compression_state,
            processing_steps=processing_steps,
            progress_callback=progress_callback,
        )
        response = await self._client.chat_with_tokens_async(messages=messages, temperature=0.1, timeout=90)
        parsed = _extract_json_object(str(getattr(response, "content", "") or ""))
        tokens = {
            "prompt_tokens": int(getattr(response, "prompt_tokens", 0) or 0)
            + int(compression_tokens.get("prompt_tokens") or 0),
            "completion_tokens": int(getattr(response, "completion_tokens", 0) or 0)
            + int(compression_tokens.get("completion_tokens") or 0),
            "total_tokens": int(getattr(response, "total_tokens", 0) or 0)
            + int(compression_tokens.get("total_tokens") or 0),
        }
        return parsed, tokens

    def _build_history_observation(self, action: dict[str, Any], tool_result: dict[str, Any]) -> dict[str, Any]:
        payload = _coerce_dict(tool_result.get("result"))
        return {
            "agent": "history",
            "action": "call_history_read",
            "tool_name": "history_read",
            "subquery": _sanitize_text(action.get("user_question") or action.get("history_id"), 200),
            "arguments": {
                "history_id": _sanitize_text(action.get("history_id"), 64),
                "user_question": _sanitize_text(action.get("user_question"), 200),
            },
            "query_records": payload.get("records") if isinstance(payload.get("records"), list) else [],
            "result": {
                "ok": bool(tool_result.get("ok")),
                "error": str(tool_result.get("error") or ""),
                "result": {
                    "matched": bool(payload.get("matched")),
                    "summary": (
                        f"读取到 {len(payload.get('records') or [])} 条历史检索记录"
                        if bool(payload.get("matched"))
                        else "未匹配到历史检索记录"
                    ),
                    "records": payload.get("records") if isinstance(payload.get("records"), list) else [],
                    "history_id": _sanitize_text(payload.get("history_id"), 64),
                    "user_question": _sanitize_text(payload.get("user_question"), 200),
                },
            },
        }

    def _build_subagent_observation(self, result: Any, action: dict[str, Any]) -> dict[str, Any]:
        query_records = result.query_records if isinstance(result.query_records, list) else []
        return {
            "agent": str(result.target_agent or ""),
            "action": str(action.get("action") or ""),
            "tool_name": f"{result.target_agent}_subagent",
            "requested_tool_name": _sanitize_text(action.get("tool_name"), 64),
            "subquery": _sanitize_text(result.question, 240),
            "arguments": _coerce_dict(action.get("arguments")),
            "query_records": query_records,
            "result": {
                "ok": True,
                "error": "",
                "result": {
                    "summary": f"signal={result.signal}; evidence_count={len(result.evidence)}; queries={len(result.queries_used)}",
                    "signal": result.signal,
                    "coverage": result.coverage,
                    "reason": result.reason,
                    "queries_used": result.queries_used,
                    "query_records": query_records,
                    "evidence_count": len(result.evidence),
                },
            },
        }

    def _build_subtask(
        self,
        *,
        action: dict[str, Any],
        default_question: str,
        max_tool_calls: int,
    ) -> SubTask:
        target_agent = "kg" if str(action.get("action") or "") == "call_kg" else "rag"
        requested_tool_name = _sanitize_text(action.get("tool_name"), 64)
        if target_agent == "kg":
            tool_name = requested_tool_name or "kg_entity_relation_search"
        else:
            tool_name = "rag_search"
        question = _sanitize_text(action.get("question"), 300) or default_question
        return SubTask(
            subtask_id=1,
            target_agent=target_agent,
            question=question,
            intent="retrieve_for_answer",
            priority=50,
            constraints=SubAgentConstraints(
                allow_extra_retrieval=False,
                max_tool_calls=max_tool_calls,
            ),
            tool_name=tool_name,
            arguments=_coerce_dict(action.get("arguments")),
            reason=_sanitize_text(action.get("reason"), 200),
        )

    def _build_evidence_summary(self, tool_observations: list[dict[str, Any]]) -> dict[str, Any]:
        query_records: list[dict[str, Any]] = []
        covered_topics: list[str] = []
        for row in tool_observations:
            if not isinstance(row, dict):
                continue
            for record in _coerce_list(row.get("query_records")):
                if isinstance(record, dict) and bool(record.get("has_signal")):
                    query_records.append(record)
            subquery = _sanitize_text(row.get("subquery"), 120)
            if subquery and subquery not in covered_topics:
                covered_topics.append(subquery)
        supported_points = [
            _sanitize_text(item.get("result_content"), 120)
            for item in query_records[:5]
            if isinstance(item, dict) and _sanitize_text(item.get("result_content"), 120)
        ]
        return {
            "answer_mode": "retrieval_augmented" if query_records else "direct_answer",
            "supported_points": supported_points,
            "covered_topics": covered_topics[:5],
            "evidence_count": len(query_records),
            "retrieval_risk_flags": [],
        }

    def _build_budget_telemetry(self, compression_state: dict[str, Any]) -> dict[str, Any]:
        compression_events = compression_state.get("compression_events")
        return {
            "prompt_budget_limit": int(compression_state.get("prompt_budget_limit") or self._PROMPT_TOKEN_LIMIT),
            "compression_count": int(compression_state.get("compression_count") or 0),
            "prompt_token_count_before_last_decision": int(
                compression_state.get("prompt_token_count_before_last_decision") or 0
            ),
            "prompt_token_count_after_last_compression": int(
                compression_state.get("prompt_token_count_after_last_compression") or 0
            ),
            "aborted_for_prompt_budget": bool(compression_state.get("aborted_for_prompt_budget")),
            "prompt_budget_events": compression_events if isinstance(compression_events, list) else [],
        }

    def _build_over_budget_result(
        self,
        *,
        tool_observations: list[dict[str, Any]],
        processing_steps: list[str],
        retrieval_round: int,
        max_turns: int,
        total_tokens: int,
        compression_state: dict[str, Any],
    ) -> dict[str, Any]:
        evidence_summary = self._build_evidence_summary(tool_observations)
        evidence_summary["answer_mode"] = "aborted_over_budget"
        return {
            "output": "这个问题超出软件能力范围，无法回答",
            "output_type": "orchestrator_aborted_over_budget",
            "confidence": 0.0,
            "tool_observations": tool_observations,
            "reflection_events": [],
            "processing_steps": processing_steps,
            "telemetry": {
                "controller_mode": "explicit_memory_with_history_read",
                "retrieval_round": retrieval_round,
                "max_turns": max_turns,
                "total_tokens": total_tokens,
                "final_source": "prompt_budget_guard",
                **self._build_budget_telemetry(compression_state),
            },
            "evidence_summary": evidence_summary,
        }

    async def run(
        self,
        *,
        input_text: str,
        session_id: str = "",
        user_id: str = "",
        max_turns: int = 3,
        memory_context: dict[str, Any] | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        _ = user_id
        safe_question = str(input_text or "").strip()
        safe_session_id = str(session_id or "").strip()
        safe_memory = memory_context if isinstance(memory_context, dict) else {}
        qa_memory_text = str(safe_memory.get("qa_memory_text") or "当前暂无问答对")
        retrieval_index_text = str(safe_memory.get("retrieval_question_index_text") or "当前暂无历史检索问题")
        safe_max_turns = max(1, int(max_turns or 1))
        safe_tool_calls = max(1, min(int(self._conf.A2A_SUBAGENT_MAX_TOOL_CALLS_DEFAULT), int(self._conf.A2A_SUBAGENT_MAX_TOOL_CALLS_MAX)))

        tool_observations: list[dict[str, Any]] = []
        processing_steps: list[str] = ["已加载显式 QA 记忆与历史检索问题索引"]
        total_tokens = 0
        retrieval_round = 0
        final_answer = ""
        confidence = 0.2
        compression_state: dict[str, Any] = {
            "compression_count": 0,
            "prompt_budget_limit": self._PROMPT_TOKEN_LIMIT,
            "prompt_token_count_before_last_decision": 0,
            "prompt_token_count_after_last_compression": 0,
            "aborted_for_prompt_budget": False,
            "compression_events": [],
        }

        for turn_index in range(1, safe_max_turns + 1):
            if progress_callback is not None:
                progress_callback(
                    {
                        "event": "orchestrator_think",
                        "detail": f"主控第 {turn_index} 轮决策中",
                        "tool_name": "orchestrator",
                    }
                )

            try:
                action, tokens = await self._decide_action(
                    question=safe_question,
                    session_id=safe_session_id,
                    qa_memory_text=qa_memory_text,
                    retrieval_index_text=retrieval_index_text,
                    tool_observations=tool_observations,
                    turn_index=turn_index,
                    max_turns=safe_max_turns,
                    force_final=False,
                    compression_state=compression_state,
                    processing_steps=processing_steps,
                    progress_callback=progress_callback,
                )
            except PromptBudgetExceededError:
                return self._build_over_budget_result(
                    tool_observations=tool_observations,
                    processing_steps=processing_steps,
                    retrieval_round=retrieval_round,
                    max_turns=safe_max_turns,
                    total_tokens=total_tokens,
                    compression_state=compression_state,
                )
            total_tokens += int(tokens.get("total_tokens") or 0)
            action_name = str(action.get("action") or "").strip()
            processing_steps.append(
                f"第 {turn_index} 轮主控决策：{action_name or 'unknown'}；原因：{_sanitize_text(action.get('reason'), 160) or '无'}"
            )

            if action_name == "final_answer":
                final_answer = str(action.get("answer") or "").strip()
                confidence = 0.9 if tool_observations else 0.7
                break

            if action_name in {"call_kg", "call_rag"}:
                retrieval_round += 1
                if progress_callback is not None:
                    progress_callback(
                        {
                            "event": "subagent_call",
                            "detail": f"调用 {action_name[5:]} 子agent",
                            "tool_name": action_name,
                        }
                    )
                task = self._build_subtask(
                    action=action,
                    default_question=safe_question,
                    max_tool_calls=safe_tool_calls,
                )
                result = await self._subagent_client.send_task(task)
                tool_observations.append(self._build_subagent_observation(result, action))
                continue

            if action_name == "call_history_read":
                retrieval_round += 1
                payload = {
                    "session_id": safe_session_id,
                    "history_id": _sanitize_text(action.get("history_id"), 64),
                    "user_question": _sanitize_text(action.get("user_question"), 300),
                }
                if progress_callback is not None:
                    progress_callback(
                        {
                            "event": "tool_call",
                            "detail": "读取历史检索记录",
                            "tool_name": "history_read",
                        }
                    )
                tool_result = await self._mcp_gateway.invoke_tool("history_read", payload)
                tool_observations.append(self._build_history_observation(action, tool_result))
                continue

            processing_steps.append("主控返回了未知动作，转入强制终答")
            break

        if not final_answer:
            try:
                forced_action, tokens = await self._decide_action(
                    question=safe_question,
                    session_id=safe_session_id,
                    qa_memory_text=qa_memory_text,
                    retrieval_index_text=retrieval_index_text,
                    tool_observations=tool_observations,
                    turn_index=safe_max_turns,
                    max_turns=safe_max_turns,
                    force_final=True,
                    compression_state=compression_state,
                    processing_steps=processing_steps,
                    progress_callback=progress_callback,
                )
            except PromptBudgetExceededError:
                return self._build_over_budget_result(
                    tool_observations=tool_observations,
                    processing_steps=processing_steps,
                    retrieval_round=retrieval_round,
                    max_turns=safe_max_turns,
                    total_tokens=total_tokens,
                    compression_state=compression_state,
                )
            total_tokens += int(tokens.get("total_tokens") or 0)
            final_answer = str(forced_action.get("answer") or "").strip()
            if not final_answer:
                final_answer = "当前基于已有问答记忆和检索记录，仍缺少足够证据给出更确定的回答。"
            processing_steps.append("已执行强制终答")
            confidence = 0.6 if tool_observations else 0.3

        evidence_summary = self._build_evidence_summary(tool_observations)
        return {
            "output": final_answer,
            "output_type": "orchestrator_final",
            "confidence": confidence,
            "tool_observations": tool_observations,
            "reflection_events": [],
            "processing_steps": processing_steps,
            "telemetry": {
                "controller_mode": "explicit_memory_with_history_read",
                "retrieval_round": retrieval_round,
                "max_turns": safe_max_turns,
                "total_tokens": total_tokens,
                "final_source": "controller",
                **self._build_budget_telemetry(compression_state),
            },
            "evidence_summary": evidence_summary,
        }
