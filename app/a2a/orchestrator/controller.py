"""统一主控决策循环。"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

from pydantic import ValidationError

try:
    import jieba  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - dependency is enforced at runtime
    jieba = None

from app.a2a.agent.llm_client import LLMClient
from app.a2a.answer_context import (
    build_answer_generation_context,
    evidence_citation,
    validate_answer_generation,
)
from app.a2a.client.subagent_client import SubAgentClient
from app.a2a.conflict_detection import detect_evidence_conflicts
from app.a2a.evidence_bundle import build_evidence_bundle
from app.a2a.evidence_confidence import assess_evidence_confidence
from app.a2a.evidence_fusion import (
    EvidenceFusionResult,
    evidence_identity_index,
    fuse_evidence,
)
from app.a2a.evidence_sufficiency import assess_evidence_sufficiency
from app.a2a.medical_risk import MedicalRiskAssessment, assess_medical_risk
from app.a2a.orchestrator.answer_pipeline import AnswerPipeline
from app.a2a.orchestrator.evidence_pipeline import EvidencePipeline
from app.a2a.orchestrator.planning_service import PlanningService
from app.a2a.orchestrator.retrieval_scheduler import RetrievalScheduler
from app.a2a.models import (
    AnswerGenerationContext,
    AnswerValidationResult,
    ClaimEvidenceAssessment,
    ClaimSupportStatus,
    ConfidenceBreakdown,
    ConfidenceLevel,
    Evidence,
    MustCoverItem,
    MustCoverProgressState,
    OrchestratorAction,
    QuestionPlan,
    RetrievalTask,
    SubAgentResult,
    SubTask,
)
from app.a2a.question_planner import build_question_plan
from app.a2a.retrieval_progress import (
    build_retrieval_progress,
    has_semantic_progress,
    no_progress_reason,
)
from app.a2a.telemetry import (
    empty_token_usage,
    merge_token_usage,
    normalize_token_usage,
)
from app.core.config import Config

logger = logging.getLogger(__name__)


def _low_confidence(reason: str) -> ConfidenceBreakdown:
    return ConfidenceBreakdown(reasons=[reason])


def _sanitize_text(value: Any, max_len: int = 800) -> str:
    """清洗文本并截断到指定长度。"""
    return str(value or "").replace("\n", " ").strip()[:max_len]


def _safe_json(value: Any, max_len: int = 2400) -> str:
    """将任意对象安全序列化为 JSON 文本，失败时降级为字符串。"""
    try:
        text = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        text = str(value or "")
    return text[:max_len]


def _parse_json_object(text: str) -> dict[str, Any]:
    """严格解析模型返回的 JSON 对象。"""
    raw = str(text or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _coerce_dict(value: Any) -> dict[str, Any]:
    """将输入转换为字典，不可转换时返回空字典。"""
    return value if isinstance(value, dict) else {}


def _coerce_list(value: Any) -> list[Any]:
    """将输入转换为列表，不可转换时返回空列表。"""
    return value if isinstance(value, list) else []


def _evidence_ids(rows: Any) -> set[str]:
    """提取可通过 Evidence 契约校验的对象身份。"""
    evidence_rows: list[Evidence] = []
    for row in _coerce_list(rows):
        try:
            evidence = (
                row if isinstance(row, Evidence) else Evidence.model_validate(row)
            )
        except (ValidationError, ValueError, TypeError):
            continue
        evidence_rows.append(evidence)
    return set(evidence_identity_index(evidence_rows))


class PromptBudgetExceededError(RuntimeError):
    """主控提示词压缩两次后仍超限时抛出。"""


class OrchestratorActionValidationError(RuntimeError):
    """主控动作连续两次未通过结构化校验时抛出。"""


class UnifiedOrchestratorController:
    """基于单一提示词驱动的主控调度器。"""

    _PROMPT_TOKEN_LIMIT = 10000
    _MAX_PROMPT_COMPRESSIONS = 2
    _MAX_NO_PROGRESS_ROUNDS = 2
    _DAG_TOTAL_CONCURRENCY = 4
    _DAG_AGENT_CONCURRENCY = 2
    _DAG_TASK_TIMEOUT_SECONDS = 120.0
    _DAG_MAX_ATTEMPTS = 2

    def __init__(
        self,
        subagent_client: SubAgentClient,
        *,
        conf: Config | None = None,
        llm_client: LLMClient | None = None,
        mcp_gateway: Any | None = None,
    ) -> None:
        """初始化主控，注入 LLM 和子代理客户端。"""
        self._conf = conf if conf is not None else Config()
        self._client = llm_client if llm_client is not None else LLMClient(self._conf)
        self._subagent_client = subagent_client
        self._mcp_gateway = mcp_gateway

    async def aclose(self) -> None:
        """释放控制器持有的底层客户端资源。"""
        await self._client.aclose()

    def _build_system_prompt(self) -> str:
        """构建主控决策提示词。"""
        action_schema = json.dumps(
            OrchestratorAction.model_json_schema(), ensure_ascii=False
        )
        return (
            "你是高血压问答系统的主控agent。\n"
            "你的职责是基于当前用户问题、会话问答记忆、历史检索问题索引，以及已经拿到的工具结果，自主决定下一步动作。\n"
            "你只能选择四种动作之一：\n"
            "1. final_answer：直接给出最终回答。\n"
            "2. call_kg：调用图谱子agent。\n"
            "3. call_rag：调用检索子agent。\n"
            "4. call_history_read：按历史检索索引读取对应 Evidence。\n"
            "如果会话记忆已经可以回答此问题，直接回答。"
            "如果历史检索里有高度相关的问题，先使用 call_history_read 读取 Evidence，再决定回答或补检索。\n"
            "必须遵循结构化问题计划，优先处理 status=pending 的 must-cover，且子问题不得偏离其要求。\n"
            "call_kg 和 call_rag 必须从问题计划中选择一个状态为 pending 或 needs_supplement 的 retrieval_task，"
            "原样返回其 task_id 和 must_cover_id；该动作会触发全计划同一 ready 层的并行检索；"
            "call_history_read 也必须返回要补充的 task_id 和 must_cover_id。\n"
            "主控只负责拆分问题和选择子agent，不允许指定 MCP 工具名、Cypher、检索 payload 或底层工具参数。\n"
            "不要输出解释性文字，不要输出 Markdown，只返回一个 JSON 对象。\n"
            "JSON 格式必须满足：\n"
            "{"
            '"action":"final_answer|call_kg|call_rag|call_history_read",'
            '"task_id":"检索或历史读取动作对应的计划 task_id",'
            '"must_cover_id":"检索或历史读取动作对应的问题点 ID",'
            '"reason":"简短原因",'
            '"question":"当需要子agent处理时拆分出的子问题，可为空",'
            '"history_id":"仅 call_history_read 时填写索引中的 history_id",'
            '"user_question":"仅 call_history_read 且没有 history_id 时填写历史问题",'
            '"answer":"仅 final_answer 时填写"'
            "}\n"
            f"输出必须满足以下 JSON Schema：{action_schema}\n"
            "若信息不足，必须选择检索动作；不要在缺少直接证据时输出 final_answer。"
        )

    @staticmethod
    def _validate_plan_action(
        action: dict[str, Any], question_plan: QuestionPlan
    ) -> tuple[RetrievalTask | None, MustCoverItem | None]:
        """校验动作引用的计划任务、问题点、代理类型和依赖。"""
        return PlanningService().validate_action(action, question_plan)

    def _build_compression_system_prompt(self) -> str:
        """构建主控提示词压缩器系统提示词。"""
        return (
            "你是主控提示词压缩器。\n"
            "你的任务是根据当前问题，压缩会话问答记忆、历史检索问题索引和工具结果，只保留与当前问题高度相关的内容。\n"
            "只允许删除、合并、缩写信息，禁止编造新事实，禁止引入新的 section 标题。\n"
            "返回 JSON 对象，不要输出任何额外解释。\n"
            "JSON 格式必须满足：\n"
            "{"
            '"qa_memory_text":"压缩后的问答记忆文本",'
            '"retrieval_question_index_text":"压缩后的历史检索问题索引文本",'
            '"tool_observations":['
            "{"
            '"agent":"kg|rag|history",'
            '"action":"call_kg|call_rag|call_history_read",'
            '"task_id":"计划任务 ID",'
            '"must_cover_id":"问题点 ID",'
            '"subquery":"相关的子问题",'
            '"summary":"该工具结果的压缩摘要",'
            '"evidence":['
            "{"
            '"source_type":"document|knowledge_graph|history",'
            '"source_id":"来源标识",'
            '"document_id":"文档标识，可为空",'
            '"chunk_id":"分块标识，可为空",'
            '"page":null,'
            '"section":"章节，可为空",'
            '"content":"和当前问题最相关的证据内容",'
            '"score":null,'
            '"retrieval_method":"检索方法",'
            '"provenance":{}'
            "}"
            "]"
            "}"
            "],"
            '"compression_reason":"可选，简述这次压缩策略"'
            "}\n"
            "要求：\n"
            "1. qa_memory_text 只保留和当前问题直接相关的问答。\n"
            "2. retrieval_question_index_text 只保留和当前问题相关的历史检索问题。\n"
            "3. tool_observations 只保留和当前问题相关的工具结果，Evidence 字段名和来源信息必须原样保留。\n"
            "4. 如果某一部分没有相关内容，可以返回“当前暂无问答对”“当前暂无历史检索问题”或空数组。\n"
            "5. 输出内容越短越好，但必须保留当前问题作答所需的关键信息。"
        )

    def _summarize_observation_result(self, row: dict[str, Any]) -> str:
        """提取单条工具观测结果的摘要文本。"""
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

    def _build_observation_lines(
        self, tool_observations: list[dict[str, Any]]
    ) -> list[str]:
        """将工具观测列表转为主控提示词可读行。"""
        if not tool_observations:
            return ["当前还没有新的工具结果。"]
        lines: list[str] = []
        for index, row in enumerate(tool_observations[-6:], start=1):
            if not isinstance(row, dict):
                continue
            agent = _sanitize_text(row.get("agent"), 32)
            action = _sanitize_text(row.get("action"), 32)
            subquery = _sanitize_text(row.get("subquery") or row.get("question"), 180)
            evidence = [
                normalized
                for normalized in (
                    self._normalize_evidence(item)
                    for item in _coerce_list(row.get("evidence"))[:6]
                )
                if normalized is not None
            ]
            evidence_summary = self._summarize_evidence(evidence, limit=3)
            result_summary = evidence_summary or self._summarize_observation_result(row)
            lines.append(
                f"{index}. agent={agent or 'unknown'} | action={action or 'unknown'} | subquery={subquery} | evidence={result_summary}"
            )
        return lines or ["当前还没有新的工具结果。"]

    @staticmethod
    def _normalize_evidence(row: Any) -> dict[str, Any] | None:
        """通过共享 Evidence 契约校验工具观测。"""
        if not isinstance(row, dict):
            return None
        try:
            return Evidence.model_validate(row).model_dump()
        except ValidationError:
            return None

    @staticmethod
    def _evidence_citation_label(row: dict[str, Any]) -> str:
        """从 Evidence 构造确定性的引用标签。"""
        try:
            return evidence_citation(Evidence.model_validate(row))
        except ValidationError:
            return ""

    @classmethod
    def _format_evidence_excerpt(cls, row: dict[str, Any]) -> str:
        """将 Evidence 格式化为带来源标签的简短证据。"""
        content = _sanitize_text(row.get("content"), 260)
        if not content:
            return ""
        citation = cls._evidence_citation_label(row)
        prefix = f"{citation} " if citation else ""
        return f"{prefix}{content}"

    def _summarize_evidence(self, rows: list[dict[str, Any]], *, limit: int = 3) -> str:
        """提炼 Evidence 并保留来源标签。"""
        snippets: list[str] = []
        for item in rows[: max(1, int(limit or 1))]:
            excerpt = self._format_evidence_excerpt(item)
            if excerpt and excerpt not in snippets:
                snippets.append(excerpt)
        return " | ".join(snippets)[:900]

    def _count_prompt_tokens(self, messages: list[dict[str, Any]]) -> int:
        """使用 jieba 统计提示词分词数。"""
        if jieba is None:
            raise RuntimeError("jieba_not_installed")
        text = "\n".join(
            _sanitize_text(_coerce_dict(row).get("content"), 200000) for row in messages
        )
        if not text.strip():
            return 0
        return sum(1 for token in jieba.lcut(text) if str(token).strip())

    def _normalize_query_record(
        self, row: Any, default_agent: str = "unknown"
    ) -> dict[str, Any] | None:
        """标准化单条 query_record，缺关键字段时返回 None。"""
        if not isinstance(row, dict):
            return None
        query = _sanitize_text(row.get("query"), 300)
        result_content = _sanitize_text(row.get("result_content"), 1200)
        if not query or not result_content:
            return None
        record = {
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
        page_num = row.get("page_num")
        start_page = row.get("start_page")
        end_page = row.get("end_page")
        chunk_id = _sanitize_text(row.get("chunk_id"), 120)
        if page_num not in (None, ""):
            record["page_num"] = page_num
        if start_page not in (None, ""):
            record["start_page"] = start_page
        if end_page not in (None, ""):
            record["end_page"] = end_page
        if chunk_id:
            record["chunk_id"] = chunk_id
        return record

    def _detect_numeric_conflicts(self, rows: list[dict[str, Any]]) -> list[str]:
        """返回结构化 Evidence 冲突的阻断标记。"""
        conflicts = self._detect_evidence_conflicts(rows)
        return sorted(
            {
                f"{_sanitize_text(item.get('conflict_type'), 40)}_conflict"
                for item in conflicts
                if _sanitize_text(item.get("conflict_type"), 40)
            }
        )

    @staticmethod
    def _detect_evidence_conflicts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """返回可解释、可追踪的结构化证据冲突。"""
        evidence: list[Evidence] = []
        for row in rows:
            try:
                evidence.append(Evidence.model_validate(row))
            except ValidationError:
                continue
        return [item.model_dump() for item in detect_evidence_conflicts(evidence)]

    def _normalize_compressed_tool_observations(
        self, rows: Any
    ) -> list[dict[str, Any]]:
        """规范化压缩器返回的工具观测数据结构。"""
        if not isinstance(rows, list):
            return []
        normalized_rows: list[dict[str, Any]] = []
        for item in rows[:6]:
            if not isinstance(item, dict):
                continue
            agent = _sanitize_text(item.get("agent"), 32) or "unknown"
            action = _sanitize_text(item.get("action"), 32) or "call_history_read"
            result_payload = _coerce_dict(
                _coerce_dict(item.get("result")).get("result")
            )
            summary = _sanitize_text(
                item.get("summary")
                or item.get("result_summary")
                or result_payload.get("summary"),
                500,
            )
            evidence: list[dict[str, Any]] = []
            for evidence_item in _coerce_list(item.get("evidence"))[:6]:
                normalized = self._normalize_evidence(evidence_item)
                if normalized is not None:
                    evidence.append(normalized)
            if summary and not _sanitize_text(result_payload.get("summary"), 500):
                result_payload["summary"] = summary
            if evidence and not _sanitize_text(result_payload.get("summary"), 500):
                result_payload["summary"] = f"保留 {len(evidence)} 条相关 Evidence"
            for key in ("matched", "history_id", "user_question"):
                if key in item and key not in result_payload:
                    result_payload[key] = item.get(key)
            normalized_rows.append(
                {
                    "agent": agent,
                    "action": action,
                    "task_id": _sanitize_text(item.get("task_id"), 64),
                    "must_cover_id": _sanitize_text(item.get("must_cover_id"), 64),
                    "tool_name": _sanitize_text(item.get("tool_name"), 64)
                    or (
                        "history_read"
                        if action == "call_history_read"
                        else f"{agent}_subagent"
                    ),
                    "subquery": _sanitize_text(
                        item.get("subquery") or item.get("question"), 240
                    ),
                    "arguments": _coerce_dict(item.get("arguments")),
                    "evidence": evidence,
                    "result": {
                        "ok": bool(item.get("ok", True)),
                        "error": _sanitize_text(item.get("error"), 240),
                        "result": result_payload,
                    },
                }
            )
        return normalized_rows

    def _compact_tool_observations_for_compression(
        self, tool_observations: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """裁剪工具观测字段，生成压缩器输入。"""
        compact_rows: list[dict[str, Any]] = []
        for row in tool_observations[-8:]:
            if not isinstance(row, dict):
                continue
            compact_rows.append(
                {
                    "agent": _sanitize_text(row.get("agent"), 32),
                    "action": _sanitize_text(row.get("action"), 32),
                    "task_id": _sanitize_text(row.get("task_id"), 64),
                    "must_cover_id": _sanitize_text(row.get("must_cover_id"), 64),
                    "subquery": _sanitize_text(
                        row.get("subquery") or row.get("question"), 240
                    ),
                    "summary": self._summarize_observation_result(row),
                    "evidence": [
                        normalized
                        for normalized in (
                            self._normalize_evidence(item)
                            for item in _coerce_list(row.get("evidence"))[:6]
                        )
                        if normalized is not None
                    ],
                    "history_id": _sanitize_text(
                        _coerce_dict(_coerce_dict(row.get("result")).get("result")).get(
                            "history_id"
                        ),
                        64,
                    ),
                    "user_question": _sanitize_text(
                        _coerce_dict(_coerce_dict(row.get("result")).get("result")).get(
                            "user_question"
                        ),
                        240,
                    ),
                    "matched": bool(
                        _coerce_dict(_coerce_dict(row.get("result")).get("result")).get(
                            "matched"
                        )
                    ),
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
        """构建压缩器用户提示词。"""
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
                    "tool_observations": self._compact_tool_observations_for_compression(
                        tool_observations
                    ),
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
        question_plan_text: str,
        tool_observations: list[dict[str, Any]],
        turn_index: int,
        max_turns: int,
        force_final: bool = False,
    ) -> str:
        """构建主控当前轮次的用户提示词。"""
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
            f"这是本轮必须遵循的结构化问题计划：\n{question_plan_text}\n\n"
            "你可以直接回答、读取历史 Evidence，或把需要补证据的子问题交给 kg/rag 子agent。\n"
            f"{final_rule}\n\n"
            "这是你目前已经拿到的工具/历史读取结果：\n" + "\n".join(observation_lines)
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
        """调用压缩器模型压缩记忆与工具观测内容。"""
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
        response = await self._client.chat_with_tokens_async(
            messages=messages,
            temperature=0.0,
            timeout=self._conf.LLM_REQUEST_TIMEOUT_SECONDS,
            actor_role="controller",
            actor_name="orchestrator_prompt_compressor",
        )
        parsed = _parse_json_object(str(getattr(response, "content", "") or ""))
        tokens = {
            "prompt_tokens": int(getattr(response, "prompt_tokens", 0) or 0),
            "completion_tokens": int(getattr(response, "completion_tokens", 0) or 0),
            "total_tokens": int(getattr(response, "total_tokens", 0) or 0),
        }
        return {
            "qa_memory_text": _sanitize_text(parsed.get("qa_memory_text"), 6000)
            or "当前暂无问答对",
            "retrieval_question_index_text": _sanitize_text(
                parsed.get("retrieval_question_index_text"), 6000
            )
            or "当前暂无历史检索问题",
            "tool_observations": self._normalize_compressed_tool_observations(
                parsed.get("tool_observations")
            ),
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
        question_plan_text: str,
        tool_observations: list[dict[str, Any]],
        turn_index: int,
        max_turns: int,
        force_final: bool,
        compression_state: dict[str, Any],
        processing_steps: list[str],
        progress_callback: Callable[[dict[str, Any]], None] | None,
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        """在预算约束下构建主控决策消息，必要时执行压缩。"""
        current_qa_memory_text = str(qa_memory_text or "当前暂无问答对")
        current_retrieval_index_text = str(
            retrieval_index_text or "当前暂无历史检索问题"
        )
        current_tool_observations = (
            tool_observations if isinstance(tool_observations, list) else []
        )
        compression_tokens = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }

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
                        question_plan_text=question_plan_text,
                        tool_observations=current_tool_observations,
                        turn_index=turn_index,
                        max_turns=max_turns,
                        force_final=force_final,
                    ),
                },
            ]
            prompt_token_count = self._count_prompt_tokens(messages)
            compression_state["prompt_budget_limit"] = self._PROMPT_TOKEN_LIMIT
            compression_state["prompt_token_count_before_last_decision"] = int(
                prompt_token_count
            )
            compression_state["prompt_token_count_after_last_compression"] = int(
                prompt_token_count
            )

            if prompt_token_count <= self._PROMPT_TOKEN_LIMIT:
                return messages, compression_tokens

            current_compression_count = int(
                compression_state.get("compression_count") or 0
            )
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
            compression_tokens["completion_tokens"] += int(
                tokens.get("completion_tokens") or 0
            )
            compression_tokens["total_tokens"] += int(tokens.get("total_tokens") or 0)

            compression_state["compression_count"] = next_compression_index
            current_qa_memory_text = str(
                compressed_sections.get("qa_memory_text") or "当前暂无问答对"
            )
            current_retrieval_index_text = str(
                compressed_sections.get("retrieval_question_index_text")
                or "当前暂无历史检索问题"
            )
            compressed_tool_observations = compressed_sections.get("tool_observations")
            current_tool_observations = (
                compressed_tool_observations
                if isinstance(compressed_tool_observations, list)
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
                        question_plan_text=question_plan_text,
                        tool_observations=current_tool_observations,
                        turn_index=turn_index,
                        max_turns=max_turns,
                        force_final=force_final,
                    ),
                },
            ]
            compressed_prompt_token_count = self._count_prompt_tokens(messages)
            compression_state["prompt_token_count_after_last_compression"] = int(
                compressed_prompt_token_count
            )
            compression_events = compression_state.get("compression_events")
            if not isinstance(compression_events, list):
                compression_events = []
                compression_state["compression_events"] = compression_events
            compression_events.append(
                {
                    "attempt": next_compression_index,
                    "before_tokens": int(prompt_token_count),
                    "after_tokens": int(compressed_prompt_token_count),
                    "reason": _sanitize_text(
                        compressed_sections.get("compression_reason"), 240
                    ),
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
        question_plan_text: str = "",
        question_plan: QuestionPlan | None = None,
        tool_observations: list[dict[str, Any]],
        turn_index: int,
        max_turns: int,
        force_final: bool = False,
        image_data: str = "",
        file_id: str = "",
        compression_state: dict[str, Any],
        processing_steps: list[str],
        progress_callback: Callable[[dict[str, Any]], None] | None,
    ) -> tuple[dict[str, Any], dict[str, int]]:
        """调用主控模型生成下一步动作，并汇总 token 统计。"""
        messages, compression_tokens = await self._prepare_budgeted_messages(
            question=question,
            session_id=session_id,
            qa_memory_text=qa_memory_text,
            retrieval_index_text=retrieval_index_text,
            question_plan_text=question_plan_text,
            tool_observations=tool_observations,
            turn_index=turn_index,
            max_turns=max_turns,
            force_final=force_final,
            compression_state=compression_state,
            processing_steps=processing_steps,
            progress_callback=progress_callback,
        )
        tokens = normalize_token_usage(compression_tokens)
        decision_messages = list(messages)
        last_error = "invalid_orchestrator_action"

        for attempt in range(1, 3):
            if image_data or file_id:
                response = await self._client.chat_with_vision_async(
                    messages=decision_messages,
                    image_data=image_data,
                    file_id=file_id,
                    temperature=0.1,
                    timeout=self._conf.LLM_REQUEST_TIMEOUT_SECONDS,
                    actor_role="controller",
                    actor_name="orchestrator_controller",
                    response_format={"type": "json_object"},
                )
            else:
                response = await self._client.chat_with_tokens_async(
                    messages=decision_messages,
                    temperature=0.1,
                    timeout=self._conf.LLM_REQUEST_TIMEOUT_SECONDS,
                    actor_role="controller",
                    actor_name="orchestrator_controller",
                    response_format={"type": "json_object"},
                )

            merge_token_usage(
                tokens,
                {
                    "prompt_tokens": int(getattr(response, "prompt_tokens", 0) or 0),
                    "completion_tokens": int(
                        getattr(response, "completion_tokens", 0) or 0
                    ),
                    "total_tokens": int(getattr(response, "total_tokens", 0) or 0),
                },
            )
            raw_content = str(getattr(response, "content", "") or "").strip()
            try:
                action = OrchestratorAction.model_validate_json(raw_content)
                action_payload = action.model_dump()
                if question_plan is not None:
                    self._validate_plan_action(action_payload, question_plan)
                return action_payload, tokens
            except (ValidationError, ValueError, TypeError) as exc:
                last_error = f"{type(exc).__name__}:{_sanitize_text(exc, 240)}"
                if attempt >= 2:
                    break
                decision_messages = [
                    *messages,
                    {"role": "assistant", "content": raw_content[:1200]},
                    {
                        "role": "user",
                        "content": (
                            "上一次输出未通过 JSON Schema 校验。"
                            f"错误：{last_error}。"
                            "请按问题计划修正，并只返回一个完整 JSON 对象，不要使用 Markdown。"
                        ),
                    },
                ]

        raise OrchestratorActionValidationError(last_error)

    def _build_history_observation(
        self, action: dict[str, Any], tool_result: dict[str, Any]
    ) -> dict[str, Any]:
        """将 history_read 工具结果包装成统一观测结构。"""
        payload = _coerce_dict(tool_result.get("result"))
        meta = _coerce_dict(tool_result.get("meta"))
        evidence = [
            normalized
            for normalized in (
                self._normalize_evidence(item)
                for item in _coerce_list(payload.get("evidence"))
            )
            if normalized is not None
        ]
        claim_assessments = [
            item
            for item in _coerce_list(payload.get("claim_assessments"))
            if isinstance(item, dict)
        ]
        return {
            "agent": "history",
            "action": "call_history_read",
            "task_id": _sanitize_text(action.get("task_id"), 64),
            "must_cover_id": _sanitize_text(action.get("must_cover_id"), 64),
            "tool_name": "history_read",
            "subquery": _sanitize_text(
                action.get("user_question") or action.get("history_id"), 200
            ),
            "arguments": {
                "history_id": _sanitize_text(action.get("history_id"), 64),
                "user_question": _sanitize_text(action.get("user_question"), 200),
            },
            "duration_ms": float(meta.get("duration_ms") or 0.0),
            "token_usage": normalize_token_usage(meta.get("token_usage")),
            "evidence": evidence,
            "claim_assessments": claim_assessments,
            "result": {
                "ok": bool(tool_result.get("ok")),
                "error": str(tool_result.get("error") or ""),
                "result": {
                    "matched": bool(payload.get("matched")),
                    "summary": (
                        f"读取到 {len(evidence)} 条历史 Evidence"
                        if bool(payload.get("matched"))
                        else "未匹配到历史检索记录"
                    ),
                    "evidence_count": len(evidence),
                    "history_id": _sanitize_text(payload.get("history_id"), 64),
                    "user_question": _sanitize_text(payload.get("user_question"), 200),
                },
            },
        }

    def _build_subagent_observation(
        self, result: Any, action: dict[str, Any]
    ) -> dict[str, Any]:
        """将子代理结果包装成主控统一观测结构。"""
        query_records = [
            normalized
            for normalized in (
                self._normalize_query_record(
                    item, default_agent=str(result.target_agent or "unknown")
                )
                for item in (
                    result.query_records
                    if isinstance(result.query_records, list)
                    else []
                )
            )
            if normalized is not None
        ]
        evidence = [
            normalized
            for normalized in (
                self._normalize_evidence(
                    item.model_dump() if isinstance(item, Evidence) else item
                )
                for item in result.evidence
            )
            if normalized is not None
        ]
        evidence_summary = self._summarize_evidence(evidence, limit=3)
        risk_flags = self._detect_numeric_conflicts(evidence)
        telemetry = result.telemetry if isinstance(result.telemetry, dict) else {}
        claim_assessments = [
            item.model_dump() if isinstance(item, ClaimEvidenceAssessment) else item
            for item in getattr(result, "claim_assessments", [])
            if isinstance(item, (ClaimEvidenceAssessment, dict))
        ]
        return {
            "agent": str(result.target_agent or ""),
            "action": str(action.get("action") or ""),
            "task_id": _sanitize_text(action.get("task_id"), 64),
            "must_cover_id": _sanitize_text(action.get("must_cover_id"), 64),
            "tool_name": f"{result.target_agent}_subagent",
            "requested_tool_name": "",
            "subquery": _sanitize_text(result.question, 240),
            "arguments": {},
            "duration_ms": float(telemetry.get("duration_ms") or 0.0),
            "token_usage": normalize_token_usage(result.token_usage),
            "query_records": query_records,
            "evidence": evidence,
            "claim_assessments": claim_assessments,
            "result": {
                "ok": True,
                "error": "",
                "result": {
                    "summary": evidence_summary
                    or f"signal={result.signal}; evidence_count={len(evidence)}; queries={len(result.queries_used)}",
                    "signal": result.signal,
                    "coverage": result.coverage,
                    "missing_points": result.missing_points,
                    "direct_support": result.direct_support,
                    "reason": result.reason,
                    "queries_used": result.queries_used,
                    "query_records": query_records,
                    "evidence_count": len(evidence),
                    "claim_assessments": claim_assessments,
                    "retrieval_risk_flags": risk_flags,
                },
            },
        }

    def _build_subtask(
        self,
        *,
        action: dict[str, Any],
        max_tool_calls: int,
        question_plan: QuestionPlan,
    ) -> SubTask:
        """根据动作中的精确计划 ID 构建子代理任务对象。"""
        return PlanningService().build_subtask(
            action=action,
            max_tool_calls=max_tool_calls,
            question_plan=question_plan,
        )

    def _build_ready_subtasks(
        self,
        *,
        action: dict[str, Any],
        max_tool_calls: int,
        question_plan: QuestionPlan,
    ) -> list[SubTask]:
        """扫描全计划并构建所有依赖已满足的可执行任务。"""
        return self._retrieval_scheduler().build_ready_subtasks(
            action=action,
            max_tool_calls=max_tool_calls,
            question_plan=question_plan,
        )

    def _retrieval_scheduler(self) -> RetrievalScheduler:
        """按控制器当前运行参数构建无状态检索调度器。"""
        return RetrievalScheduler(
            subagent_client=getattr(self, "_subagent_client", None),
            planning_service=PlanningService(),
            total_concurrency=self._DAG_TOTAL_CONCURRENCY,
            agent_concurrency=self._DAG_AGENT_CONCURRENCY,
            task_timeout_seconds=self._DAG_TASK_TIMEOUT_SECONDS,
            max_attempts=self._DAG_MAX_ATTEMPTS,
        )

    async def _execute_subtasks(self, tasks: list[SubTask]) -> list[SubAgentResult]:
        """按总量和单 Agent 上限并发执行，并隔离超时与单任务失败。"""
        return await self._retrieval_scheduler().execute_subtasks(tasks)

    @staticmethod
    def _record_claim_assessments(
        question_plan: QuestionPlan,
        rows: Any,
        *,
        task_id: str,
        must_cover_id: str,
        allowed_evidence_ids: set[str],
    ) -> None:
        """记录与当前计划任务精确匹配的 claim 支持判定。"""
        for row in _coerce_list(rows):
            try:
                assessment = ClaimEvidenceAssessment.model_validate(row)
            except (ValidationError, ValueError, TypeError):
                continue
            if (
                assessment.task_id != task_id
                or assessment.must_cover_id != must_cover_id
                or assessment.evidence_id not in allowed_evidence_ids
            ):
                continue
            key = (
                assessment.task_id,
                assessment.must_cover_id,
                assessment.evidence_id,
                assessment.verifier_version,
            )
            question_plan.claim_evidence_assessments = [
                item
                for item in question_plan.claim_evidence_assessments
                if (
                    item.task_id,
                    item.must_cover_id,
                    item.evidence_id,
                    item.verifier_version,
                )
                != key
            ]
            question_plan.claim_evidence_assessments.append(assessment)

    async def _verify_task_evidence(
        self,
        *,
        question_plan: QuestionPlan,
        task_id: str,
        must_cover_id: str,
        evidence_rows: Any,
        existing_rows: Any,
    ) -> tuple[list[ClaimEvidenceAssessment], dict[str, int]]:
        """补齐当前任务尚未生成的 claim-evidence 支持判定。"""
        llm_client = getattr(self, "_client", None)
        timeout = (
            float(self._conf.LLM_REQUEST_TIMEOUT_SECONDS)
            if llm_client is not None
            else 0.0
        )
        return await EvidencePipeline(
            llm_client=llm_client,
            timeout=timeout,
        ).verify_task_evidence(
            question_plan=question_plan,
            task_id=task_id,
            must_cover_id=must_cover_id,
            evidence_rows=evidence_rows,
            existing_rows=existing_rows,
        )

    @staticmethod
    def _refresh_must_cover_support(
        question_plan: QuestionPlan, must_cover_id: str
    ) -> None:
        """根据显式支持判定刷新 must-cover，而不读取 Evidence 数量。"""
        must_cover_item = next(
            (
                item
                for item in question_plan.must_cover
                if item.item_id == must_cover_id
            ),
            None,
        )
        if must_cover_item is None:
            return
        support_statuses = {
            item.support_status
            for item in question_plan.claim_evidence_assessments
            if item.must_cover_id == must_cover_id
        }
        aggregate_status: ClaimSupportStatus
        if "contradicted" in support_statuses:
            aggregate_status = "contradicted"
        elif "supported" in support_statuses:
            aggregate_status = "supported"
        elif "partially_supported" in support_statuses:
            aggregate_status = "partially_supported"
        elif support_statuses == {"irrelevant"}:
            aggregate_status = "irrelevant"
        else:
            aggregate_status = "uncertain"
        must_cover_item.support_status = aggregate_status
        if aggregate_status == "supported":
            must_cover_item.status = "covered"
            return
        task_statuses = {
            item.status
            for item in question_plan.retrieval_tasks
            if item.must_cover_id == must_cover_id
        }
        must_cover_item.status = (
            "pending"
            if task_statuses.intersection({"pending", "needs_supplement"})
            else "missing"
        )

    @staticmethod
    def _activate_deferred_supplement(
        question_plan: QuestionPlan, must_cover_id: str
    ) -> bool:
        """仅为指定证据缺口激活尚未执行的补充检索器。"""
        activated = False
        for task in question_plan.retrieval_tasks:
            if (
                task.must_cover_id == must_cover_id
                and task.execution_stage == "supplement"
                and task.status == "deferred"
            ):
                task.status = "needs_supplement"
                activated = True
        return activated

    @staticmethod
    def _update_question_plan(
        question_plan: QuestionPlan, task: SubTask, result: Any
    ) -> None:
        """分别更新检索执行状态和 claim 支持状态。"""
        must_cover_item = next(
            (
                item
                for item in question_plan.must_cover
                if item.item_id == task.must_cover_id
            ),
            None,
        )
        retrieval_task = next(
            (
                item
                for item in question_plan.retrieval_tasks
                if item.task_id == task.task_id
                and item.must_cover_id == task.must_cover_id
                and item.target_agent == task.target_agent
            ),
            None,
        )
        if must_cover_item is None or retrieval_task is None:
            return
        reason = str(getattr(result, "reason", "") or "").strip()
        technical_failure = reason.startswith(("error:", "subagent_error:"))
        retrieval_task.status = "failed" if technical_failure else "completed"
        UnifiedOrchestratorController._record_claim_assessments(
            question_plan,
            getattr(result, "claim_assessments", []),
            task_id=task.task_id,
            must_cover_id=task.must_cover_id,
            allowed_evidence_ids=_evidence_ids(getattr(result, "evidence", [])),
        )
        has_supported_assessment = any(
            item.must_cover_id == must_cover_item.item_id
            and item.support_status == "supported"
            for item in question_plan.claim_evidence_assessments
        )
        if not has_supported_assessment:
            UnifiedOrchestratorController._activate_deferred_supplement(
                question_plan, must_cover_item.item_id
            )
        UnifiedOrchestratorController._refresh_must_cover_support(
            question_plan, must_cover_item.item_id
        )

    @staticmethod
    def _update_question_plan_from_history(
        question_plan: QuestionPlan,
        action: dict[str, Any],
        history_result: dict[str, Any],
    ) -> None:
        """在历史 Evidence 能明确对应问题点时更新计划覆盖。"""
        payload = _coerce_dict(history_result.get("result"))
        must_cover_id = _sanitize_text(action.get("must_cover_id"), 64)
        matched_item = next(
            (
                item
                for item in question_plan.must_cover
                if item.item_id == must_cover_id and item.status == "pending"
            ),
            None,
        )
        if matched_item is None:
            return
        task_id = _sanitize_text(action.get("task_id"), 64)
        matched_task = next(
            (
                item
                for item in question_plan.retrieval_tasks
                if item.task_id == task_id
                and item.must_cover_id == matched_item.item_id
                and item.status in {"pending", "needs_supplement"}
            ),
            None,
        )
        if matched_task is None:
            return
        matched_task.status = (
            "completed" if bool(history_result.get("ok")) else "failed"
        )
        UnifiedOrchestratorController._record_claim_assessments(
            question_plan,
            payload.get("claim_assessments"),
            task_id=matched_task.task_id,
            must_cover_id=matched_item.item_id,
            allowed_evidence_ids=_evidence_ids(payload.get("evidence")),
        )
        has_supported_assessment = any(
            item.must_cover_id == matched_item.item_id
            and item.support_status == "supported"
            for item in question_plan.claim_evidence_assessments
        )
        if not has_supported_assessment:
            UnifiedOrchestratorController._activate_deferred_supplement(
                question_plan, matched_item.item_id
            )
        UnifiedOrchestratorController._refresh_must_cover_support(
            question_plan, matched_item.item_id
        )

    @staticmethod
    def _mark_supplement_tasks(
        question_plan: QuestionPlan, readiness: dict[str, Any]
    ) -> None:
        """把证据不足的问题点及其已执行任务标为可补检索。"""
        sufficiency = _coerce_dict(readiness.get("evidence_sufficiency"))
        insufficient_ids = {
            _sanitize_text(item.get("item_id"), 64)
            for item in _coerce_list(sufficiency.get("items"))
            if isinstance(item, dict) and not bool(item.get("sufficient"))
        }
        insufficient_ids.discard("")
        for must_cover_item in question_plan.must_cover:
            if must_cover_item.item_id not in insufficient_ids:
                continue
            must_cover_item.status = "pending"
            deferred_activated = (
                UnifiedOrchestratorController._activate_deferred_supplement(
                    question_plan, must_cover_item.item_id
                )
            )
            if deferred_activated:
                continue
            for retrieval_task in question_plan.retrieval_tasks:
                if (
                    retrieval_task.must_cover_id == must_cover_item.item_id
                    and retrieval_task.status in {"completed", "failed"}
                ):
                    retrieval_task.status = "needs_supplement"

    @staticmethod
    def _question_plan_coverage(question_plan: QuestionPlan) -> dict[str, Any]:
        """计算 must-cover 覆盖率和未覆盖问题点。"""
        total = len(question_plan.must_cover)
        covered = [
            item for item in question_plan.must_cover if item.status == "covered"
        ]
        missing = [
            item for item in question_plan.must_cover if item.status == "missing"
        ]
        pending = [
            item for item in question_plan.must_cover if item.status == "pending"
        ]
        return {
            "total": total,
            "covered": len(covered),
            "missing": len(missing),
            "pending": len(pending),
            "coverage_ratio": round(len(covered) / total, 4) if total else 0.0,
            "complete": bool(total) and len(covered) == total,
            "uncovered_items": [
                {
                    "item_id": item.item_id,
                    "question": item.question,
                    "support_status": item.support_status,
                }
                for item in [*pending, *missing]
            ],
            "support_status_counts": {
                status: sum(
                    1
                    for item in question_plan.must_cover
                    if item.support_status == status
                )
                for status in (
                    "supported",
                    "partially_supported",
                    "contradicted",
                    "irrelevant",
                    "uncertain",
                )
            },
        }

    def _retrieval_progress_state(
        self,
        tool_observations: list[dict[str, Any]],
        question_plan: QuestionPlan,
    ) -> dict[str, MustCoverProgressState]:
        """生成逐 must-cover 的语义进展快照。"""
        return build_retrieval_progress(
            question_plan=question_plan,
            evidence_by_item=self._evidence_by_must_cover(
                tool_observations, question_plan
            ),
        )

    def _advance_retrieval_progress(
        self,
        *,
        tool_observations: list[dict[str, Any]],
        question_plan: QuestionPlan,
        previous_state: dict[str, MustCoverProgressState],
        no_progress_rounds: dict[str, int],
        no_progress_reasons: dict[str, str],
        attempted_must_cover_ids: set[str],
    ) -> tuple[dict[str, MustCoverProgressState], dict[str, int], dict[str, str]]:
        """只更新本轮实际检索问题点的连续无进展状态。"""
        current_state = self._retrieval_progress_state(tool_observations, question_plan)
        next_rounds = dict(no_progress_rounds)
        next_reasons = dict(no_progress_reasons)
        for item_id in attempted_must_cover_ids:
            previous = previous_state.get(item_id)
            current = current_state.get(item_id)
            if previous is None or current is None:
                continue
            if has_semantic_progress(previous, current):
                next_rounds[item_id] = 0
                next_reasons.pop(item_id, None)
                continue
            next_rounds[item_id] = next_rounds.get(item_id, 0) + 1
            next_reasons[item_id] = no_progress_reason(previous, current)
        return current_state, next_rounds, next_reasons

    @classmethod
    def _all_unresolved_items_stalled(
        cls, question_plan: QuestionPlan, no_progress_rounds: dict[str, int]
    ) -> bool:
        """仅当所有未覆盖问题点都连续无进展时终止检索。"""
        unresolved_ids = [
            item.item_id
            for item in question_plan.must_cover
            if item.status != "covered"
        ]
        return bool(unresolved_ids) and all(
            no_progress_rounds.get(item_id, 0) >= cls._MAX_NO_PROGRESS_ROUNDS
            for item_id in unresolved_ids
        )

    @staticmethod
    def _build_progress_telemetry(
        progress_state: dict[str, MustCoverProgressState],
        no_progress_rounds: dict[str, int],
        no_progress_reasons: dict[str, str],
    ) -> dict[str, Any]:
        """输出可追踪的逐问题点语义进展。"""
        return {
            "must_cover_progress": {
                item_id: state.model_dump() for item_id, state in progress_state.items()
            },
            "no_progress_rounds_by_must_cover": dict(no_progress_rounds),
            "no_progress_reasons": dict(no_progress_reasons),
            "no_progress_rounds": max(no_progress_rounds.values(), default=0),
        }

    def _build_evidence_summary(
        self, tool_observations: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """汇总工具观测中的证据覆盖信息。"""
        covered_topics: list[str] = []
        for row in tool_observations:
            if not isinstance(row, dict):
                continue
            subquery = _sanitize_text(row.get("subquery"), 120)
            if subquery and subquery not in covered_topics:
                covered_topics.append(subquery)
        fusion = self._fuse_tool_evidence(tool_observations)
        evidence = [item.model_dump() for item in fusion.evidence]
        supported_points = []
        citations: list[str] = []
        for item in evidence[:5]:
            excerpt = self._format_evidence_excerpt(item)
            if excerpt and excerpt not in supported_points:
                supported_points.append(excerpt)
            citation = self._evidence_citation_label(item)
            if citation and citation not in citations:
                citations.append(citation)
        retrieval_risk_flags = self._detect_numeric_conflicts(evidence)
        conflicts = self._detect_evidence_conflicts(evidence)
        return {
            "answer_mode": "retrieval_augmented" if evidence else "direct_answer",
            "supported_points": supported_points,
            "citations": citations,
            "covered_topics": covered_topics[:5],
            "evidence_count": len(evidence),
            "raw_evidence_count": fusion.raw_evidence_count,
            "duplicate_count": fusion.duplicate_count,
            "distinct_publication_count": fusion.distinct_publication_count,
            "corroborated_claim_count": fusion.corroborated_claim_count,
            "fused_evidence": evidence,
            "retrieval_risk_flags": retrieval_risk_flags,
            "conflicts": conflicts,
        }

    @staticmethod
    def _tool_evidence_rows(
        tool_observations: list[dict[str, Any]],
    ) -> list[tuple[str, dict[str, Any]]]:
        """从工具观测收集带代理来源的 Evidence。"""
        rows: list[tuple[str, dict[str, Any]]] = []
        for observation in tool_observations:
            if not isinstance(observation, dict):
                continue
            agent = _sanitize_text(observation.get("agent"), 32) or "unknown"
            for item in _coerce_list(observation.get("evidence")):
                if isinstance(item, dict):
                    rows.append((agent, item))
        return rows

    @classmethod
    def _evidence_by_must_cover(
        cls,
        tool_observations: list[dict[str, Any]],
        question_plan: QuestionPlan,
    ) -> dict[str, list[tuple[str, Evidence | dict[str, Any]]]]:
        """按计划问题点归集 Evidence，避免跨结论错误计数。"""
        grouped: dict[str, list[tuple[str, Evidence | dict[str, Any]]]] = {
            item.item_id: [] for item in question_plan.must_cover
        }
        for observation in tool_observations:
            if not isinstance(observation, dict):
                continue
            item_id = _sanitize_text(observation.get("must_cover_id"), 64)
            if item_id not in grouped:
                continue
            agent = _sanitize_text(observation.get("agent"), 32) or "unknown"
            for evidence in _coerce_list(observation.get("evidence")):
                if isinstance(evidence, dict):
                    grouped[item_id].append((agent, evidence))
        return grouped

    @classmethod
    def _build_answer_generation_context(
        cls,
        *,
        question_plan: QuestionPlan,
        tool_observations: list[dict[str, Any]],
        conflict_rows: Any,
    ) -> AnswerGenerationContext:
        """构建按 must-cover 组织且只包含已校验支持证据的终答上下文。"""
        return build_answer_generation_context(
            question_plan=question_plan,
            evidence_by_item=cls._evidence_by_must_cover(
                tool_observations, question_plan
            ),
            conflicts=_coerce_list(conflict_rows),
        )

    @classmethod
    def _fuse_tool_evidence(
        cls, tool_observations: list[dict[str, Any]]
    ) -> EvidenceFusionResult:
        """收集工具观测并执行统一 Evidence 融合。"""
        return fuse_evidence(cls._tool_evidence_rows(tool_observations))

    def _assess_answer_readiness(
        self,
        tool_observations: list[dict[str, Any]],
        medical_risk: MedicalRiskAssessment | None = None,
        question_plan: QuestionPlan | None = None,
    ) -> dict[str, Any]:
        """根据现有直接证据和冲突标记判断是否允许生成答案。"""
        missing_points: list[str] = []
        for row in tool_observations:
            if not isinstance(row, dict):
                continue
            result_payload = _coerce_dict(_coerce_dict(row.get("result")).get("result"))
            for item in _coerce_list(result_payload.get("missing_points")):
                point = _sanitize_text(item, 160)
                if point and point not in missing_points:
                    missing_points.append(point)

        fusion = self._fuse_tool_evidence(tool_observations)
        evidence = [item.model_dump() for item in fusion.evidence]
        risk_flags = self._detect_numeric_conflicts(evidence)
        conflicts = self._detect_evidence_conflicts(evidence)
        if not evidence:
            missing_points.append("direct_evidence")
        if risk_flags and question_plan is None:
            missing_points.append("unresolved_conflict")
        plan_coverage = (
            self._question_plan_coverage(question_plan)
            if question_plan is not None
            else None
        )
        sufficiency = None
        if question_plan is not None:
            sufficiency = assess_evidence_sufficiency(
                question_plan=question_plan,
                evidence_by_item=self._evidence_by_must_cover(
                    tool_observations, question_plan
                ),
            )
            if evidence and not sufficiency.sufficient:
                for item in sufficiency.items:
                    marker = f"must_cover:{item.item_id}"
                    if not item.sufficient and marker not in missing_points:
                        missing_points.append(marker)
        return {
            "ready": bool(evidence) and not missing_points,
            "evidence_count": len(evidence),
            "raw_evidence_count": fusion.raw_evidence_count,
            "distinct_publication_count": fusion.distinct_publication_count,
            "missing_points": missing_points,
            "unresolved_conflicts": risk_flags,
            "conflict_details": conflicts,
            "plan_coverage": plan_coverage,
            "evidence_sufficiency": (
                sufficiency.model_dump() if sufficiency is not None else None
            ),
        }

    def _build_budget_telemetry(
        self, compression_state: dict[str, Any]
    ) -> dict[str, Any]:
        """构建提示词预算相关观测字段。"""
        compression_events = compression_state.get("compression_events")
        return {
            "prompt_budget_limit": int(
                compression_state.get("prompt_budget_limit") or self._PROMPT_TOKEN_LIMIT
            ),
            "compression_count": int(compression_state.get("compression_count") or 0),
            "prompt_token_count_before_last_decision": int(
                compression_state.get("prompt_token_count_before_last_decision") or 0
            ),
            "prompt_token_count_after_last_compression": int(
                compression_state.get("prompt_token_count_after_last_compression") or 0
            ),
            "aborted_for_prompt_budget": bool(
                compression_state.get("aborted_for_prompt_budget")
            ),
            "prompt_budget_events": (
                compression_events if isinstance(compression_events, list) else []
            ),
        }

    def _build_final_answer_system_prompt(self) -> str:
        """构建最终答案生成阶段的系统提示词。"""
        return AnswerPipeline.system_prompt()

    def _build_final_answer_user_prompt(
        self,
        *,
        question: str,
        answer_context: AnswerGenerationContext,
    ) -> str:
        """构建最终答案生成阶段的用户提示词。"""
        return AnswerPipeline.user_prompt(
            question=question,
            answer_context=answer_context,
        )

    async def _stream_final_answer(
        self,
        *,
        question: str,
        tool_observations: list[dict[str, Any]],
        answer_context: AnswerGenerationContext,
        image_data: str,
        file_id: str,
        progress_callback: Callable[[dict[str, Any]], None] | None,
    ) -> tuple[str, dict[str, int]]:
        """流式生成最终答案，并通过回调推送增量正文。"""
        _ = tool_observations
        return await AnswerPipeline(
            llm_client=self._client,
            timeout=float(self._conf.LLM_REQUEST_TIMEOUT_SECONDS),
        ).stream(
            question=question,
            answer_context=answer_context,
            image_data=image_data,
            file_id=file_id,
            progress_callback=progress_callback,
        )

    def _build_over_budget_result(
        self,
        *,
        tool_observations: list[dict[str, Any]],
        processing_steps: list[str],
        retrieval_round: int,
        max_turns: int,
        token_usage: dict[str, int],
        trace_id: str,
        compression_state: dict[str, Any],
    ) -> dict[str, Any]:
        """构建超预算中止场景的统一返回结构。"""
        evidence_summary = self._build_evidence_summary(tool_observations)
        evidence_summary["answer_mode"] = "aborted_over_budget"
        confidence = _low_confidence("answer_not_generated")
        return {
            "output": "这个问题超出软件能力范围，无法回答",
            "output_type": "orchestrator_aborted_over_budget",
            "confidence": confidence.level,
            "confidence_breakdown": confidence.model_dump(),
            "tool_observations": tool_observations,
            "reflection_events": [],
            "processing_steps": processing_steps,
            "telemetry": {
                "controller_mode": "explicit_memory_with_subagents",
                "retrieval_round": retrieval_round,
                "max_turns": max_turns,
                "trace_id": trace_id,
                **token_usage,
                "final_source": "prompt_budget_guard",
                **self._build_budget_telemetry(compression_state),
            },
            "evidence_summary": evidence_summary,
        }

    def _build_invalid_decision_result(
        self,
        *,
        tool_observations: list[dict[str, Any]],
        processing_steps: list[str],
        retrieval_round: int,
        max_turns: int,
        token_usage: dict[str, int],
        trace_id: str,
        compression_state: dict[str, Any],
        error: str,
    ) -> dict[str, Any]:
        """构建主控动作无法通过结构化校验时的安全拒答。"""
        evidence_summary = self._build_evidence_summary(tool_observations)
        evidence_summary["answer_mode"] = "abstained_invalid_decision"
        confidence = _low_confidence("answer_not_generated")
        return {
            "output": "当前无法可靠解析回答策略，请稍后重试。",
            "output_type": "orchestrator_abstained_invalid_decision",
            "confidence": confidence.level,
            "confidence_breakdown": confidence.model_dump(),
            "tool_observations": tool_observations,
            "reflection_events": [],
            "processing_steps": [
                *processing_steps,
                "主控动作连续两次未通过结构化校验，已安全拒答",
            ],
            "telemetry": {
                "controller_mode": "explicit_memory_with_subagents",
                "retrieval_round": retrieval_round,
                "max_turns": max_turns,
                "trace_id": trace_id,
                **token_usage,
                "final_source": "invalid_orchestrator_action",
                "decision_error": _sanitize_text(error, 300),
                **self._build_budget_telemetry(compression_state),
            },
            "evidence_summary": evidence_summary,
        }

    def _build_insufficient_evidence_result(
        self,
        *,
        tool_observations: list[dict[str, Any]],
        processing_steps: list[str],
        retrieval_round: int,
        max_turns: int,
        token_usage: dict[str, int],
        trace_id: str,
        compression_state: dict[str, Any],
        readiness: dict[str, Any],
    ) -> dict[str, Any]:
        """构建证据不足或冲突未解决时的安全拒答。"""
        evidence_summary = self._build_evidence_summary(tool_observations)
        evidence_summary.update(
            {
                "answer_mode": "abstained_insufficient_evidence",
                "missing_points": readiness.get("missing_points") or [],
                "unresolved_conflicts": readiness.get("unresolved_conflicts") or [],
                "conflict_details": readiness.get("conflict_details") or [],
                "plan_coverage": readiness.get("plan_coverage"),
                "evidence_sufficiency": readiness.get("evidence_sufficiency"),
            }
        )
        confidence = assess_evidence_confidence(
            sufficiency=readiness.get("evidence_sufficiency")
        )
        return {
            "output": "当前证据不足或存在未解决冲突，无法给出可靠回答。",
            "output_type": "orchestrator_abstained_insufficient_evidence",
            "confidence": confidence.level,
            "confidence_breakdown": confidence.model_dump(),
            "tool_observations": tool_observations,
            "reflection_events": [],
            "processing_steps": [
                *processing_steps,
                "未达到回答条件，已停止生成答案",
            ],
            "telemetry": {
                "controller_mode": "explicit_memory_with_subagents",
                "retrieval_round": retrieval_round,
                "max_turns": max_turns,
                "trace_id": trace_id,
                **token_usage,
                "final_source": "insufficient_evidence_policy",
                **self._build_budget_telemetry(compression_state),
            },
            "evidence_summary": evidence_summary,
        }

    @staticmethod
    def _attach_medical_risk_telemetry(
        result: dict[str, Any], assessment: MedicalRiskAssessment
    ) -> dict[str, Any]:
        """将医疗风险 Gate 结果写入统一 telemetry。"""
        telemetry = _coerce_dict(result.get("telemetry"))
        telemetry["medical_risk"] = assessment.model_dump()
        result["telemetry"] = telemetry
        return result

    @staticmethod
    def _attach_question_plan(
        result: dict[str, Any], question_plan: QuestionPlan
    ) -> dict[str, Any]:
        """将结构化问题计划及其覆盖状态写入结果。"""
        status_counts = {
            status: sum(1 for item in question_plan.must_cover if item.status == status)
            for status in ("pending", "covered", "missing")
        }
        result["question_plan"] = question_plan.model_dump()
        telemetry = _coerce_dict(result.get("telemetry"))
        telemetry["question_plan"] = {
            "plan_id": question_plan.plan_id,
            "must_cover_count": len(question_plan.must_cover),
            "coverage": UnifiedOrchestratorController._question_plan_coverage(
                question_plan
            ),
            **status_counts,
        }
        result["telemetry"] = telemetry
        return result

    @classmethod
    def _attach_evidence_bundle(
        cls,
        result: dict[str, Any],
        *,
        question: str,
        question_plan: QuestionPlan,
        tool_observations: list[dict[str, Any]],
        sufficiency: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """把最终 Evidence Bundle 写入终止结果和遥测。"""
        bundle = build_evidence_bundle(
            question=question,
            plan_id=question_plan.plan_id,
            rows=cls._tool_evidence_rows(tool_observations),
            coverage=cls._question_plan_coverage(question_plan),
            sufficiency=sufficiency,
        )
        result["evidence_bundle"] = bundle.model_dump()
        telemetry = _coerce_dict(result.get("telemetry"))
        telemetry["evidence_bundle"] = {
            "bundle_id": bundle.bundle_id,
            "evidence_count": len(bundle.evidence),
            "distinct_publication_count": bundle.distinct_publication_count,
            "conflict_count": len(bundle.conflicts),
        }
        result["telemetry"] = telemetry
        return result

    def _build_urgent_redirect_result(
        self,
        *,
        assessment: MedicalRiskAssessment,
        trace_id: str,
    ) -> dict[str, Any]:
        """构建紧急医疗信号的立即就医结果。"""
        confidence = _low_confidence("medical_risk_redirect")
        result = {
            "output": (
                "你描述的情况可能涉及医疗紧急风险。请立即联系当地急救服务或尽快前往急诊，"
                "不要等待在线问答，也不要自行调整药物。"
            ),
            "output_type": "orchestrator_urgent_redirect",
            "confidence": confidence.level,
            "confidence_breakdown": confidence.model_dump(),
            "tool_observations": [],
            "reflection_events": [],
            "processing_steps": ["医疗风险 Gate 命中紧急信号，已停止普通问答"],
            "telemetry": {
                "controller_mode": "medical_risk_gate",
                "retrieval_round": 0,
                "trace_id": trace_id,
                **empty_token_usage(),
                "final_source": "medical_risk_gate",
            },
            "evidence_summary": {
                "answer_mode": "urgent_redirect",
                "supported_points": [],
                "citations": [],
                "covered_topics": [],
                "evidence_count": 0,
                "retrieval_risk_flags": [],
            },
        }
        return self._attach_medical_risk_telemetry(result, assessment)

    def _build_safety_caution_result(
        self,
        *,
        assessment: MedicalRiskAssessment,
        trace_id: str,
    ) -> dict[str, Any]:
        """主体或当前状态不明确时给出保守安全分流。"""
        confidence = _low_confidence("medical_risk_context_uncertain")
        result = {
            "output": (
                "如果这些症状或危险血压是你本人或他人当前正在发生的情况，请立即联系当地急救服务或尽快前往急诊，"
                "不要等待在线问答，也不要自行调整药物。若你只是在咨询医学知识，请补充说明这是知识问题、历史情况还是当前症状。"
            ),
            "output_type": "orchestrator_medical_risk_clarification",
            "confidence": confidence.level,
            "confidence_breakdown": confidence.model_dump(),
            "tool_observations": [],
            "reflection_events": [],
            "processing_steps": ["医疗风险语境不明确，已执行保守安全分流"],
            "telemetry": {
                "controller_mode": "medical_risk_gate",
                "retrieval_round": 0,
                "trace_id": trace_id,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "final_source": "medical_risk_gate",
            },
            "evidence_summary": {
                "answer_mode": "medical_risk_clarification",
                "supported_points": [],
                "citations": [],
                "evidence_count": 0,
            },
        }
        return self._attach_medical_risk_telemetry(result, assessment)

    async def run(
        self,
        *,
        input_text: str,
        session_id: str = "",
        user_id: str = "",
        trace_id: str = "",
        max_turns: int = 3,
        image_data: str = "",
        file_id: str = "",
        memory_context: dict[str, Any] | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """执行主控决策循环，直到终答或触发保护性中止。"""
        _ = user_id
        safe_question = str(input_text or "").strip()
        safe_session_id = str(session_id or "").strip()
        safe_trace_id = str(trace_id or "").strip()
        medical_risk = assess_medical_risk(safe_question)
        if medical_risk.action == "urgent_redirect":
            return self._build_urgent_redirect_result(
                assessment=medical_risk,
                trace_id=safe_trace_id,
            )
        if medical_risk.action == "safety_caution":
            return self._build_safety_caution_result(
                assessment=medical_risk,
                trace_id=safe_trace_id,
            )
        question_plan = build_question_plan(safe_question, medical_risk)
        safe_memory = memory_context if isinstance(memory_context, dict) else {}
        qa_memory_text = str(safe_memory.get("qa_memory_text") or "当前暂无问答对")
        retrieval_index_text = str(
            safe_memory.get("retrieval_question_index_text") or "当前暂无历史检索问题"
        )
        safe_max_turns = max(1, int(max_turns or 1))
        safe_tool_calls = max(
            1,
            min(
                3,
                int(self._conf.A2A_SUBAGENT_MAX_TOOL_CALLS_DEFAULT),
                int(self._conf.A2A_SUBAGENT_MAX_TOOL_CALLS_MAX),
            ),
        )

        tool_observations: list[dict[str, Any]] = []
        processing_steps: list[str] = [
            "已加载显式 QA 记忆与历史检索问题索引",
            f"医疗风险 Gate：{medical_risk.risk_level}",
            f"已生成结构化问题计划：{len(question_plan.must_cover)} 个 must-cover",
        ]
        token_usage = empty_token_usage()
        retrieval_round = 0
        final_answer = ""
        confidence: ConfidenceLevel = "low"
        confidence_breakdown = _low_confidence("answer_not_generated")
        no_progress_rounds = {item.item_id: 0 for item in question_plan.must_cover}
        no_progress_reasons: dict[str, str] = {}
        previous_progress_state = self._retrieval_progress_state(
            tool_observations, question_plan
        )
        stop_reason = ""
        accepted_readiness: dict[str, Any] | None = None
        accepted_answer_context: AnswerGenerationContext | None = None
        answer_validation: AnswerValidationResult | None = None
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
                    question_plan_text=_safe_json(
                        question_plan.model_dump(), max_len=12000
                    ),
                    question_plan=question_plan,
                    tool_observations=tool_observations,
                    turn_index=turn_index,
                    max_turns=safe_max_turns,
                    force_final=False,
                    image_data=str(image_data or "").strip(),
                    file_id=str(file_id or "").strip(),
                    compression_state=compression_state,
                    processing_steps=processing_steps,
                    progress_callback=progress_callback,
                )
            except PromptBudgetExceededError:
                return self._attach_evidence_bundle(
                    self._attach_question_plan(
                        self._attach_medical_risk_telemetry(
                            self._build_over_budget_result(
                                tool_observations=tool_observations,
                                processing_steps=processing_steps,
                                retrieval_round=retrieval_round,
                                max_turns=safe_max_turns,
                                token_usage=token_usage,
                                trace_id=safe_trace_id,
                                compression_state=compression_state,
                            ),
                            medical_risk,
                        ),
                        question_plan,
                    ),
                    question=safe_question,
                    question_plan=question_plan,
                    tool_observations=tool_observations,
                )
            except OrchestratorActionValidationError as exc:
                return self._attach_evidence_bundle(
                    self._attach_question_plan(
                        self._attach_medical_risk_telemetry(
                            self._build_invalid_decision_result(
                                tool_observations=tool_observations,
                                processing_steps=processing_steps,
                                retrieval_round=retrieval_round,
                                max_turns=safe_max_turns,
                                token_usage=token_usage,
                                trace_id=safe_trace_id,
                                compression_state=compression_state,
                                error=str(exc),
                            ),
                            medical_risk,
                        ),
                        question_plan,
                    ),
                    question=safe_question,
                    question_plan=question_plan,
                    tool_observations=tool_observations,
                )
            merge_token_usage(token_usage, tokens)
            action_name = str(action.get("action") or "").strip()
            processing_steps.append(
                f"第 {turn_index} 轮主控决策：{action_name or 'unknown'}；原因：{_sanitize_text(action.get('reason'), 160) or '无'}"
            )

            if action_name == "final_answer":
                readiness = self._assess_answer_readiness(
                    tool_observations, medical_risk, question_plan
                )
                if not bool(readiness.get("ready")):
                    self._mark_supplement_tasks(question_plan, readiness)
                    processing_steps.append(
                        "主控请求终答，但证据覆盖未达标或仍有冲突，继续检索决策"
                    )
                    continue
                accepted_readiness = readiness
                accepted_answer_context = self._build_answer_generation_context(
                    question_plan=question_plan,
                    tool_observations=tool_observations,
                    conflict_rows=readiness.get("conflict_details"),
                )
                final_answer, answer_tokens = await self._stream_final_answer(
                    question=safe_question,
                    tool_observations=tool_observations,
                    answer_context=accepted_answer_context,
                    image_data=str(image_data or "").strip(),
                    file_id=str(file_id or "").strip(),
                    progress_callback=progress_callback,
                )
                merge_token_usage(token_usage, answer_tokens)
                answer_validation = validate_answer_generation(
                    final_answer, accepted_answer_context
                )
                if not answer_validation.valid:
                    final_answer = ""
                    stop_reason = "answer_validation_failed"
                    processing_steps.append(
                        "终答未通过 must-cover、引用、适用条件或冲突后置校验"
                    )
                    break
                confidence_breakdown = assess_evidence_confidence(
                    sufficiency=readiness.get("evidence_sufficiency"),
                    answer_context=accepted_answer_context,
                    answer_validation=answer_validation,
                )
                confidence = confidence_breakdown.level
                stop_reason = "answer_ready"
                break

            if action_name == "call_history_read":
                retrieval_round += 1
                if progress_callback is not None:
                    progress_callback(
                        {
                            "event": "history_read",
                            "detail": "读取历史 Evidence",
                            "tool_name": "history_read",
                        }
                    )
                if self._mcp_gateway is None:
                    from app.a2a.tool_gateway import MCPToolGateway

                    self._mcp_gateway = MCPToolGateway(agent_type="orchestrator")
                history_id = _sanitize_text(action.get("history_id"), 64)
                history_question = _sanitize_text(action.get("user_question"), 300)
                history_result = await self._mcp_gateway.invoke_tool(
                    "history_read",
                    {
                        "session_id": safe_session_id,
                        "history_id": history_id,
                        "user_question": history_question,
                    },
                )
                merge_token_usage(
                    token_usage,
                    _coerce_dict(history_result.get("meta")).get("token_usage"),
                )
                history_payload = _coerce_dict(history_result.get("result"))
                history_assessments, verifier_tokens = await self._verify_task_evidence(
                    question_plan=question_plan,
                    task_id=_sanitize_text(action.get("task_id"), 64),
                    must_cover_id=_sanitize_text(action.get("must_cover_id"), 64),
                    evidence_rows=history_payload.get("evidence"),
                    existing_rows=history_payload.get("claim_assessments"),
                )
                history_payload["claim_assessments"] = [
                    item.model_dump() for item in history_assessments
                ]
                history_result["result"] = history_payload
                merge_token_usage(token_usage, verifier_tokens)
                self._update_question_plan_from_history(
                    question_plan, action, history_result
                )
                tool_observations.append(
                    self._build_history_observation(action, history_result)
                )
                (
                    previous_progress_state,
                    no_progress_rounds,
                    no_progress_reasons,
                ) = self._advance_retrieval_progress(
                    tool_observations=tool_observations,
                    question_plan=question_plan,
                    previous_state=previous_progress_state,
                    no_progress_rounds=no_progress_rounds,
                    no_progress_reasons=no_progress_reasons,
                    attempted_must_cover_ids={
                        _sanitize_text(action.get("must_cover_id"), 64)
                    },
                )
                if self._all_unresolved_items_stalled(
                    question_plan, no_progress_rounds
                ):
                    stop_reason = "no_retrieval_progress"
                    processing_steps.append(
                        "所有未覆盖 must-cover 连续未提升支持状态、来源独立性、适用条件或冲突状态，提前停止"
                    )
                    break
                continue

            if action_name in {"call_kg", "call_rag"}:
                tasks = self._build_ready_subtasks(
                    action=action,
                    max_tool_calls=safe_tool_calls,
                    question_plan=question_plan,
                )
                retrieval_round += len(tasks)
                target_names = "/".join(task.target_agent for task in tasks)
                processing_steps.append(
                    f"执行计划任务：{target_names}；并行数={len(tasks)}"
                )
                if progress_callback is not None:
                    progress_callback(
                        {
                            "event": "subagent_call",
                            "detail": f"调用 {target_names} 子agent",
                            "tool_name": "parallel_retrieval",
                        }
                    )
                results = await self._execute_subtasks(tasks)
                for task, result in zip(tasks, results):
                    claim_assessments, verifier_tokens = (
                        await self._verify_task_evidence(
                            question_plan=question_plan,
                            task_id=task.task_id,
                            must_cover_id=task.must_cover_id,
                            evidence_rows=result.evidence,
                            existing_rows=result.claim_assessments,
                        )
                    )
                    result.claim_assessments = claim_assessments
                    self._update_question_plan(question_plan, task, result)
                    merge_token_usage(token_usage, result.token_usage)
                    merge_token_usage(token_usage, verifier_tokens)
                    result_action = {
                        **action,
                        "action": f"call_{task.target_agent}",
                        "task_id": task.task_id,
                        "question": task.question,
                        "must_cover_id": task.must_cover_id,
                    }
                    tool_observations.append(
                        self._build_subagent_observation(result, result_action)
                    )
                (
                    previous_progress_state,
                    no_progress_rounds,
                    no_progress_reasons,
                ) = self._advance_retrieval_progress(
                    tool_observations=tool_observations,
                    question_plan=question_plan,
                    previous_state=previous_progress_state,
                    no_progress_rounds=no_progress_rounds,
                    no_progress_reasons=no_progress_reasons,
                    attempted_must_cover_ids={task.must_cover_id for task in tasks},
                )
                if self._all_unresolved_items_stalled(
                    question_plan, no_progress_rounds
                ):
                    stop_reason = "no_retrieval_progress"
                    processing_steps.append(
                        "所有未覆盖 must-cover 连续未提升支持状态、来源独立性、适用条件或冲突状态，提前停止"
                    )
                    break
                continue

            processing_steps.append("主控返回了未知动作，停止生成答案")
            stop_reason = "unknown_action"
            break

        if not final_answer:
            readiness = self._assess_answer_readiness(
                tool_observations, medical_risk, question_plan
            )
            if not stop_reason:
                stop_reason = "max_turns_exhausted"
            insufficient_result = self._attach_question_plan(
                self._attach_medical_risk_telemetry(
                    self._build_insufficient_evidence_result(
                        tool_observations=tool_observations,
                        processing_steps=processing_steps,
                        retrieval_round=retrieval_round,
                        max_turns=safe_max_turns,
                        token_usage=token_usage,
                        trace_id=safe_trace_id,
                        compression_state=compression_state,
                        readiness=readiness,
                    ),
                    medical_risk,
                ),
                question_plan,
            )
            insufficient_result = self._attach_evidence_bundle(
                insufficient_result,
                question=safe_question,
                question_plan=question_plan,
                tool_observations=tool_observations,
                sufficiency=readiness.get("evidence_sufficiency"),
            )
            result_telemetry = _coerce_dict(insufficient_result.get("telemetry"))
            result_telemetry["stop_reason"] = stop_reason
            result_telemetry.update(
                self._build_progress_telemetry(
                    previous_progress_state,
                    no_progress_rounds,
                    no_progress_reasons,
                )
            )
            if answer_validation is not None:
                result_telemetry["answer_validation"] = answer_validation.model_dump()
            insufficient_result["telemetry"] = result_telemetry
            return insufficient_result

        evidence_summary = self._build_evidence_summary(tool_observations)
        evidence_summary["evidence_sufficiency"] = (
            accepted_readiness.get("evidence_sufficiency")
            if accepted_readiness is not None
            else None
        )
        response_result: dict[str, Any] = {
            "output": final_answer,
            "output_type": "orchestrator_final",
            "confidence": confidence,
            "confidence_breakdown": confidence_breakdown.model_dump(),
            "tool_observations": tool_observations,
            "reflection_events": [],
            "processing_steps": processing_steps,
            "telemetry": {
                "controller_mode": "explicit_memory_with_subagents",
                "retrieval_round": retrieval_round,
                "max_turns": safe_max_turns,
                "trace_id": safe_trace_id,
                **token_usage,
                "final_source": "controller",
                "stop_reason": stop_reason,
                **self._build_progress_telemetry(
                    previous_progress_state,
                    no_progress_rounds,
                    no_progress_reasons,
                ),
                **self._build_budget_telemetry(compression_state),
            },
            "evidence_summary": evidence_summary,
            "answer_context": (
                accepted_answer_context.model_dump()
                if accepted_answer_context is not None
                else None
            ),
            "answer_validation": (
                answer_validation.model_dump()
                if answer_validation is not None
                else None
            ),
        }
        return self._attach_evidence_bundle(
            self._attach_question_plan(
                self._attach_medical_risk_telemetry(response_result, medical_risk),
                question_plan,
            ),
            question=safe_question,
            question_plan=question_plan,
            tool_observations=tool_observations,
            sufficiency=(
                accepted_readiness.get("evidence_sufficiency")
                if accepted_readiness is not None
                else None
            ),
        )
