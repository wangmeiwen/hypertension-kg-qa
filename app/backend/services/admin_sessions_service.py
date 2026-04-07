"""管理员会话管理服务。"""

from __future__ import annotations

from pathlib import Path
import json
from typing import Any, Awaitable, Callable

from app.backend.repositories.database import MessageManager, SessionManager, SessionMemoryManager


class AdminSessionsService:
    """封装管理员会话与记忆管理相关业务。"""

    def __init__(
        self,
        await_call: Callable[..., Awaitable[Any]],
        get_session_memory_service: Callable[[], Any],
    ) -> None:
        """初始化实例并注入运行依赖。
        
        Args:
            await_call: 函数输入参数。
            get_session_memory_service: 函数输入参数。
        
        Returns:
            None: 无返回值。
        """
        self._await_call = await_call
        self._get_session_memory_service = get_session_memory_service

    @staticmethod
    def _project_root() -> Path:
        """执行project_root流程并返回结果。
        
        Args:
            无。
        
        Returns:
            Path: 执行结果。
        """
        return Path(__file__).resolve().parents[3]

    @staticmethod
    def _safe_log_id(raw: str, fallback: str = "unknown") -> str:
        """执行safe_log_id流程并返回结果。
        
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

    @staticmethod
    def _normalize_response_mode_for_display(value: Any) -> str:
        token = str(value or "").strip()
        if token == "direct_answer":
            return "direct_answer"
        if token == "retrieval_required":
            return "retrieval_required"
        return token or "retrieval_required"

    @staticmethod
    def _response_mode_label(value: Any) -> str:
        token = AdminSessionsService._normalize_response_mode_for_display(value)
        if token == "direct_answer":
            return "能直接回答"
        if token == "retrieval_required":
            return "需要检索"
        return token or "未知"

    def _read_session_turn_records(self, session_id: str) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
        """读取session_turn_records相关信息并返回结果。
        
        Args:
            session_id: 会话标识。
        
        Returns:
            tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]: 执行结果。
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
            except (OSError, ValueError, TypeError):
                continue
        turn_rows.sort(key=lambda item: str(item.get("started_at") or ""))
        return turn_rows, memory_updates

    @staticmethod
    def _merge_turn_memory_update(turn: dict[str, Any], memory_update: dict[str, Any]) -> dict[str, Any]:
        """合并turn_memory_update相关信息并返回结果。
        
        Args:
            turn: 函数输入参数。
            memory_update: 函数输入参数。
        
        Returns:
            dict[str, Any]: 结构化结果数据。
        """
        if not memory_update:
            return turn
        memory_compression = turn.get("memory_compression") if isinstance(turn.get("memory_compression"), list) else []
        snapshot = memory_update.get("snapshot") if isinstance(memory_update.get("snapshot"), dict) else {}
        updated_at = str(memory_update.get("updated_at") or "")
        merged_turn = dict(turn)
        merged_turn["llm_calls"] = max(int(turn.get("llm_calls") or 0), int(memory_update.get("llm_calls") or 0))
        merged_turn["total_tokens"] = max(int(turn.get("total_tokens") or 0), int(memory_update.get("total_tokens") or 0))
        update_errors = memory_update.get("error_types") if isinstance(memory_update.get("error_types"), dict) else {}
        if update_errors:
            base_errors = merged_turn.get("error_types") if isinstance(merged_turn.get("error_types"), dict) else {}
            merged_turn["error_types"] = {
                **base_errors,
                **{str(key): int(value or 0) for key, value in update_errors.items()},
            }
        patched: list[dict[str, Any]] = []
        for item in memory_compression:
            row = item if isinstance(item, dict) else {}
            if str(row.get("stage_name") or "") != "压缩后记忆":
                patched.append(row)
                continue
            summary = snapshot.get("summary") if isinstance(snapshot.get("summary"), dict) else {}
            retrieval = snapshot.get("retrieval_summary") if isinstance(snapshot.get("retrieval_summary"), dict) else {}
            patched.append(
                {
                    **row,
                    "event_time": updated_at or str(row.get("event_time") or ""),
                    "content_lines": [
                        f"会话目标：{str(summary.get('conversation_goal') or '无')}",
                        f"已确认事实：{'；'.join(summary.get('confirmed_facts') or []) or '无'}",
                        f"检索焦点：{'；'.join(retrieval.get('focus_entities') or []) or '无'}",
                    ],
                }
            )
        merged_turn["memory_compression"] = patched
        return merged_turn

    @staticmethod
    def _sanitize_text(value: Any, limit: int = 240) -> str:
        """清洗输入并返回可用结果。
        
        Args:
            value: 待处理输入值。
            limit: 函数输入参数。
        
        Returns:
            str: 文本结果。
        """
        text = str(value or "").replace("\n", " ").strip()
        if not text:
            return ""
        if len(text) <= limit:
            return text
        return f"{text[:limit]}..."

    @classmethod
    def _join_lines(cls, rows: Any, limit: int = 4, item_limit: int = 100) -> str:
        """执行join_lines流程并返回结果。
        
        Args:
            rows: 输入数据列表。
            limit: 函数输入参数。
            item_limit: 函数输入参数。
        
        Returns:
            str: 文本结果。
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
        """执行humanize_payload流程并返回结果。
        
        Args:
            value: 待处理输入值。
        
        Returns:
            str: 文本结果。
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
        """执行lane_order流程并返回结果。
        
        Args:
            name: 函数输入参数。
        
        Returns:
            int: 数值结果。
        """
        mapping = {
            "主控编排": 0,
            "图谱代理": 1,
            "检索代理": 2,
            "评估代理": 3,
            "外部资源": 4,
        }
        return mapping.get(str(name or ""), 99)

    @staticmethod
    def _safe_event_id(turn_id: str, prefix: str, index: int) -> str:
        """执行safe_event_id流程并返回结果。
        
        Args:
            turn_id: 对象标识。
            prefix: 函数输入参数。
            index: 函数输入参数。
        
        Returns:
            str: 文本结果。
        """
        return f"{turn_id}:{prefix}:{index}"

    @classmethod
    def _tool_to_resource(cls, tool_name: str) -> tuple[str, str]:
        """执行tool_to_resource流程并返回结果。
        
        Args:
            tool_name: 函数输入参数。
        
        Returns:
            tuple[str, str]: 执行结果。
        """
        lowered = str(tool_name or "").strip().lower()
        if "neo4j" in lowered or lowered == "kg_query":
            return "Neo4j", "调用图谱数据库"
        if "milvus" in lowered or "rag" in lowered:
            return "Milvus", "调用向量检索"
        if "kg_entity_relation_search" in lowered:
            return "图谱检索服务", "调用图谱检索工具"
        if "dashscope" in lowered or "llm" in lowered:
            return "DashScope", "调用大模型"
        return "外部资源", "调用外部工具"

    @classmethod
    def _build_detail(
        cls,
        *,
        input_content: Any,
        output_content: Any,
        handoff: Any,
        final_return: Any,
        status: Any,
        note: Any,
    ) -> dict[str, str]:
        """构建并返回目标数据。
        
        Args:
            input_content: 函数输入参数。
            output_content: 函数输入参数。
            handoff: 函数输入参数。
            final_return: 函数输入参数。
            status: 函数输入参数。
            note: 函数输入参数。
        
        Returns:
            dict[str, str]: 结构化结果数据。
        """
        return {
            "输入内容": cls._humanize_payload(input_content),
            "输出内容": cls._humanize_payload(output_content),
            "输出承接方": cls._humanize_payload(handoff),
            "最终返回": cls._humanize_payload(final_return),
            "状态": cls._humanize_payload(status),
            "补充说明": cls._humanize_payload(note),
        }

    @classmethod
    def _build_flow_events(cls, turn: dict[str, Any]) -> list[dict[str, Any]]:
        """构建并返回目标数据。
        
        Args:
            turn: 函数输入参数。
        
        Returns:
            list[dict[str, Any]]: 处理后的列表结果。
        """
        turn_id = str(turn.get("turn_id") or "")
        started_at = str(turn.get("started_at") or "")
        finished_at = str(turn.get("finished_at") or started_at)
        question = str(turn.get("question") or "")
        answer_digest = str(turn.get("answer_digest") or "")
        memory_before = turn.get("memory_before") if isinstance(turn.get("memory_before"), dict) else {}
        resolved_query = turn.get("resolved_query") if isinstance(turn.get("resolved_query"), dict) else {}
        orchestrator_decision = turn.get("orchestrator_decision") if isinstance(turn.get("orchestrator_decision"), dict) else {}
        agent_lanes = turn.get("agent_lanes") if isinstance(turn.get("agent_lanes"), dict) else {}
        tool_observations = turn.get("tool_observations") if isinstance(turn.get("tool_observations"), list) else []
        memory_compression = turn.get("memory_compression") if isinstance(turn.get("memory_compression"), list) else []
        timeline = turn.get("timeline") if isinstance(turn.get("timeline"), list) else []

        events: list[dict[str, Any]] = []

        focus_entities = cls._join_lines(memory_before.get("focus_entities"), limit=3, item_limit=80)
        events.append(
            {
                "event_id": cls._safe_event_id(turn_id, "memory-read", 0),
                "event_time": started_at,
                "lane": "主控编排",
                "stage": "读取会话记忆",
                "title": "载入当前会话记忆",
                "summary": cls._sanitize_text(
                    f"会话目标：{memory_before.get('conversation_goal') or '无'}；关注实体：{focus_entities}",
                    120,
                ),
                "actor": "主控编排",
                "kind": "memory_read",
                "detail": cls._build_detail(
                    input_content=question,
                    output_content={
                        "会话目标": memory_before.get("conversation_goal") or "无",
                        "已确认事实": cls._join_lines(memory_before.get("confirmed_facts"), limit=4, item_limit=100),
                        "未闭合问题": cls._join_lines(memory_before.get("open_questions"), limit=4, item_limit=100),
                        "关注实体": focus_entities,
                    },
                    handoff="主控编排",
                    final_return="会话记忆摘要已送入后续决策链路",
                    status="完成",
                    note="该阶段读取当前会话中的长期记忆与检索摘要",
                ),
            }
        )

        if resolved_query:
            events.append(
                {
                    "event_id": cls._safe_event_id(turn_id, "rewrite", 0),
                    "event_time": started_at,
                    "lane": "主控编排",
                    "stage": "问题改写",
                    "title": "生成本轮独立问题",
                    "summary": cls._sanitize_text(
                        resolved_query.get("standalone_question") or question or "本轮问题改写结果",
                        120,
                    ),
                    "actor": "主控编排",
                    "kind": "model_call",
                    "detail": cls._build_detail(
                        input_content=question,
                        output_content={
                            "独立问题": resolved_query.get("standalone_question") or "无",
                            "意图": resolved_query.get("intent") or "无",
                            "问题模式": resolved_query.get("question_mode") or "无",
                            "优先来源": cls._join_lines(resolved_query.get("preferred_sources"), limit=4, item_limit=40),
                        },
                        handoff="主控编排",
                        final_return=resolved_query.get("standalone_question") or question,
                        status="完成",
                        note="主控先把用户问题补全为当前轮的独立问题",
                    ),
                }
            )

        if orchestrator_decision:
            response_mode = cls._normalize_response_mode_for_display(orchestrator_decision.get("response_mode"))
            response_label = cls._response_mode_label(response_mode)
            route_reason = (
                orchestrator_decision.get("direct_reason")
                or orchestrator_decision.get("retrieval_reason")
                or orchestrator_decision.get("memory_route_reason")
                or "无"
            )
            events.append(
                {
                    "event_id": cls._safe_event_id(turn_id, "decision", 0),
                    "event_time": finished_at,
                    "lane": "主控编排",
                    "stage": "主控决策",
                    "title": "确定响应路径",
                    "summary": cls._sanitize_text(
                        f"响应模式：{response_label}；回答模式：{orchestrator_decision.get('answer_mode') or 'unknown'}；原因：{route_reason}",
                        120,
                    ),
                    "actor": "主控编排",
                    "kind": "decision",
                    "detail": cls._build_detail(
                        input_content=resolved_query.get("standalone_question") or question,
                        output_content={
                            "响应模式": response_label,
                            "回答模式": orchestrator_decision.get("answer_mode") or "unknown",
                            "路由原因": route_reason,
                            "回答来源": orchestrator_decision.get("answer_source") or "unknown",
                            "问题模式": orchestrator_decision.get("question_mode") or "unknown",
                            "主题切换": "已触发" if orchestrator_decision.get("topic_shift_detected") else "未触发",
                        },
                        handoff="图谱代理 / 检索代理 / 评估代理",
                        final_return="主控已将问题分发到后续执行链路",
                        status="完成",
                        note=f"Topic shift 原因：{orchestrator_decision.get('topic_shift_reason') or 'no_shift'}",
                    ),
                }
            )

        event_index = 0
        for lane_name in ("主控编排", "图谱代理", "检索代理", "评估代理"):
            cards = agent_lanes.get(lane_name) if isinstance(agent_lanes.get(lane_name), list) else []
            for card in cards:
                row = card if isinstance(card, dict) else {}
                title = str(row.get("title") or lane_name)
                if lane_name == "主控编排" and title in {"主控路由判定"}:
                    continue
                event_time = str(row.get("occurred_at") or row.get("started_at") or row.get("finished_at") or finished_at)
                output_handoff = "主控编排"
                if lane_name == "主控编排":
                    output_handoff = "图谱代理 / 检索代理 / 评估代理"
                elif lane_name in {"图谱代理", "检索代理", "评估代理"}:
                    output_handoff = "主控编排"
                events.append(
                    {
                        "event_id": cls._safe_event_id(turn_id, "lane", event_index),
                        "event_time": event_time,
                        "lane": lane_name,
                        "stage": title,
                        "title": title,
                        "summary": cls._sanitize_text(row.get("decision_preview") or row.get("prompt_preview") or title, 120),
                        "actor": lane_name,
                        "kind": "lane_event",
                        "detail": cls._build_detail(
                            input_content=row.get("full_prompt") or row.get("prompt_preview") or "无",
                            output_content=row.get("full_decision") or row.get("decision_preview") or "无",
                            handoff=output_handoff,
                            final_return=row.get("decision_preview") or row.get("full_decision") or "无",
                            status=row.get("status") or "完成",
                            note=f"{lane_name} 在本轮中的执行节点",
                        ),
                    }
                )
                event_index += 1

        for obs_index, observation in enumerate(tool_observations):
            row = observation if isinstance(observation, dict) else {}
            tool_name = str(row.get("tool_name") or row.get("requested_tool_name") or "")
            if not tool_name:
                continue
            resource_name, stage_name = cls._tool_to_resource(tool_name)
            result_obj = row.get("result") if isinstance(row.get("result"), dict) else {}
            agent_name = str(row.get("agent") or "")
            handoff = "主控编排"
            if agent_name == "kg":
                handoff = "图谱代理"
            elif agent_name == "rag":
                handoff = "检索代理"
            events.append(
                {
                    "event_id": cls._safe_event_id(turn_id, "resource", obs_index),
                    "event_time": finished_at,
                    "lane": "外部资源",
                    "stage": stage_name,
                    "title": f"{resource_name} 请求",
                    "summary": cls._sanitize_text(
                        f"{tool_name}：{cls._humanize_payload(result_obj.get('result') if isinstance(result_obj.get('result'), dict) else result_obj)}",
                        120,
                    ),
                    "actor": resource_name,
                    "kind": "resource_call",
                    "detail": cls._build_detail(
                        input_content=row.get("subquery") or row.get("question") or row.get("action") or tool_name,
                        output_content=result_obj.get("result") if isinstance(result_obj.get("result"), dict) else result_obj,
                        handoff=handoff,
                        final_return=result_obj.get("message") or result_obj.get("summary") or cls._humanize_payload(result_obj),
                        status="成功" if bool(result_obj.get("ok", True)) else "失败",
                        note=f"调用工具：{tool_name}",
                    ),
                }
            )

        for stage_index, stage in enumerate(memory_compression):
            row = stage if isinstance(stage, dict) else {}
            stage_name = str(row.get("stage_name") or "记忆压缩")
            description = str(row.get("description") or "")
            content_lines = row.get("content_lines") if isinstance(row.get("content_lines"), list) else []
            events.append(
                {
                    "event_id": cls._safe_event_id(turn_id, "compression", stage_index),
                    "event_time": str(row.get("event_time") or finished_at),
                    "lane": "外部资源",
                    "stage": stage_name,
                    "title": stage_name,
                    "summary": cls._sanitize_text(description or cls._join_lines(content_lines, limit=2, item_limit=80), 120),
                    "actor": "会话记忆",
                    "kind": "memory_compression",
                    "detail": cls._build_detail(
                        input_content=question if stage_name == "原始消息增量" else cls._join_lines(content_lines, limit=5, item_limit=120),
                        output_content=content_lines,
                        handoff="写回会话记忆",
                        final_return=cls._join_lines(content_lines, limit=5, item_limit=120),
                        status="完成",
                        note=description or "该阶段展示本轮记忆压缩的状态变化",
                    ),
                }
            )

        for time_index, event in enumerate(timeline):
            row = event if isinstance(event, dict) else {}
            title = str(row.get("title") or "")
            detail = str(row.get("detail") or "")
            if not title:
                continue
            events.append(
                {
                    "event_id": cls._safe_event_id(turn_id, "timeline", time_index),
                    "event_time": str(row.get("event_time") or finished_at),
                    "lane": "主控编排",
                    "stage": title,
                    "title": title,
                    "summary": cls._sanitize_text(detail or title, 120),
                    "actor": "主控编排",
                    "kind": "timeline",
                    "detail": cls._build_detail(
                        input_content=question,
                        output_content=detail or title,
                        handoff="当前时间线节点",
                        final_return=detail or title,
                        status="完成",
                        note="该事件来自本轮处理的关键时间线",
                    ),
                }
            )

        events.sort(
            key=lambda item: (
                str(item.get("event_time") or ""),
                cls._lane_order(str(item.get("lane") or "")),
                str(item.get("event_id") or ""),
            )
        )
        return events

    async def admin_get_sessions(self, limit: int = 100, offset: int = 0) -> dict[str, Any]:
        """获取所有会话列表。"""
        sessions = await self._await_call(SessionManager.get_all_sessions, limit, offset)
        total = await self._await_call(SessionManager.get_total_sessions_count)
        return {"ok": True, "data": sessions, "total": total}

    async def admin_get_session_messages(self, session_id: str) -> dict[str, Any]:
        """获取指定会话消息。"""
        session = await self._await_call(SessionManager.get_session, session_id)
        if not session:
            return {"ok": False, "error": "会话不存在"}
        messages = await self._await_call(MessageManager.get_messages, session_id)
        return {"ok": True, "session": session, "messages": messages}

    async def admin_get_session_observability(self, session_id: str) -> dict[str, Any]:
        """获取指定会话的按轮次观测详情。"""
        session = await self._await_call(SessionManager.get_session, session_id)
        if not session:
            return {"ok": False, "error": "会话不存在"}

        turns, memory_updates = self._read_session_turn_records(session_id)
        normalized_turns: list[dict[str, Any]] = []
        for turn in turns:
            merged_turn = self._merge_turn_memory_update(turn, memory_updates.get(str(turn.get("turn_id") or "")) or {})
            orchestrator_decision = (
                merged_turn.get("orchestrator_decision") if isinstance(merged_turn.get("orchestrator_decision"), dict) else {}
            )
            normalized_response_mode = self._normalize_response_mode_for_display(orchestrator_decision.get("response_mode"))
            normalized_decision = {
                **orchestrator_decision,
                "response_mode": normalized_response_mode,
                "response_mode_label": self._response_mode_label(normalized_response_mode),
            }
            merged_for_events = {**merged_turn, "orchestrator_decision": normalized_decision}
            normalized_turns.append(
                {
                    "turn_id": str(merged_turn.get("turn_id") or ""),
                    "session_id": str(session_id or ""),
                    "user_message_id": int(merged_turn.get("user_message_id") or 0),
                    "assistant_message_id": int(merged_turn.get("assistant_message_id") or 0),
                    "question": str(merged_turn.get("question") or ""),
                    "answer_digest": str(merged_turn.get("answer_digest") or ""),
                    "started_at": str(merged_turn.get("started_at") or ""),
                    "finished_at": str(merged_turn.get("finished_at") or ""),
                    "status": str(merged_turn.get("status") or "done"),
                    "llm_calls": int(merged_turn.get("llm_calls") or 0),
                    "total_tokens": int(merged_turn.get("total_tokens") or 0),
                    "memory_before": merged_turn.get("memory_before") if isinstance(merged_turn.get("memory_before"), dict) else {},
                    "resolved_query": merged_turn.get("resolved_query") if isinstance(merged_turn.get("resolved_query"), dict) else {},
                    "orchestrator_decision": normalized_decision,
                    "agent_lanes": merged_turn.get("agent_lanes") if isinstance(merged_turn.get("agent_lanes"), dict) else {},
                    "flow_events": self._build_flow_events(merged_for_events),
                    "memory_compression": merged_turn.get("memory_compression") if isinstance(merged_turn.get("memory_compression"), list) else [],
                    "timeline": merged_turn.get("timeline") if isinstance(merged_turn.get("timeline"), list) else [],
                }
            )
        return {"ok": True, "session": session, "turns": normalized_turns}

    async def admin_get_session_memory(self, session_id: str) -> dict[str, Any]:
        """获取指定会话记忆快照。"""
        session = await self._await_call(SessionManager.get_session, session_id)
        if not session:
            return {"ok": False, "error": "会话不存在"}
        snapshot = await self._await_call(self._get_session_memory_service().load_memory, session_id)
        return {"ok": True, "data": snapshot}

    async def admin_rebuild_session_memory(self, session_id: str) -> dict[str, Any]:
        """重建指定会话记忆。"""
        session = await self._await_call(SessionManager.get_session, session_id)
        if not session:
            return {"ok": False, "error": "会话不存在"}
        await self._await_call(self._get_session_memory_service().clear_memory, session_id)
        snapshot = await self._await_call(self._get_session_memory_service().rebuild_from_messages, session_id)
        return {"ok": True, "data": snapshot}

    async def admin_clear_session_messages(self, session_id: str) -> dict[str, Any]:
        """清空指定会话历史消息。"""
        session = await self._await_call(SessionManager.get_session, session_id)
        if not session:
            return {"ok": False, "error": "会话不存在"}

        existing_messages = await self._await_call(MessageManager.get_messages, session_id)
        removed_count = len(existing_messages)
        if removed_count > 0:
            await self._await_call(MessageManager.delete_messages, session_id)
        await self._await_call(SessionMemoryManager.clear_summary, session_id)
        await self._await_call(SessionManager.update_session_time, session_id)

        return {"ok": True, "message": "会话历史记录已清空", "removed_count": removed_count}

    async def admin_delete_session(self, session_id: str) -> dict[str, Any]:
        """删除会话。"""
        success = await self._await_call(SessionManager.delete_session, session_id)
        if success:
            return {"ok": True, "message": "会话已删除"}
        return {"ok": False, "error": "删除失败或会话不存在"}
