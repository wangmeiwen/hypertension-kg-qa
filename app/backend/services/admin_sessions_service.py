"""管理员会话管理服务。"""

from __future__ import annotations

from pathlib import Path
import json
import logging
import shutil
from typing import Any, Awaitable, Callable

from app.backend.repositories.session_manager import MessageManager, SessionManager
from app.backend.services.session_log_repository import read_request_records, safe_log_id
from app.core.concurrency import concurrency_guard
from app.core.config import Config

logger = logging.getLogger(__name__)


class AdminSessionsService:
    """封装管理员会话与记忆管理相关业务。"""

    def __init__(
        self,
        await_call: Callable[..., Awaitable[Any]],
        get_session_memory_service: Callable[[], Any],
    ) -> None:
        """初始化实例并注入运行依赖。

        Args:
            await_call: 兼容同步/异步仓储和服务调用的执行器。
            get_session_memory_service: 延迟获取会话记忆服务的回调。

        Returns:
            None: 无返回值。
        """
        self._await_call = await_call
        self._get_session_memory_service = get_session_memory_service
        self._config = Config()

    @staticmethod
    def _project_root() -> Path:
        """定位项目根目录。

        Args:
            无。

        Returns:
            Path: 当前仓库根目录路径。
        """
        return Path(__file__).resolve().parents[3]

    @staticmethod
    def _safe_log_id(raw: str, fallback: str = "unknown") -> str:
        """将会话标识转换为可安全用于日志目录名的文本。

        Args:
            raw: 原始会话或请求标识。
            fallback: 清洗后为空时使用的兜底文本。

        Returns:
            str: 仅包含字母、数字、短横线和下划线的目录名片段。
        """
        return safe_log_id(raw, fallback)

    @staticmethod
    def _is_relative_to(child: Path, parent: Path) -> bool:
        """判断 child 是否在 parent 目录内，兼容较旧 Python 版本。"""
        try:
            child.relative_to(parent)
            return True
        except ValueError as exc:
            logger.debug(
                "admin_sessions_relative_path_check_failed: child=%s parent=%s error_type=%s error=%s",
                str(child),
                str(parent),
                type(exc).__name__,
                exc,
            )
            return False

    async def _clear_session_memory(self, session_id: str) -> tuple[bool, str]:
        """清理会话记忆；失败不阻断消息/会话删除。"""
        try:
            await self._await_call(self._get_session_memory_service().clear_memory, session_id)
            return True, ""
        except Exception as exc:  # pragma: no cover - error type is surfaced to API response
            logger.exception(
                "clear_session_memory_failed: session_id=%s error_type=%s error=%s",
                session_id,
                type(exc).__name__,
                exc,
            )
            return False, type(exc).__name__

    def _delete_session_log_artifacts(self, session_id: str) -> tuple[bool, str]:
        """删除单个会话的日志 JSON/JSONL 目录，限制在 logs/sessions 内。"""
        try:
            with concurrency_guard.sync_keyed_lock(f"session-artifacts:{self._safe_log_id(session_id)}"):
                logs_root = (self._project_root() / "logs" / "sessions").resolve()
                target_dir = (logs_root / self._safe_log_id(session_id)).resolve()
                if target_dir == logs_root or not self._is_relative_to(target_dir, logs_root):
                    return False, "unsafe_log_path"
                if not target_dir.exists():
                    return True, ""
                shutil.rmtree(target_dir)
                return True, ""
        except Exception as exc:  # pragma: no cover - filesystem failures depend on OS state
            logger.exception(
                "delete_session_log_artifacts_failed: session_id=%s error_type=%s error=%s",
                session_id,
                type(exc).__name__,
                exc,
            )
            return False, type(exc).__name__

    @staticmethod
    def _normalize_controller_mode(value: Any) -> str:
        """规范化主控模式标识，兜底为显式记忆主控。"""
        token = str(value or "").strip()
        if token in {"explicit_memory_with_history_read", "explicit_memory_with_subagents"}:
            return "explicit_memory_with_subagents"
        return token or "explicit_memory_with_subagents"

    @staticmethod
    def _controller_mode_label(value: Any) -> str:
        """将主控模式标识转换为可展示中文标签。"""
        token = AdminSessionsService._normalize_controller_mode(value)
        if token == "explicit_memory_with_subagents":
            return "显式记忆子代理主控"
        return token or "未知"

    def _read_session_turn_records(self, session_id: str) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
        """读取指定会话的 turn 观测记录和记忆更新记录。

        Args:
            session_id: 会话标识。

        Returns:
            tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]: turn 记录列表和按 turn_id 索引的记忆更新记录。
        """
        turns_dir = self._project_root() / "logs" / "sessions" / self._safe_log_id(session_id) / "turns"
        turn_rows: list[dict[str, Any]] = []
        memory_updates: dict[str, dict[str, Any]] = {}
        if not turns_dir.exists():
            return turn_rows, memory_updates
        for file_path in sorted(turns_dir.glob("*.jsonl")):
            try:
                for line in file_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                    if not line.strip():
                        continue
                    payload = json.loads(line)
                    if not isinstance(payload, dict):
                        continue
                    record_type = str(payload.get("record_type") or "")
                    if record_type == "turn_observability":
                        turn_rows.append(payload)
                    elif record_type == "turn_memory_update":
                        turn_id = str(payload.get("turn_id") or "")
                        if turn_id:
                            memory_updates[turn_id] = payload
            except (OSError, ValueError, TypeError) as exc:
                logger.warning(
                    "read_session_turn_records_skip_file: file=%s error_type=%s error=%s",
                    str(file_path),
                    type(exc).__name__,
                    exc,
                )
                continue
        turn_rows.sort(key=lambda item: str(item.get("started_at") or ""))
        return turn_rows, memory_updates

    @classmethod
    def _derive_llm_metrics_from_agent_lanes(cls, turn: dict[str, Any]) -> dict[str, int]:
        """从内嵌 agent_lanes telemetry 中兜底提取 LLM 指标。"""
        agent_lanes = cls._as_dict(turn.get("agent_lanes"))
        total_tokens = 0
        llm_calls = 0
        for cards in agent_lanes.values():
            if not isinstance(cards, list):
                continue
            for card in cards:
                row = card if isinstance(card, dict) else {}
                parsed = cls._extract_json_object(row.get("full_decision"))
                total_tokens = max(total_tokens, cls._to_int_metric(parsed.get("total_tokens")))
                llm_calls = max(llm_calls, cls._to_int_metric(parsed.get("llm_calls")))
        if llm_calls <= 0 and total_tokens > 0:
            llm_calls = 1
        return {"llm_calls": llm_calls, "total_tokens": total_tokens}

    @staticmethod
    def _to_int_metric(value: Any) -> int:
        """将日志指标转换为非负整数。"""
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return max(0, value)
        if isinstance(value, float):
            return max(0, int(value))
        if isinstance(value, str):
            try:
                return max(0, int(float(value)))
            except ValueError:
                return 0
        return 0

    @classmethod
    def _merge_turn_memory_update(cls, turn: dict[str, Any], memory_update: dict[str, Any]) -> dict[str, Any]:
        """将 turn 观测记录与同轮记忆更新记录合并。

        Args:
            turn: 原始 turn 观测记录。
            memory_update: 同一 turn 的记忆更新记录。

        Returns:
            dict[str, Any]: 合并 token、错误类型和记忆快照后的 turn 记录。
        """
        merged_turn = dict(turn)
        fallback_metrics = cls._derive_llm_metrics_from_agent_lanes(merged_turn)
        merged_turn["llm_calls"] = max(
            cls._to_int_metric(merged_turn.get("llm_calls")),
            cls._to_int_metric(fallback_metrics.get("llm_calls")),
        )
        merged_turn["total_tokens"] = max(
            cls._to_int_metric(merged_turn.get("total_tokens")),
            cls._to_int_metric(fallback_metrics.get("total_tokens")),
        )
        if not memory_update:
            return merged_turn
        memory_compression = cls._as_list(turn.get("memory_compression"))
        snapshot = cls._as_dict(memory_update.get("snapshot"))
        updated_at = str(memory_update.get("updated_at") or "")
        merged_turn["llm_calls"] = max(cls._to_int_metric(merged_turn.get("llm_calls")), cls._to_int_metric(memory_update.get("llm_calls")))
        merged_turn["total_tokens"] = max(cls._to_int_metric(merged_turn.get("total_tokens")), cls._to_int_metric(memory_update.get("total_tokens")))
        update_errors = cls._as_dict(memory_update.get("error_types"))
        if update_errors:
            base_errors = cls._as_dict(merged_turn.get("error_types"))
            merged_turn["error_types"] = {
                **base_errors,
                **{str(key): int(value or 0) for key, value in update_errors.items()},
            }
        patched: list[dict[str, Any]] = []
        for item in memory_compression:
            row = item if isinstance(item, dict) else {}
            if str(row.get("stage_name") or "") not in {"压缩后记忆", "写回后记忆"}:
                patched.append(row)
                continue
            qa_memory = cls._as_dict(snapshot.get("qa_memory"))
            retrieval_memory = cls._as_dict(snapshot.get("retrieval_memory"))
            qa_pairs = cls._as_list(qa_memory.get("qa_pairs"))
            retrieval_questions = cls._as_list(retrieval_memory.get("questions"))
            latest_qa = cls._as_dict(qa_pairs[-1]) if qa_pairs else {}
            patched.append(
                {
                    **row,
                    "event_time": updated_at or str(row.get("event_time") or ""),
                    "content_lines": [
                        f"QA 条数：{len(qa_pairs)}",
                        f"最近问题：{str(latest_qa.get('user_question') or '无')}",
                        f"历史检索问题数：{len(retrieval_questions)}",
                    ],
                }
            )
        merged_turn["memory_compression"] = patched
        return merged_turn

    @staticmethod
    def _as_dict(value: Any) -> dict[str, Any]:
        """将外部日志值收敛为字典。"""
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _as_list(value: Any) -> list[Any]:
        """将外部日志值收敛为列表。"""
        return value if isinstance(value, list) else []

    @staticmethod
    def _sanitize_text(value: Any, limit: int = 240) -> str:
        """清洗并截断用于管理端展示的文本。

        Args:
            value: 待处理输入值。
            limit: 最多保留的字符数量。

        Returns:
            str: 去除换行并按长度裁剪后的文本。
        """
        text = str(value or "").replace("\n", " ").strip()
        if not text:
            return ""
        if len(text) <= limit:
            return text
        return f"{text[:limit]}..."

    @classmethod
    def _join_lines(cls, rows: Any, limit: int = 4, item_limit: int = 100) -> str:
        """将列表中的前若干项格式化为单行展示文本。

        Args:
            rows: 输入数据列表。
            limit: 最多取用的列表项数量。
            item_limit: 单项文本最多保留的字符数量。

        Returns:
            str: 用中文分号连接的展示文本，空列表返回“无”。
        """
        if not isinstance(rows, list):
            return "无"
        values: list[str] = []
        for item in rows:
            text = cls._sanitize_text(item, item_limit)
            if not text:
                continue
            values.append(text)
            if len(values) >= limit:
                break
        return "；".join(values) if values else "无"

    @classmethod
    def _humanize_payload(cls, value: Any) -> str:
        """将任意日志 payload 转换为短文本。

        Args:
            value: 待处理输入值。

        Returns:
            str: 适合在管理端详情中展示的文本。
        """
        if value is None:
            return "无"
        if isinstance(value, str):
            return cls._sanitize_text(value, 1200) or "无"
        if isinstance(value, list):
            return cls._join_lines(value, limit=6, item_limit=180)
        if isinstance(value, dict):
            rows: list[str] = []
            for key, item in value.items():
                item_text = cls._sanitize_text(item, 180)
                if not item_text:
                    continue
                rows.append(f"{key}：{item_text}")
                if len(rows) >= 6:
                    break
            return "；".join(rows) if rows else "无"
        return cls._sanitize_text(value, 1200) or "无"

    @staticmethod
    def _lane_order(name: str) -> int:
        """返回泳道排序权重。

        Args:
            name: 泳道名称。

        Returns:
            int: 排序权重，未知泳道排在最后。
        """
        mapping = {
            "主控编排": 0,
            "图谱代理": 1,
            "检索代理": 2,
            "外部资源": 3,
        }
        return mapping.get(str(name or ""), 99)

    @staticmethod
    def _safe_event_id(turn_id: str, prefix: str, index: int) -> str:
        """生成管理端泳道事件标识。

        Args:
            turn_id: 对象标识。
            prefix: 事件类型前缀。
            index: 当前事件在同类事件中的序号。

        Returns:
            str: 由 turn、前缀和序号拼出的稳定事件标识。
        """
        return f"{turn_id}:{prefix}:{index}"

    @classmethod
    def _safe_json_text(cls, value: Any, limit: int = 1600) -> str:
        """将对象安全序列化为展示文本，无法 JSON 编码时退回普通文本。"""
        try:
            return cls._sanitize_text(json.dumps(value, ensure_ascii=False), limit) or "无"
        except (TypeError, ValueError):
            return cls._sanitize_text(value, limit) or "无"

    @staticmethod
    def _allowed_flow_lane(name: Any) -> str:
        """将泳道名收敛到当前界面允许的 3 个泳道。"""
        text = str(name or "").strip()
        if text in {"图谱代理", "检索代理"}:
            return text
        if text in {"", "记忆压缩"}:
            return "主控编排"
        return "主控编排"

    @classmethod
    def _lane_from_actor_role(cls, actor_role: Any) -> str:
        """根据显式 actor_role 映射泳道。"""
        token = str(actor_role or "").strip().lower()
        mapping = {
            "controller": "主控编排",
            "graph": "图谱代理",
            "retrieval": "检索代理",
        }
        return mapping.get(token, "")

    @classmethod
    def _resolve_llm_lane(cls, row: dict[str, Any]) -> tuple[str, str, str]:
        """解析 LLM 日志的泳道来源，优先使用显式 actor_role。"""
        actor_role = str(row.get("actor_role") or "").strip().lower()
        explicit_lane = cls._lane_from_actor_role(actor_role)
        if explicit_lane:
            return explicit_lane, actor_role, "explicit_actor_role"

        actor_name = str(row.get("actor_name") or "").strip().lower()
        if "controller" in actor_name or "orchestrator" in actor_name:
            return "主控编排", actor_role, "actor_name_fallback"
        if "graph" in actor_name:
            return "图谱代理", actor_role, "actor_name_fallback"
        if "retrieval" in actor_name:
            return "检索代理", actor_role, "actor_name_fallback"

        messages_text = str(row.get("messages_compact") or "")
        if "会话记忆更新器" in messages_text:
            return "", actor_role, "ignored_memory_writer"
        if "你是高血压问答系统的主控agent" in messages_text or "主控agent" in messages_text:
            return "主控编排", actor_role, "legacy_controller_prompt"
        if "Hypertension Graph Agent" in messages_text or "Graph Agent" in messages_text:
            return "图谱代理", actor_role, "legacy_graph_prompt"
        if "Hypertension Retrieval Agent" in messages_text or "Retrieval Agent" in messages_text:
            return "检索代理", actor_role, "legacy_retrieval_prompt"

        response_text = str(row.get("response_compact") or "")
        if any(token in response_text for token in ('"action":"final_answer"', '"action": "final_answer"', '"action":"call_kg"', '"action": "call_kg"', '"action":"call_rag"', '"action": "call_rag"', '"action":"call_history_read"', '"action": "call_history_read"')):
            return "主控编排", actor_role, "legacy_controller_action"

        return "主控编排", actor_role, "default_controller_fallback"

    @classmethod
    def _infer_tool_lane(cls, observation: dict[str, Any]) -> str:
        """根据工具观测信息推断工具所属泳道。"""
        agent = str(observation.get("agent") or "").strip().lower()
        tool_name = str(observation.get("tool_name") or observation.get("requested_tool_name") or "").strip().lower()
        action = str(observation.get("action") or "").strip().lower()
        if agent == "kg" or tool_name.startswith("kg_"):
            return "图谱代理"
        if agent == "rag" or "rag" in tool_name:
            return "检索代理"
        if action == "call_history_read" or "history_read" in tool_name or agent == "history":
            return "主控编排"
        return "主控编排"

    @classmethod
    def _build_tool_only_event(
        cls,
        *,
        turn_id: str,
        turn: dict[str, Any],
        lane_name: str,
        observation: dict[str, Any],
        sequence_index: int,
    ) -> dict[str, Any]:
        """当某个泳道没有独立 LLM 事件时，为工具调用补一个兜底节点。

        Args:
            turn_id: turn 标识。
            turn: 当前 turn 观测记录。
            lane_name: 工具调用归属泳道。
            observation: 工具观测记录。
            sequence_index: 兜底节点序号。

        Returns:
            dict[str, Any]: 可直接加入泳道图的工具兜底事件。
        """
        tool_log = cls._as_dict(observation.get("_tool_log"))
        result_obj = cls._as_dict(observation.get("result"))
        event_time = str(tool_log.get("ts") or turn.get("finished_at") or turn.get("started_at") or "")
        summary = cls._sanitize_text(cls._summarize_tool_observation(observation), 180) or "该次工具调用暂无输出"
        return {
            "event_id": cls._safe_event_id(turn_id, "tool_action", sequence_index),
            "event_time": event_time,
            "lane": lane_name,
            "stage": "工具行动",
            "title": f"{lane_name}工具行动",
            "summary": summary,
            "actor": lane_name,
            "kind": "tool_action",
            "status": "失败" if cls._sanitize_text(result_obj.get("error"), 120) else "完成",
            "detail": {
                "model_input_prompt": "该代理本次未记录独立模型输入",
                "model_output_text": "该代理本次未记录独立模型输出",
                "tool_call_inputs": "本次未调用工具",
                "tool_call_results": "本次未调用工具",
                "tool_call_count": 0,
                "merge_strategy": "tool_only",
            },
            "source_actor_role": str(observation.get("agent") or "").strip().lower(),
            "lane_inference_reason": "tool_only_fallback",
            "_tool_entries": [],
        }

    @classmethod
    def _summarize_tool_observation(cls, observation: dict[str, Any]) -> str:
        """提取工具观测摘要，用于泳道节点标题。"""
        result_obj = cls._as_dict(observation.get("result"))
        payload = cls._as_dict(result_obj.get("result"))
        return (
            cls._sanitize_text(payload.get("summary"), 240)
            or cls._sanitize_text(payload.get("message"), 240)
            or cls._sanitize_text(result_obj.get("error"), 240)
            or "本次工具调用暂无输出"
        )

    @classmethod
    def _decode_logged_text(cls, value: Any) -> str:
        """尽量还原日志里被 JSON 编码过的文本。"""
        text = str(value or "").strip()
        if not text:
            return ""
        try:
            decoded = json.loads(text)
            if isinstance(decoded, str):
                return decoded
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
        return text

    @classmethod
    def _extract_json_object(cls, value: Any) -> dict[str, Any]:
        """从日志文本中提取首个 JSON 对象。"""
        raw = cls._decode_logged_text(value).strip()
        if not raw:
            return {}
        if raw.startswith("```"):
            lines = raw.splitlines()
            if lines:
                lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            raw = "\n".join(lines).strip()
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

    @classmethod
    def _extract_controller_action(cls, response_compact: Any) -> str:
        """从主控模型输出中提取 action。"""
        parsed = cls._extract_json_object(response_compact)
        return str(parsed.get("action") or "").strip()

    @classmethod
    def _expected_tool_lane_for_action(cls, action_name: str) -> str:
        """将主控动作映射为其后续工具结果应落入的泳道。"""
        action = str(action_name or "").strip().lower()
        if action == "call_rag":
            return "检索代理"
        if action == "call_kg":
            return "图谱代理"
        if action == "call_history_read":
            return "主控编排"
        return ""

    @classmethod
    def _read_session_llm_records(cls, session_id: str, request_id: str) -> list[dict[str, Any]]:
        """读取指定请求的 LLM 日志。"""
        return read_request_records(
            project_root=cls._project_root(),
            session_id=session_id,
            request_id=request_id,
            category="llm",
            logger=logger,
            warning_event="admin_sessions_read_llm_records_skip_file",
        )

    @classmethod
    def _read_session_tool_records(cls, session_id: str, request_id: str) -> list[dict[str, Any]]:
        """读取指定请求的工具日志。"""
        return read_request_records(
            project_root=cls._project_root(),
            session_id=session_id,
            request_id=request_id,
            category="tools",
            logger=logger,
            warning_event="admin_sessions_read_tool_records_skip_file",
        )

    @classmethod
    def _bind_tool_logs(
        cls,
        tool_observations: list[dict[str, Any]],
        tool_rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """将工具观测和工具日志按顺序绑定，补齐调用输入。"""
        available_rows = [row for row in tool_rows if isinstance(row, dict)]
        used_indexes: set[int] = set()
        bound_rows: list[dict[str, Any]] = []
        for observation in tool_observations:
            row = observation if isinstance(observation, dict) else {}
            tool_name = str(row.get("tool_name") or row.get("requested_tool_name") or "").strip()
            matched_index: int | None = None
            if tool_name:
                for idx, tool_row in enumerate(available_rows):
                    if idx in used_indexes:
                        continue
                    if str(tool_row.get("tool_name") or "").strip() == tool_name:
                        matched_index = idx
                        break
            if matched_index is None:
                for idx, tool_row in enumerate(available_rows):
                    if idx in used_indexes:
                        continue
                    matched_index = idx
                    break
            matched_row = available_rows[matched_index] if matched_index is not None else {}
            if matched_index is not None:
                used_indexes.add(matched_index)
            bound_rows.append({**row, "_tool_log": matched_row if isinstance(matched_row, dict) else {}})
        return bound_rows

    @classmethod
    def _format_tool_input(cls, tool_row: dict[str, Any], observation: dict[str, Any], index: int) -> str:
        """格式化工具调用输入文本。"""
        tool_name = str(
            observation.get("tool_name")
            or observation.get("requested_tool_name")
            or tool_row.get("tool_name")
            or "unknown_tool"
        ).strip()
        question = str(observation.get("subquery") or observation.get("question") or "").strip()
        arguments = cls._as_dict(tool_row.get("arguments"))
        rows = [f"{index}. 工具：{tool_name}"]
        if question:
            rows.append(f"子问题：{cls._sanitize_text(question, 280)}")
        if arguments:
            rows.append(f"参数：{cls._safe_json_text(arguments, 1200)}")
        return "\n".join(rows)

    @classmethod
    def _format_tool_result(cls, observation: dict[str, Any], index: int) -> str:
        """格式化工具调用结果文本。"""
        result_obj = cls._as_dict(observation.get("result"))
        payload = result_obj.get("result")
        payload_dict = payload if isinstance(payload, dict) else {}
        summary = cls._sanitize_text(
            result_obj.get("summary")
            or result_obj.get("message")
            or payload_dict.get("summary")
            or payload_dict.get("message")
            or payload_dict.get("answer")
            or "",
            320,
        )
        error_text = cls._sanitize_text(result_obj.get("error") or "", 320)
        query_records = cls._as_list(observation.get("query_records"))
        lines = [f"{index}. 状态：{'成功' if bool(result_obj.get('ok', True)) else '失败'}"]
        if summary:
            lines.append(f"摘要：{summary}")
        if error_text:
            lines.append(f"错误：{error_text}")
        for query_index, item in enumerate(query_records[:3], start=1):
            if not isinstance(item, dict):
                continue
            query = cls._sanitize_text(item.get("query"), 200)
            result_content = cls._sanitize_text(item.get("result_content"), 360)
            if query:
                lines.append(f"检索{query_index} Query：{query}")
            if result_content:
                lines.append(f"检索{query_index} 结果：{result_content}")
        if len(lines) == 1:
            lines.append("本次未返回可展示的结果摘要")
        return "\n".join(lines)

    @classmethod
    def _build_agent_lane_card_event(
        cls,
        *,
        turn_id: str,
        turn: dict[str, Any],
        lane_name: str,
        card: dict[str, Any],
        sequence_index: int,
    ) -> dict[str, Any]:
        """从 turn.agent_lanes 的卡片兜底生成泳道事件。"""
        title = cls._sanitize_text(card.get("title"), 80) or f"{lane_name}行动"
        event_time = str(card.get("occurred_at") or card.get("started_at") or card.get("finished_at") or turn.get("finished_at") or turn.get("started_at") or "")
        full_prompt = str(card.get("full_prompt") or card.get("prompt_preview") or "无")
        full_decision = str(card.get("full_decision") or card.get("decision_preview") or "无")
        is_tool_card = "工具" in title or lane_name in {"图谱代理", "检索代理"}
        return {
            "event_id": cls._safe_event_id(turn_id, "agent_lane", sequence_index),
            "event_time": event_time,
            "lane": lane_name,
            "stage": "代理行动" if is_tool_card else "模型行动",
            "title": title,
            "summary": cls._sanitize_text(card.get("decision_preview") or card.get("prompt_preview") or "该代理暂无摘要", 180),
            "actor": lane_name,
            "kind": "agent_lane_card",
            "status": cls._sanitize_text(card.get("status"), 40) or "完成",
            "detail": {
                "model_input_prompt": full_prompt if not is_tool_card else "该代理本次未记录独立模型输入",
                "model_output_text": full_decision if not is_tool_card else "该代理本次未记录独立模型输出",
                "tool_call_inputs": full_prompt if is_tool_card else "本次未调用工具",
                "tool_call_results": full_decision if is_tool_card else "本次未调用工具",
                "tool_call_count": 1 if is_tool_card else 0,
                "merge_strategy": "agent_lanes_fallback",
            },
            "sequence_order": sequence_index * 10,
        }

    @classmethod
    def _build_tool_observation_fallback_events(cls, *, turn_id: str, turn: dict[str, Any], start_index: int) -> list[dict[str, Any]]:
        """没有 agent_lanes 卡片时，从 tool_observations 兜底生成事件。"""
        tool_observations = cls._as_list(turn.get("tool_observations"))
        events: list[dict[str, Any]] = []
        for offset, observation in enumerate(tool_observations, start=start_index):
            row = observation if isinstance(observation, dict) else {}
            lane_name = cls._allowed_flow_lane(cls._infer_tool_lane(row))
            result_obj = cls._as_dict(row.get("result"))
            events.append(
                {
                    "event_id": cls._safe_event_id(turn_id, "tool_fallback", offset),
                    "event_time": str(turn.get("finished_at") or turn.get("started_at") or ""),
                    "lane": lane_name,
                    "stage": "工具行动",
                    "title": f"{lane_name}工具行动",
                    "summary": cls._sanitize_text(cls._summarize_tool_observation(row), 180) or "该次工具调用暂无输出",
                    "actor": lane_name,
                    "kind": "tool_fallback",
                    "status": "失败" if cls._sanitize_text(result_obj.get("error"), 120) else "完成",
                    "detail": {
                        "model_input_prompt": "该代理本次未记录独立模型输入",
                        "model_output_text": "该代理本次未记录独立模型输出",
                        "tool_call_inputs": cls._format_tool_input({}, row, 1),
                        "tool_call_results": cls._format_tool_result(row, 1),
                        "tool_call_count": 1,
                        "merge_strategy": "tool_observations_fallback",
                    },
                    "sequence_order": offset * 10,
                }
            )
        return events

    @classmethod
    def _build_agent_lanes_fallback_events(cls, turn: dict[str, Any]) -> list[dict[str, Any]]:
        """当独立 LLM 日志缺失时，从 turn 内嵌 agent_lanes 构建泳道事件。"""
        turn_id = str(turn.get("turn_id") or "")
        agent_lanes = cls._as_dict(turn.get("agent_lanes"))
        events: list[dict[str, Any]] = []
        sequence_index = 0
        for lane_key in ("主控编排", "图谱代理", "检索代理"):
            cards = cls._as_list(agent_lanes.get(lane_key))
            for card in cards:
                if not isinstance(card, dict):
                    continue
                sequence_index += 1
                events.append(
                    cls._build_agent_lane_card_event(
                        turn_id=turn_id,
                        turn=turn,
                        lane_name=cls._allowed_flow_lane(card.get("lane_name") or lane_key),
                        card=card,
                        sequence_index=sequence_index,
                    )
                )
        if not events:
            events.extend(cls._build_tool_observation_fallback_events(turn_id=turn_id, turn=turn, start_index=1))
        events.sort(
            key=lambda item: (
                int(item.get("sequence_order") or 0),
                str(item.get("event_time") or ""),
                cls._lane_order(str(item.get("lane") or "")),
                str(item.get("event_id") or ""),
            )
        )
        return events

    @classmethod
    def _build_flow_events(
        cls,
        *,
        session_id: str,
        request_id: str,
        turn: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """基于 LLM 日志和工具日志构建管理端泳道图事件。

        Args:
            session_id: 会话标识。
            request_id: 请求标识，用于筛选同一轮日志。
            turn: 当前 turn 观测记录。

        Returns:
            list[dict[str, Any]]: 按执行顺序排序的泳道图事件。
        """
        turn_id = str(turn.get("turn_id") or "")
        llm_rows = cls._read_session_llm_records(session_id, request_id)
        if not llm_rows:
            return cls._build_agent_lanes_fallback_events(turn)
        tool_observations = cls._as_list(turn.get("tool_observations"))
        tool_rows = cls._read_session_tool_records(session_id, request_id)
        bound_tool_rows = cls._bind_tool_logs(tool_observations, tool_rows)

        events: list[dict[str, Any]] = []
        lane_event_indexes: dict[str, list[int]] = {"主控编排": [], "图谱代理": [], "检索代理": []}
        pending_tool_anchor_indexes: dict[str, list[int]] = {"主控编排": [], "图谱代理": [], "检索代理": []}
        for index, row in enumerate(llm_rows):
            lane_name_raw, source_actor_role, lane_inference_reason = cls._resolve_llm_lane(
                row if isinstance(row, dict) else {}
            )
            if not lane_name_raw:
                continue
            lane_name = cls._allowed_flow_lane(lane_name_raw)
            response_text = str(row.get("response_compact") or row.get("error") or "").strip()
            controller_action = cls._extract_controller_action(row.get("response_compact"))
            event: dict[str, Any] = {
                "event_id": cls._safe_event_id(turn_id, "llm_action", index),
                "event_time": str(row.get("ts") or turn.get("finished_at") or turn.get("started_at") or ""),
                "lane": lane_name,
                "stage": "模型行动",
                "title": f"{lane_name}模型行动",
                "summary": cls._sanitize_text(response_text or "该次模型调用暂无输出", 180),
                "actor": lane_name,
                "kind": "llm_action",
                "status": "失败" if str(row.get("error") or "").strip() else "完成",
                "detail": {
                    "model_input_prompt": str(row.get("messages_compact") or "无"),
                    "model_output_text": response_text or "无",
                    "tool_call_inputs": "本次未调用工具",
                    "tool_call_results": "本次未调用工具",
                    "tool_call_count": 0,
                    "merge_strategy": "llm_only",
                },
                "source_actor_role": source_actor_role,
                "lane_inference_reason": lane_inference_reason,
                "sequence_order": (index + 1) * 10,
                "_controller_action": controller_action,
                "_tool_entries": [],
            }
            lane_event_indexes[lane_name].append(len(events))
            events.append(event)
            if lane_name == "主控编排":
                expected_lane = cls._expected_tool_lane_for_action(controller_action)
                if expected_lane:
                    pending_tool_anchor_indexes[expected_lane].append(len(events) - 1)

        tool_only_sequence = 0
        anchor_offsets: dict[int, int] = {}
        for observation in bound_tool_rows:
            lane_name = cls._allowed_flow_lane(cls._infer_tool_lane(observation))
            candidate_indexes = lane_event_indexes.get(lane_name) or []
            tool_log = cls._as_dict(observation.get("_tool_log"))
            tool_time = str(tool_log.get("ts") or "").strip()
            preferred_anchor_index = None
            pending_anchor_list = pending_tool_anchor_indexes.get(lane_name) or []
            if pending_anchor_list:
                preferred_anchor_index = pending_anchor_list.pop(0)
            matching_indexes = (
                [idx for idx in candidate_indexes if str(events[idx].get("event_time") or "") <= tool_time]
                if tool_time
                else []
            )
            is_history_observation = lane_name == "主控编排" and (
                str(observation.get("action") or "").strip().lower() == "call_history_read"
                or "history_read" in str(observation.get("tool_name") or "").strip().lower()
            )
            if preferred_anchor_index is not None and (
                not candidate_indexes or is_history_observation
            ):
                tool_only_sequence += 1
                events.append(
                    cls._build_tool_only_event(
                        turn_id=turn_id,
                        turn=turn,
                        lane_name=lane_name,
                        observation=observation,
                        sequence_index=tool_only_sequence,
                    )
                )
                target_index = len(events) - 1
                lane_event_indexes[lane_name].append(target_index)
                base_sequence = int(events[preferred_anchor_index].get("sequence_order") or 0)
                anchor_offsets[preferred_anchor_index] = int(anchor_offsets.get(preferred_anchor_index) or 0) + 1
                events[target_index]["sequence_order"] = base_sequence + anchor_offsets[preferred_anchor_index]
                merge_strategy = "tool_only"
            elif candidate_indexes:
                target_index = matching_indexes[-1] if matching_indexes else candidate_indexes[-1]
                merge_strategy = "timestamp_merge" if matching_indexes else "sequential_merge"
            else:
                tool_only_sequence += 1
                events.append(
                    cls._build_tool_only_event(
                        turn_id=turn_id,
                        turn=turn,
                        lane_name=lane_name,
                        observation=observation,
                        sequence_index=tool_only_sequence,
                    )
                )
                target_index = len(events) - 1
                lane_event_indexes[lane_name].append(target_index)
                fallback_base = int(events[-2].get("sequence_order") or 0) if len(events) >= 2 else 0
                events[target_index]["sequence_order"] = fallback_base + 1
                merge_strategy = "tool_only"
            events[target_index]["_tool_entries"].append({**observation, "_merge_strategy": merge_strategy})

        for event in events:
            tool_entries = event.pop("_tool_entries", [])
            if not isinstance(tool_entries, list):
                tool_entries = []
            tool_inputs = [
                cls._format_tool_input(
                    cls._as_dict(item.get("_tool_log")),
                    item if isinstance(item, dict) else {},
                    index,
                )
                for index, item in enumerate(tool_entries, start=1)
                if isinstance(item, dict)
            ]
            tool_results = [
                cls._format_tool_result(item if isinstance(item, dict) else {}, index)
                for index, item in enumerate(tool_entries, start=1)
                if isinstance(item, dict)
            ]
            tool_count = len(tool_entries)
            merge_strategy = "llm_only"
            if tool_entries:
                merge_strategy = (
                    "tool_only"
                    if all(str(item.get("_merge_strategy") or "") == "tool_only" for item in tool_entries)
                    else (
                        "timestamp_merge"
                        if all(str(item.get("_merge_strategy") or "") == "timestamp_merge" for item in tool_entries)
                        else "sequential_merge"
                    )
                )
                summary = cls._sanitize_text(
                    f"{event.get('summary') or '该次模型调用暂无输出'} | 工具调用 {tool_count} 次",
                    180,
                )
                event["summary"] = summary
            event_detail = cls._as_dict(event.get("detail"))
            event["detail"] = {
                "model_input_prompt": str(event_detail.get("model_input_prompt") or "无"),
                "model_output_text": str(event_detail.get("model_output_text") or "无"),
                "tool_call_inputs": "\n\n".join(tool_inputs) if tool_inputs else "本次未调用工具",
                "tool_call_results": "\n\n".join(tool_results) if tool_results else "本次未调用工具",
                "tool_call_count": tool_count,
                "merge_strategy": merge_strategy,
            }
            event.pop("_controller_action", None)

        events.sort(
            key=lambda item: (
                int(item.get("sequence_order") or 0),
                str(item.get("event_time") or ""),
                cls._lane_order(str(item.get("lane") or "")),
                str(item.get("event_id") or ""),
            )
        )
        return events

    async def admin_get_sessions(self, limit: int = 100, offset: int = 0) -> dict[str, Any]:
        """获取管理员会话列表。

        Args:
            limit: 返回数量上限。
            offset: 分页偏移量。

        Returns:
            dict[str, Any]: 会话列表和总数。
        """
        sessions = await self._await_call(SessionManager.get_all_sessions, limit, offset)
        total = await self._await_call(SessionManager.get_total_sessions_count)
        return {"ok": True, "data": sessions, "total": total}

    async def admin_get_session_messages(self, session_id: str) -> dict[str, Any]:
        """获取指定会话的基础信息和消息列表。

        Args:
            session_id: 会话标识。

        Returns:
            dict[str, Any]: 会话信息与消息列表，或会话不存在错误。
        """
        session = await self._await_call(SessionManager.get_session, session_id)
        if not session:
            return {"ok": False, "error": "会话不存在"}
        messages = await self._await_call(MessageManager.get_messages, session_id)
        return {"ok": True, "session": session, "messages": messages}

    async def admin_get_session_observability(self, session_id: str) -> dict[str, Any]:
        """获取指定会话的按轮次观测详情。

        Args:
            session_id: 会话标识。

        Returns:
            dict[str, Any]: 会话信息和标准化后的 turn 观测详情。
        """
        session = await self._await_call(SessionManager.get_session, session_id)
        if not session:
            return {"ok": False, "error": "会话不存在"}

        turns, memory_updates = self._read_session_turn_records(session_id)
        normalized_turns: list[dict[str, Any]] = []
        for turn in turns:
            memory_update = self._as_dict(
                memory_updates.get(str(turn.get("turn_id") or ""))
            )
            merged_turn = self._merge_turn_memory_update(turn, memory_update)
            orchestrator_decision = self._as_dict(
                merged_turn.get("orchestrator_decision")
            )
            normalized_controller_mode = self._normalize_controller_mode(orchestrator_decision.get("controller_mode"))
            normalized_decision = {
                **orchestrator_decision,
                "controller_mode": normalized_controller_mode,
                "controller_mode_label": self._controller_mode_label(normalized_controller_mode),
            }
            merged_for_events = {**merged_turn, "orchestrator_decision": normalized_decision}
            normalized_turns.append(
                {
                    "request_id": str(merged_turn.get("request_id") or ""),
                    "turn_id": str(merged_turn.get("turn_id") or ""),
                    "session_id": str(session_id or ""),
                    "user_message_id": int(merged_turn.get("user_message_id") or 0),
                    "assistant_message_id": int(merged_turn.get("assistant_message_id") or 0),
                    "question": str(merged_turn.get("question") or ""),
                    "answer_digest": str(merged_turn.get("answer_digest") or ""),
                    "started_at": str(merged_turn.get("started_at") or ""),
                    "finished_at": str(merged_turn.get("finished_at") or ""),
                    "status": str(merged_turn.get("status") or "done"),
                    "duration_ms": float(merged_turn.get("duration_ms") or 0.0),
                    "llm_calls": int(merged_turn.get("llm_calls") or 0),
                    "total_tokens": int(merged_turn.get("total_tokens") or 0),
                    "memory_before": merged_turn.get("memory_before") if isinstance(merged_turn.get("memory_before"), dict) else {},
                    "orchestrator_decision": normalized_decision,
                    "tool_observations": merged_turn.get("tool_observations") if isinstance(merged_turn.get("tool_observations"), list) else [],
                    "agent_lanes": merged_turn.get("agent_lanes") if isinstance(merged_turn.get("agent_lanes"), dict) else {},
                    "flow_events": self._build_flow_events(
                        session_id=str(session_id or ""),
                        request_id=str(merged_turn.get("request_id") or ""),
                        turn=merged_for_events,
                    ),
                    "memory_compression": merged_turn.get("memory_compression") if isinstance(merged_turn.get("memory_compression"), list) else [],
                    "timeline": merged_turn.get("timeline") if isinstance(merged_turn.get("timeline"), list) else [],
                }
            )
        return {"ok": True, "session": session, "turns": normalized_turns}

    async def admin_get_session_memory(self, session_id: str) -> dict[str, Any]:
        """获取指定会话记忆快照。

        Args:
            session_id: 会话标识。

        Returns:
            dict[str, Any]: 会话记忆快照或会话不存在错误。
        """
        session = await self._await_call(SessionManager.get_session, session_id)
        if not session:
            return {"ok": False, "error": "会话不存在"}
        snapshot = await self._await_call(self._get_session_memory_service().load_memory, session_id)
        return {"ok": True, "data": snapshot}

    async def admin_rebuild_session_memory(self, session_id: str) -> dict[str, Any]:
        """清空并基于历史消息重建指定会话记忆。

        Args:
            session_id: 会话标识。

        Returns:
            dict[str, Any]: 重建后的记忆快照或会话不存在错误。
        """
        session = await self._await_call(SessionManager.get_session, session_id)
        if not session:
            return {"ok": False, "error": "会话不存在"}
        await self._await_call(self._get_session_memory_service().clear_memory, session_id)
        snapshot = await self._await_call(self._get_session_memory_service().rebuild_from_messages, session_id)
        return {"ok": True, "data": snapshot}

    async def admin_clear_session_messages(self, session_id: str) -> dict[str, Any]:
        """清空指定会话历史消息，并同步清理记忆和日志工件。

        Args:
            session_id: 会话标识。

        Returns:
            dict[str, Any]: 清理结果、删除数量和附属工件清理状态。
        """
        session = await self._await_call(SessionManager.get_session, session_id)
        if not session:
            return {"ok": False, "error": "会话不存在"}

        existing_messages = await self._await_call(MessageManager.get_messages, session_id)
        removed_count = len(existing_messages)
        if removed_count > 0:
            await self._await_call(MessageManager.delete_messages, session_id)
        memory_cleared, memory_error_type = await self._clear_session_memory(session_id)
        async with concurrency_guard.keyed_lock(
            f"session-artifacts:{session_id}",
            timeout=self._config.SESSION_ARTIFACT_LOCK_TIMEOUT_SECONDS,
        ):
            log_artifacts_deleted, log_artifacts_error_type = self._delete_session_log_artifacts(session_id)
        await self._await_call(SessionManager.update_session_time, session_id)

        response: dict[str, Any] = {
            "ok": True,
            "message": "会话历史记录已清空",
            "removed_count": removed_count,
            "memory_cleared": memory_cleared,
            "log_artifacts_deleted": log_artifacts_deleted,
        }
        if memory_error_type:
            response["memory_error_type"] = memory_error_type
        if log_artifacts_error_type:
            response["log_artifacts_error_type"] = log_artifacts_error_type
        return response

    async def admin_delete_session(self, session_id: str) -> dict[str, Any]:
        """删除指定会话，并尽量清理记忆和日志工件。

        Args:
            session_id: 会话标识。

        Returns:
            dict[str, Any]: 删除结果和附属工件清理状态。
        """
        memory_cleared, memory_error_type = await self._clear_session_memory(session_id)
        success = await self._await_call(SessionManager.delete_session, session_id)
        if success:
            async with concurrency_guard.keyed_lock(
                f"session-artifacts:{session_id}",
                timeout=self._config.SESSION_ARTIFACT_LOCK_TIMEOUT_SECONDS,
            ):
                log_artifacts_deleted, log_artifacts_error_type = self._delete_session_log_artifacts(session_id)
            response: dict[str, Any] = {
                "ok": True,
                "message": "会话已删除",
                "memory_cleared": memory_cleared,
                "log_artifacts_deleted": log_artifacts_deleted,
            }
            if memory_error_type:
                response["memory_error_type"] = memory_error_type
            if log_artifacts_error_type:
                response["log_artifacts_error_type"] = log_artifacts_error_type
            return response
        return {"ok": False, "error": "删除失败或会话不存在"}
