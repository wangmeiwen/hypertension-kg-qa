"""基于 create_app 的确定性测试支撑。"""

from __future__ import annotations

from dataclasses import dataclass, field
import itertools
import json
from typing import Any, AsyncIterator

from app.backend.app_factory import BackendAppTestOverrides
from app.backend.domain.stream_events import (
    DoneStreamEvent,
    JobStreamEvent,
    PersistedStreamEvent,
    ProgressStreamEvent,
    ReplyStreamEvent,
    SessionStreamEvent,
    StreamEvent,
    stream_event_json_line,
)
from app.backend.services.startup_runtime_service import StartupRuntimeBehavior


MEDICAL_DISCLAIMER = "以上内容仅供参考，不构成诊断或治疗建议，不能替代专业医生意见；如有不适或紧急情况，请及时就医。"


class DeterministicMcpServer:
    """轻量 MCP 测试桩。"""

    def list_tools(self) -> list[dict[str, Any]]:
        """返回确定性的 MCP 工具清单。"""
        return [
            {"name": "kg_entity_relation_search", "description": "知识图谱检索"},
            {"name": "rag_search", "description": "文档语义检索"},
        ]

    async def ainvoke_batch(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """批量回显工具 payload，模拟 MCP 批处理调用。"""
        return [
            {"ok": True, "result": row.get("payload") or {}, "meta": {}} for row in rows
        ]

    async def ainvoke(self, tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        """回显单次工具调用，模拟 MCP 工具执行结果。"""
        return {"ok": True, "tool_name": tool_name, "result": payload, "meta": {}}


class DeterministicSessionMemoryService:
    """会话记忆测试桩。"""

    def __init__(self) -> None:
        """初始化内存态会话记忆快照容器。"""
        self._snapshots: dict[str, dict[str, Any]] = {}

    def _empty_snapshot(self) -> dict[str, Any]:
        """构造确定性测试使用的空记忆快照。"""
        return {
            "qa_memory": {"qa_pairs": [], "last_message_id": 0},
            "retrieval_memory": {"questions": [], "last_message_id": 0},
            "updated_at": "",
        }

    async def load_memory(self, session_id: str) -> dict[str, Any]:
        """读取测试内存中的会话记忆快照。"""
        return json.loads(
            json.dumps(
                self._snapshots.get(session_id) or self._empty_snapshot(),
                ensure_ascii=False,
            )
        )

    async def clear_memory(self, session_id: str) -> None:
        """清理测试内存中的指定会话记忆。"""
        self._snapshots.pop(session_id, None)

    async def rebuild_from_messages(self, session_id: str) -> dict[str, Any]:
        """模拟基于消息重建会话记忆，并写入固定更新时间。"""
        snapshot = self._empty_snapshot()
        snapshot["updated_at"] = "2026-04-23T10:00:00"
        self._snapshots[session_id] = snapshot
        return snapshot

    async def build_runtime_context(
        self, session_id: str, input_text: str, current_message_id: int
    ) -> dict[str, Any]:
        """构建确定性的主控运行时记忆上下文。"""
        snapshot = await self.load_memory(session_id)
        return {
            "original_input_text": input_text,
            "memory_context": {
                "qa_pairs": snapshot["qa_memory"]["qa_pairs"],
                "retrieval_questions": snapshot["retrieval_memory"]["questions"],
                "current_message_id": current_message_id,
            },
        }

    async def refresh_after_turn(
        self, session_id: str, assistant_message_id: int, turn_artifacts: dict[str, Any]
    ) -> dict[str, Any]:
        """模拟单轮结束后的记忆刷新。"""
        snapshot = await self.load_memory(session_id)
        qa_pairs = list(snapshot["qa_memory"].get("qa_pairs") or [])
        qa_pairs.append(
            {
                "user_question": str(turn_artifacts.get("user_question") or ""),
                "assistant_answer": str(turn_artifacts.get("answer_digest") or ""),
                "assistant_message_id": int(assistant_message_id or 0),
            }
        )
        updated = {
            "qa_memory": {
                "qa_pairs": qa_pairs[-6:],
                "last_message_id": int(assistant_message_id or 0),
            },
            "retrieval_memory": snapshot["retrieval_memory"],
            "updated_at": "2026-04-23T10:05:00",
        }
        self._snapshots[session_id] = updated
        return updated


def _fixed_now() -> str:
    """返回确定性测试固定时间。"""
    return "2026-04-23T10:00:00Z"


def _session_title(text: Any) -> str:
    """根据输入文本生成确定性会话标题。"""
    value = str(text or "").strip()
    if not value:
        return "新建会话"
    return value[:30] + "..." if len(value) > 30 else value


def _node_name(node: dict[str, Any]) -> str:
    """从测试图节点中提取展示名称。"""
    properties_value = node.get("properties")
    props = properties_value if isinstance(properties_value, dict) else {}
    return str(props.get("name") or node.get("id") or "")


def _public_node(node: dict[str, Any]) -> dict[str, Any]:
    """将内部测试图节点转换为公开接口节点结构。"""
    labels = list(node.get("labels") or ["Entity"])
    props = dict(node.get("properties") or {})
    return {
        "id": node.get("id"),
        "name": str(props.get("name") or node.get("id") or ""),
        "label": labels[0] if labels else "Entity",
        "labels": labels,
        "properties": props,
    }


def _json_line(payload: dict[str, Any]) -> str:
    """将事件 payload 序列化为一行 JSONL 文本。"""
    return json.dumps(payload, ensure_ascii=False) + "\n"


@dataclass
class DeterministicSessionState:
    next_session_id: int = 1
    next_message_id: int = 1
    sessions: dict[str, dict[str, Any]] = field(default_factory=dict)
    messages: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def create_session(self, user_id: str, title: str) -> dict[str, Any]:
        """在内存态会话集合中创建会话。"""
        session_id = f"session-{self.next_session_id}"
        self.next_session_id += 1
        session = {
            "id": session_id,
            "user_id": str(user_id or "user-test"),
            "title": str(title or "新建会话"),
            "message_count": 0,
            "created_at": _fixed_now(),
            "updated_at": _fixed_now(),
        }
        self.sessions[session_id] = session
        self.messages[session_id] = []
        return dict(session)

    def ensure_session(
        self, session_id: str, user_id: str, title: str = ""
    ) -> dict[str, Any]:
        """返回已有会话或创建一个新会话。"""
        safe_session_id = str(session_id or "").strip()
        if safe_session_id and safe_session_id in self.sessions:
            return dict(self.sessions[safe_session_id])
        return self.create_session(user_id=user_id, title=title or "新建会话")

    def add_message(
        self, session_id: str, role: str, content: str, think_content: str = ""
    ) -> dict[str, Any]:
        """向内存态会话追加消息。"""
        message = {
            "id": self.next_message_id,
            "role": str(role or "user"),
            "content": str(content or ""),
            "think_content": str(think_content or ""),
            "created_at": _fixed_now(),
        }
        self.next_message_id += 1
        self.messages.setdefault(session_id, []).append(message)
        session = self.sessions.get(session_id)
        if session is not None:
            session["message_count"] = len(self.messages.get(session_id, []))
            session["updated_at"] = _fixed_now()
        return dict(message)

    def list_sessions(
        self, user_id: str = "", limit: int = 50, offset: int = 0
    ) -> tuple[list[dict[str, Any]], int]:
        """按用户和分页参数返回内存态会话列表。"""
        rows = list(self.sessions.values())
        if user_id:
            rows = [
                row for row in rows if str(row.get("user_id") or "") == str(user_id)
            ]
        rows.sort(key=lambda row: str(row.get("updated_at") or ""), reverse=True)
        total = len(rows)
        sliced = rows[max(0, offset) : max(0, offset) + max(0, limit)]
        return [dict(row) for row in sliced], total


class DeterministicReadinessService:
    """始终返回 ready 的测试 readiness 服务。"""

    async def build_readiness_report(self) -> dict[str, Any]:
        """构建确定性 ready 状态报告。"""
        return {
            "ready": True,
            "level": "ready",
            "checked_at": _fixed_now(),
            "critical_not_ready": [],
            "optional_issues": [],
            "dependencies": {
                "mysql": {"ready": True, "mode": "deterministic"},
                "mcp": {"ready": True, "mode": "deterministic"},
            },
        }

    async def ensure_core_ready(self, endpoint: str) -> None:
        """模拟核心依赖已就绪，不抛出异常。"""
        _ = endpoint
        return None


class DeterministicA2AEventStreamService:
    """生成固定 A2A 流式事件的测试服务。"""

    async def iter_a2a_stream_events(
        self,
        input_text: str,
        user_id: str,
        session_id: str,
        request_id: str = "",
        image_data: Any = None,
        file_id: str = "",
        original_input_text: str = "",
        memory_context: dict[str, Any] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """按固定顺序生成 job、msg、reply 和 done 事件。"""
        _ = user_id, session_id, request_id, image_data, file_id, memory_context
        question = str(original_input_text or input_text or "").strip()
        reply = f"基于当前测试知识库，建议围绕血压监测、生活方式干预和规范用药评估：{question}\n\n{MEDICAL_DISCLAIMER}"
        job_id = f"job-{session_id or 'deterministic'}"
        yield JobStreamEvent(job_id=job_id, request_id=request_id, task_id=job_id)
        yield ProgressStreamEvent(
            stage="orchestrator", msg="主控编排完成", task_id=job_id
        )
        yield ReplyStreamEvent(delta=reply, task_id=job_id)
        yield DoneStreamEvent(
            status="done",
            task_id=job_id,
            processing_steps=["主控编排", "图谱代理", "检索代理"],
            tool_observations=[
                {
                    "agent": "kg",
                    "tool_name": "kg_entity_relation_search",
                    "result": {"ok": True, "result": {"summary": "图谱证据"}},
                },
                {
                    "agent": "rag",
                    "tool_name": "rag_search",
                    "result": {"ok": True, "result": {"summary": "检索证据"}},
                },
            ],
            evidence_summary={"supported_points": ["血压监测", "限盐饮食"]},
            telemetry={"llm_calls": 2, "tool_calls": 2, "total_tokens": 128},
        )

    async def generate_data_lines(
        self,
        input_text: str,
        user_id: str,
        session_id: str = "",
        request_id: str = "",
        image_data: Any = None,
    ) -> AsyncIterator[str]:
        """将确定性 A2A 事件序列化为 JSONL 流。"""
        async for event in self.iter_a2a_stream_events(
            input_text=input_text,
            user_id=user_id,
            session_id=session_id,
            request_id=request_id,
            image_data=image_data,
            original_input_text=input_text,
            memory_context={},
        ):
            yield stream_event_json_line(event)


class DeterministicSessionTurnService:
    """确定性会话轮次准备测试服务。"""

    def __init__(
        self,
        state: DeterministicSessionState,
        memory_service: DeterministicSessionMemoryService,
    ) -> None:
        """注入内存态会话和记忆服务。"""
        self._state = state
        self._memory_service = memory_service

    @staticmethod
    def bind_process_session_trace_metadata(
        trace_id: str, session_id: str, user_id: str
    ) -> None:
        """测试桩中忽略追踪元数据绑定。"""
        _ = trace_id, session_id, user_id

    async def prepare_session_turn(
        self,
        input_text: str,
        session_id: str,
        user_id: str,
        image_data: Any = None,
        file_name: str = "",
        file_id: str = "",
    ) -> dict[str, Any]:
        """创建或复用会话、写入用户消息并返回运行时上下文。"""
        safe_user_id = str(user_id or "user-test")
        session = self._state.ensure_session(
            session_id, safe_user_id, _session_title(input_text)
        )
        content = str(input_text or "")
        if image_data:
            content = f"[图片] {content}"
        elif str(file_name or "").strip():
            file_marker = str(file_name or "").strip()
            if str(file_id or "").strip():
                file_marker = f"{file_marker}; file_id={str(file_id or '').strip()}"
            content = f"[文件:{file_marker}] {content}".strip()
        user_message = self._state.add_message(str(session["id"]), "user", content)
        runtime_payload = await self._memory_service.build_runtime_context(
            str(session["id"]), str(input_text or ""), int(user_message["id"])
        )
        return {
            "ok": True,
            "user_id": safe_user_id,
            "session_id": str(session["id"]),
            "user_message_id": int(user_message["id"]),
            "original_input_text": str(
                runtime_payload.get("original_input_text") or input_text or ""
            ),
            "memory_context": (
                runtime_payload.get("memory_context")
                if isinstance(runtime_payload.get("memory_context"), dict)
                else {}
            ),
        }


class DeterministicSessionStreamService:
    """确定性会话流式输出测试服务。"""

    def __init__(self, state: DeterministicSessionState) -> None:
        """注入内存态会话状态。"""
        self._state = state

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
        memory_context: dict[str, Any] | None = None,
    ) -> AsyncIterator[str]:
        """生成固定聊天流并在内存态会话中写入助手消息。"""
        _ = user_id, image_data, file_id, request_id, memory_context
        question = str(original_input_text or input_text or "").strip()
        reply = f"基于当前测试知识库，建议围绕血压监测、生活方式干预和规范用药评估：{question}\n\n{MEDICAL_DISCLAIMER}"
        job_id = f"job-{session_id or 'deterministic'}"
        yield stream_event_json_line(
            SessionStreamEvent(
                session_id=session_id,
                user_message_id=int(user_message_id or 0),
            )
        )
        yield stream_event_json_line(
            JobStreamEvent(job_id=job_id, request_id=request_id, task_id=job_id)
        )
        yield stream_event_json_line(
            ProgressStreamEvent(
                stage="orchestrator", msg="主控编排完成", task_id=job_id
            )
        )
        yield stream_event_json_line(ReplyStreamEvent(delta=reply, task_id=job_id))
        assistant = self._state.add_message(session_id, "assistant", reply)
        yield stream_event_json_line(
            PersistedStreamEvent(
                session_id=session_id,
                user_message_id=int(user_message_id or 0),
                assistant_message_id=int(assistant["id"]),
            )
        )
        yield stream_event_json_line(DoneStreamEvent(status="done", task_id=job_id))


class DeterministicSessionsHistoryService:
    """确定性会话历史测试服务。"""

    def __init__(self, state: DeterministicSessionState) -> None:
        """注入内存态会话状态。"""
        self._state = state

    async def create_session(self, data: dict[str, Any]) -> dict[str, Any]:
        """创建测试会话。"""
        session = self._state.create_session(
            str(data.get("user_id") or "user-test"),
            str(data.get("title") or "新建会话"),
        )
        return {"ok": True, "data": session}

    async def list_sessions(self, user_id: str = "", limit: int = 50) -> dict[str, Any]:
        """列出测试会话。"""
        rows, _ = self._state.list_sessions(user_id=user_id, limit=limit, offset=0)
        return {"ok": True, "data": rows}

    async def get_session(self, session_id: str, user_id: str = "") -> dict[str, Any]:
        """获取测试会话及其消息。"""
        session = self._state.sessions.get(session_id)
        if session is None or (
            user_id and str(session.get("user_id") or "") != str(user_id)
        ):
            return {"ok": False, "error": "session_not_found"}
        return {
            "ok": True,
            "data": {
                **session,
                "messages": [
                    dict(row) for row in self._state.messages.get(session_id, [])
                ],
            },
        }

    async def update_session(
        self, session_id: str, data: dict[str, Any]
    ) -> dict[str, Any]:
        """更新测试会话标题。"""
        session = self._state.sessions.get(session_id)
        if session is None:
            return {"ok": False, "error": "session_not_found"}
        session["title"] = str(data.get("title") or session.get("title") or "新建会话")
        session["updated_at"] = _fixed_now()
        return {"ok": True, "data": dict(session)}

    async def delete_session(
        self, session_id: str, user_id: str = ""
    ) -> dict[str, Any]:
        """删除测试会话及其消息。"""
        session = self._state.sessions.get(session_id)
        if session is None or (
            user_id and str(session.get("user_id") or "") != str(user_id)
        ):
            return {"ok": False, "error": "session_not_found"}
        self._state.sessions.pop(session_id, None)
        self._state.messages.pop(session_id, None)
        return {"ok": True}

    async def add_message(
        self, session_id: str, data: dict[str, Any]
    ) -> dict[str, Any]:
        """向测试会话追加消息。"""
        if session_id not in self._state.sessions:
            return {"ok": False, "error": "session_not_found"}
        message = self._state.add_message(
            session_id, str(data.get("role") or "user"), str(data.get("content") or "")
        )
        return {"ok": True, "data": message}

    async def get_messages(self, session_id: str, user_id: str = "") -> dict[str, Any]:
        """读取测试会话消息。"""
        session = self._state.sessions.get(session_id)
        if session is None or (
            user_id and str(session.get("user_id") or "") != str(user_id)
        ):
            return {"ok": False, "error": "session_not_found"}
        return {
            "ok": True,
            "data": [dict(row) for row in self._state.messages.get(session_id, [])],
        }

    async def rollback_to_question(
        self, session_id: str, data: dict[str, Any]
    ) -> dict[str, Any]:
        """将测试会话消息回撤到指定用户消息。"""
        target_id = int(data.get("message_id") or 0)
        rows = self._state.messages.get(session_id, [])
        keep_index = next(
            (
                idx
                for idx, row in enumerate(rows)
                if int(row.get("id") or 0) == target_id
            ),
            -1,
        )
        if keep_index < 0:
            return {"ok": False, "error": "message_not_found"}
        question = str(rows[keep_index].get("content") or "")
        self._state.messages[session_id] = rows[: keep_index + 1]
        if session_id in self._state.sessions:
            self._state.sessions[session_id]["message_count"] = len(
                self._state.messages[session_id]
            )
        return {
            "ok": True,
            "data": {
                "question": question,
                "removed_count": len(rows) - keep_index - 1,
                "memory_rebuilt": True,
            },
        }

    async def upload_file(self, file: Any) -> dict[str, Any]:
        """模拟文件上传并返回固定结构。"""
        filename = str(getattr(file, "filename", "") or "upload.bin")
        content_type = str(getattr(file, "content_type", "") or "")
        return {
            "ok": True,
            "data": {
                "filename": filename,
                "is_image": content_type.startswith("image/"),
                "base64": "",
            },
        }

    async def cancel_qa_job(self, job_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """模拟取消 QA 任务。"""
        _ = data
        return {"ok": True, "data": {"job_id": job_id, "status": "cancelled"}}


class DeterministicAdminSessionsService:
    """确定性管理员会话测试服务。"""

    def __init__(
        self,
        state: DeterministicSessionState,
        memory_service: DeterministicSessionMemoryService,
    ) -> None:
        """注入内存态会话状态和记忆服务。"""
        self._state = state
        self._memory_service = memory_service

    def _session_or_default(self, session_id: str) -> dict[str, Any]:
        """返回测试会话，缺失时构造一个默认展示会话。"""
        if session_id in self._state.sessions:
            return dict(self._state.sessions[session_id])
        return {
            "id": session_id,
            "user_id": "user-test",
            "title": "测试会话",
            "message_count": 0,
            "created_at": _fixed_now(),
            "updated_at": _fixed_now(),
        }

    async def admin_get_sessions(
        self, limit: int = 100, offset: int = 0
    ) -> dict[str, Any]:
        """返回管理员会话列表测试数据。"""
        rows, total = self._state.list_sessions(limit=limit, offset=offset)
        return {"ok": True, "data": rows, "total": total}

    async def admin_get_session_messages(self, session_id: str) -> dict[str, Any]:
        """返回指定会话的管理员消息视图。"""
        return {
            "ok": True,
            "session": self._session_or_default(session_id),
            "messages": [dict(row) for row in self._state.messages.get(session_id, [])],
        }

    async def admin_get_session_observability(self, session_id: str) -> dict[str, Any]:
        """返回指定会话的固定观测详情。"""
        messages = self._state.messages.get(session_id, [])
        question = next(
            (
                str(row.get("content") or "")
                for row in messages
                if row.get("role") == "user"
            ),
            "测试问题",
        )
        return {
            "ok": True,
            "turns": [
                {
                    "turn_id": f"{session_id}-turn-1",
                    "status": "done",
                    "question": question,
                    "answer_digest": "基于当前测试知识库",
                    "total_tokens": 128,
                    "started_at": _fixed_now(),
                    "finished_at": _fixed_now(),
                    "duration_ms": 32,
                    "flow_events": [
                        {
                            "event_id": "evt-1",
                            "event_time": _fixed_now(),
                            "lane": "主控编排",
                            "stage": "llm",
                            "title": "主控编排",
                            "summary": "模型输入与工具计划",
                            "detail": {
                                "model_input_prompt": "模型输入：测试问题",
                                "model_output_text": "工具结果：已完成",
                                "tool_call_results": "工具结果：图谱代理、检索代理",
                            },
                        },
                        {
                            "event_id": "evt-2",
                            "event_time": _fixed_now(),
                            "lane": "图谱代理",
                            "stage": "tool",
                            "title": "图谱代理",
                            "summary": "图谱证据",
                        },
                        {
                            "event_id": "evt-3",
                            "event_time": _fixed_now(),
                            "lane": "检索代理",
                            "stage": "tool",
                            "title": "检索代理",
                            "summary": "检索证据",
                        },
                    ],
                }
            ],
        }

    async def admin_get_session_memory(self, session_id: str) -> dict[str, Any]:
        """返回指定会话的测试记忆快照。"""
        return {"ok": True, "data": await self._memory_service.load_memory(session_id)}

    async def admin_rebuild_session_memory(self, session_id: str) -> dict[str, Any]:
        """模拟管理员重建会话记忆。"""
        return {
            "ok": True,
            "data": await self._memory_service.rebuild_from_messages(session_id),
        }

    async def admin_clear_session_messages(self, session_id: str) -> dict[str, Any]:
        """清空测试会话消息。"""
        self._state.messages[session_id] = []
        if session_id in self._state.sessions:
            self._state.sessions[session_id]["message_count"] = 0
        return {"ok": True}

    async def admin_delete_session(self, session_id: str) -> dict[str, Any]:
        """删除测试会话。"""
        self._state.sessions.pop(session_id, None)
        self._state.messages.pop(session_id, None)
        return {"ok": True}


class DeterministicAdminObservabilityService:
    """确定性管理员可观测性测试服务。"""

    async def admin_agent_observability_summary(
        self, limit: int = 200
    ) -> dict[str, Any]:
        """返回固定 Agent 可观测性汇总。"""
        _ = limit
        return {
            "ok": True,
            "data": {
                "total_runs": 1,
                "avg_duration_ms": 32,
                "total_tokens": 128,
                "total_llm_calls": 2,
                "total_tool_calls": 2,
            },
        }

    async def admin_agent_observability_runs(self, limit: int = 50) -> dict[str, Any]:
        """返回固定 Agent 运行明细。"""
        _ = limit
        return {
            "ok": True,
            "data": [
                {
                    "session_id": "session-1",
                    "timestamp": _fixed_now(),
                    "llm_calls": 2,
                    "attempted_tool_actions": 2,
                    "blocked_tool_actions": 0,
                    "tool_calls": 2,
                    "total_tokens": 128,
                }
            ],
        }


class DeterministicAdminRuntimeService:
    """确定性管理员运行时测试服务。"""

    async def admin_runtime_flags(self) -> dict[str, Any]:
        """返回固定运行时开关。"""
        return {
            "ok": True,
            "data": {
                "flags": {"test_mode": True, "deterministic_backend": True},
                "updated_at": _fixed_now(),
            },
        }

    async def admin_perf_baseline(self, days: int = 1) -> dict[str, Any]:
        """返回固定性能基线。"""
        return {
            "ok": True,
            "data": {
                "window_days": int(days or 1),
                "api_latency": {"p50_ms": 12, "p95_ms": 24},
                "event_loop_lag_ms": 0,
                "queue": {"running": 0, "queued": 0},
                "runtime_flags": {"test_mode": True},
                "active_threads": 1,
                "generated_at": _fixed_now(),
            },
        }

    async def admin_qa_queue_status(self) -> dict[str, Any]:
        """返回固定 QA 队列状态。"""
        return {"ok": True, "data": {"running": 0, "queued": 0, "cancelled": 0}}

    async def admin_cancel_qa_job(self, job_id: str) -> dict[str, Any]:
        """模拟管理员取消 QA 任务。"""
        return {"ok": True, "data": {"job_id": job_id, "status": "cancelled"}}

    async def admin_get_stats(self) -> dict[str, Any]:
        """返回固定管理员统计数据。"""
        return {
            "ok": True,
            "data": {
                "total_sessions": 1,
                "total_messages": 2,
                "today_sessions": 1,
                "today_messages": 2,
            },
        }


class DeterministicA2AService:
    """支持聊天/A2A 路由的确定性测试桩。"""

    def __init__(self, *, agent_name: str, answer_prefix: str) -> None:
        """初始化确定性 A2A agent 名称、回答前缀和内存任务表。"""
        self._agent_name = agent_name
        self._answer_prefix = answer_prefix
        self._counter = itertools.count(1)
        self._tasks: dict[str, dict[str, Any]] = {}

    def _build_task(
        self, *, task_id: str, session_id: str, user_id: str, question: str
    ) -> dict[str, Any]:
        """构造一个已完成的 A2A task 快照。"""
        final_answer = f"{self._answer_prefix}{question}".strip()
        artifacts = [
            {"name": "final_answer", "parts": [{"kind": "text", "text": final_answer}]},
            {
                "name": "processing_steps",
                "parts": [
                    {
                        "kind": "data",
                        "data": [
                            "主控分析问题",
                            "调用图谱代理",
                            "调用检索代理",
                            "生成最终答案",
                        ],
                    }
                ],
            },
            {
                "name": "tool_observations",
                "parts": [
                    {
                        "kind": "data",
                        "data": [
                            {
                                "agent": "kg",
                                "tool_name": "kg_entity_relation_search",
                                "subquery": question,
                                "result": {
                                    "ok": True,
                                    "result": {"summary": "命中高血压与危险因素关系"},
                                },
                            },
                            {
                                "agent": "rag",
                                "tool_name": "rag_search",
                                "subquery": question,
                                "result": {
                                    "ok": True,
                                    "result": {"summary": "命中文档证据 2 条"},
                                },
                            },
                        ],
                    }
                ],
            },
            {
                "name": "telemetry",
                "parts": [
                    {
                        "kind": "data",
                        "data": {
                            "controller_mode": "explicit_memory_with_subagents",
                            "llm_calls": 2,
                            "tool_calls": 2,
                            "retrieval_round": 1,
                            "compression_count": 1,
                        },
                    }
                ],
            },
            {
                "name": "evidence_summary",
                "parts": [
                    {
                        "kind": "data",
                        "data": {
                            "supported_points": ["建议监测血压", "注意限盐饮食"],
                            "evidence_count": 2,
                        },
                    }
                ],
            },
        ]
        return {
            "id": task_id,
            "contextId": session_id,
            "metadata": {"user_id": user_id},
            "status": {
                "state": "completed",
                "message": {"parts": [{"kind": "text", "text": "task_completed"}]},
            },
            "artifacts": artifacts,
        }

    def get_agent_card(self) -> dict[str, Any]:
        """返回固定 agent card。"""
        return {"name": self._agent_name, "protocolVersion": "0.2.5"}

    async def asend_message(
        self, message: dict[str, Any], request_id: str
    ) -> dict[str, Any]:
        """创建确定性已完成任务并返回 task id。"""
        _ = request_id
        task_id = f"task-{next(self._counter)}"
        session_id = str(message.get("contextId") or "session-1")
        metadata_value = message.get("metadata")
        metadata = metadata_value if isinstance(metadata_value, dict) else {}
        user_id = str(message.get("userId") or metadata.get("user_id") or "user-test")
        parts = message.get("parts") if isinstance(message.get("parts"), list) else []
        question = str(
            parts[0].get("text") if parts and isinstance(parts[0], dict) else ""
        )
        self._tasks[task_id] = self._build_task(
            task_id=task_id, session_id=session_id, user_id=user_id, question=question
        )
        return {"id": task_id}

    async def ahandle_rpc(self, data: dict[str, Any], trace_id: str) -> dict[str, Any]:
        """回显 JSON-RPC trace id。"""
        return {
            "jsonrpc": "2.0",
            "id": data.get("id"),
            "result": {"trace_id": trace_id},
        }

    async def alist_tasks(
        self, context_id: str = "", limit: int = 20
    ) -> list[dict[str, Any]]:
        """列出内存态 A2A 任务。"""
        rows = list(self._tasks.values())
        if context_id:
            rows = [
                row for row in rows if str(row.get("contextId") or "") == context_id
            ]
        return rows[:limit]

    async def aget_task(self, task_id: str) -> dict[str, Any] | None:
        """读取指定 A2A 任务。"""
        return self._tasks.get(task_id)

    async def aget_task_updates(
        self, task_id: str, cursor: int
    ) -> dict[str, Any] | None:
        """返回固定 task update 流片段。"""
        task = self._tasks.get(task_id)
        if task is None:
            return None
        events: list[dict[str, Any]] = []
        if cursor == 0:
            final_answer = str(task["artifacts"][0]["parts"][0]["text"])
            events = [
                {"event": "orchestrator_start", "detail": "主控开始分析"},
                {"event": "subagent_search", "detail": "调用图谱与检索代理"},
                {"event": "answer_delta", "metadata": {"delta": final_answer}},
            ]
        return {
            "events": events,
            "next_cursor": cursor + 1,
            "state": "completed",
            "terminal": True,
            "task": task,
        }

    async def acancel_task(self, task_id: str, reason: str) -> dict[str, Any]:
        """将内存态 A2A 任务标记为取消。"""
        task = self._tasks.get(task_id)
        if task is None:
            return {"ok": False, "error": "task_not_found"}
        task["status"] = {
            "state": "canceled",
            "message": {"parts": [{"kind": "text", "text": reason or "cancelled"}]},
        }
        return {"ok": True, "task": task}

    async def ashutdown(self) -> None:
        """测试桩无需释放资源。"""
        return None


@dataclass
class DeterministicGraphState:
    next_node_id: int = 300
    next_rel_id: int = 900
    next_job_id: int = 1
    nodes: dict[int, dict[str, Any]] = field(
        default_factory=lambda: {
            100: {
                "id": 100,
                "labels": ["Disease"],
                "properties": {"name": "高血压", "code": "I10"},
            },
            101: {
                "id": 101,
                "labels": ["Drug"],
                "properties": {"name": "缬沙坦", "code": "ARB"},
            },
        }
    )
    relationships: dict[int, dict[str, Any]] = field(
        default_factory=lambda: {
            200: {
                "rel_id": 200,
                "rel_type": "TREATS",
                "source_id": 101,
                "target_id": 100,
                "rel_props": {"evidence": "guideline"},
            }
        }
    )
    jobs: dict[str, dict[str, Any]] = field(default_factory=dict)

    def create_job(self, job_type: str, progress_text: str) -> dict[str, Any]:
        """创建一个已成功的图谱后台任务快照。"""
        job_id = f"graph-job-{self.next_job_id}"
        self.next_job_id += 1
        job = {
            "job_id": job_id,
            "job_type": job_type,
            "status": "succeeded",
            "progress_text": progress_text,
            "created_at": "2026-04-23T10:00:00Z",
            "started_at": "2026-04-23T10:00:01Z",
            "finished_at": "2026-04-23T10:00:02Z",
            "result": {"message": progress_text},
        }
        self.jobs[job_id] = job
        return job


class DeterministicGraphPublicService:
    """确定性公开图谱测试服务。"""

    def __init__(self, state: DeterministicGraphState) -> None:
        """注入内存态图谱状态。"""
        self._state = state

    async def graph_visualization(
        self, keyword: str = "", limit: int = 200
    ) -> dict[str, Any]:
        """返回固定图谱可视化数据。"""
        nodes = list(self._state.nodes.values())
        if keyword:
            nodes = [
                node
                for node in nodes
                if keyword in str(node["properties"].get("name") or "")
                or keyword in str(node["properties"].get("test_run_id") or "")
            ]
        node_ids = {node["id"] for node in nodes[:limit]}
        links = [
            {
                "id": str(rel["rel_id"]),
                "source": rel["source_id"],
                "target": rel["target_id"],
                "label": rel["rel_type"],
                "rel_type": rel["rel_type"],
                "rel_props": dict(rel.get("rel_props") or {}),
            }
            for rel in self._state.relationships.values()
            if rel["source_id"] in node_ids and rel["target_id"] in node_ids
        ]
        return {
            "ok": True,
            "nodes": [_public_node(node) for node in nodes[:limit]],
            "links": links,
            "count": len(links),
        }

    async def get_entity_detail(self, entity_id: str) -> dict[str, Any]:
        """返回固定图谱实体详情和相邻关系。"""
        node = self._state.nodes.get(int(entity_id))
        if node is None:
            return {"ok": False, "error": "实体不存在"}
        incoming = [
            {
                "source_id": rel["source_id"],
                "source_name": self._state.nodes[rel["source_id"]]["properties"].get(
                    "name"
                ),
                "source_labels": self._state.nodes[rel["source_id"]]["labels"],
                "rel_type": rel["rel_type"],
                "rel_props": rel["rel_props"],
            }
            for rel in self._state.relationships.values()
            if rel["target_id"] == node["id"]
        ]
        outgoing = [
            {
                "target_id": rel["target_id"],
                "target_name": self._state.nodes[rel["target_id"]]["properties"].get(
                    "name"
                ),
                "target_labels": self._state.nodes[rel["target_id"]]["labels"],
                "rel_type": rel["rel_type"],
                "rel_props": rel["rel_props"],
            }
            for rel in self._state.relationships.values()
            if rel["source_id"] == node["id"]
        ]
        public_node = _public_node(node)
        return {
            "ok": True,
            "entity": public_node,
            "relationships": {"incoming": incoming, "outgoing": outgoing},
        }


class DeterministicGraphAdminService:
    """确定性管理员图谱测试服务。"""

    def __init__(self, state: DeterministicGraphState) -> None:
        """注入内存态图谱状态。"""
        self._state = state

    async def get_graph_labels(self) -> dict[str, Any]:
        """返回测试图谱节点标签。"""
        labels = sorted(
            {label for node in self._state.nodes.values() for label in node["labels"]}
        )
        return {"ok": True, "data": labels}

    async def get_graph_relationship_types(self) -> dict[str, Any]:
        """返回测试图谱关系类型。"""
        rel_types = sorted(
            {rel["rel_type"] for rel in self._state.relationships.values()}
        )
        return {"ok": True, "data": rel_types}

    async def get_graph_nodes(
        self, label: str = "", keyword: str = "", limit: int = 100, offset: int = 0
    ) -> dict[str, Any]:
        """按标签、关键词和分页参数返回测试节点。"""
        rows = list(self._state.nodes.values())
        if label:
            rows = [row for row in rows if label in row["labels"]]
        if keyword:
            rows = [
                row
                for row in rows
                if keyword in str(row["properties"].get("name") or "")
                or keyword in str(row["properties"].get("test_run_id") or "")
            ]
        sliced = rows[offset : offset + limit]
        return {
            "ok": True,
            "data": [_public_node(row) for row in sliced],
            "total": len(rows),
        }

    async def get_graph_node(self, node_id: int) -> dict[str, Any]:
        """返回测试图谱节点详情。"""
        public_service = DeterministicGraphPublicService(self._state)
        detail = await public_service.get_entity_detail(str(node_id))
        if not detail.get("ok"):
            return detail
        return {
            "ok": True,
            "data": {
                "node": detail["entity"],
                "relationships": detail["relationships"],
            },
        }

    async def create_graph_node(self, data: dict[str, Any]) -> dict[str, Any]:
        """在内存态图谱中创建节点。"""
        label = str(data.get("label") or "").strip()
        if not label:
            return {"ok": False, "error": "节点标签不能为空"}
        node_id = self._state.next_node_id
        self._state.next_node_id += 1
        self._state.nodes[node_id] = {
            "id": node_id,
            "labels": [label],
            "properties": dict(data.get("properties") or {}),
        }
        return {"ok": True, "data": {"id": node_id, "message": "节点已创建。"}}

    async def update_graph_node(
        self, node_id: int, data: dict[str, Any]
    ) -> dict[str, Any]:
        """更新内存态图谱节点属性。"""
        node = self._state.nodes.get(node_id)
        if node is None:
            return {"ok": False, "error": "节点不存在"}
        node["properties"].update(dict(data.get("properties") or {}))
        return {"ok": True, "data": {"id": node_id, "message": "节点已更新。"}}

    async def delete_graph_node(self, node_id: int) -> dict[str, Any]:
        """删除内存态图谱节点及关联关系。"""
        if self._state.nodes.pop(node_id, None) is None:
            return {"ok": False, "error": "节点不存在"}
        self._state.relationships = {
            rel_id: rel
            for rel_id, rel in self._state.relationships.items()
            if rel["source_id"] != node_id and rel["target_id"] != node_id
        }
        return {"ok": True, "message": "节点已删除。"}

    async def create_graph_relationship(self, data: dict[str, Any]) -> dict[str, Any]:
        """在内存态图谱中创建关系。"""
        try:
            source_id_value = data.get("source_id")
            target_id_value = data.get("target_id")
            if source_id_value is None or target_id_value is None:
                raise ValueError("missing_node_id")
            source_id = int(source_id_value)
            target_id = int(target_id_value)
        except (TypeError, ValueError):
            return {"ok": False, "error": "节点 ID 必须为整数"}
        rel_id = self._state.next_rel_id
        self._state.next_rel_id += 1
        self._state.relationships[rel_id] = {
            "rel_id": rel_id,
            "rel_type": str(data.get("rel_type") or "RELATED_TO"),
            "source_id": source_id,
            "target_id": target_id,
            "rel_props": dict(data.get("properties") or {}),
        }
        return {"ok": True, "data": {"rel_id": rel_id, "message": "关系已创建。"}}

    async def delete_graph_relationship(self, rel_id: int) -> dict[str, Any]:
        """删除内存态图谱关系。"""
        if self._state.relationships.pop(rel_id, None) is None:
            return {"ok": False, "error": "关系不存在"}
        return {"ok": True, "message": "关系已删除。"}

    async def execute_cypher(self, data: dict[str, Any]) -> dict[str, Any]:
        """模拟只读 Cypher 执行。"""
        _ = data
        return {"ok": True, "data": [{"label": "Disease"}]}

    async def extract_and_import_graph(self, file: Any) -> dict[str, Any]:
        """兼容旧接口，提交图谱导入测试任务。"""
        return await self.submit_graph_extract_import_job(file)

    async def submit_graph_extract_import_job(self, file: Any) -> dict[str, Any]:
        """创建一个已成功的图谱导入测试任务。"""
        _ = file
        job = self._state.create_job("graph_extract_import", "图谱导入任务已提交。")
        return {"ok": True, "data": {**job, "message": "图谱导入任务已提交。"}}

    async def list_graph_jobs(self, limit: int = 10) -> dict[str, Any]:
        """列出最近图谱测试任务。"""
        jobs = list(self._state.jobs.values())[-limit:]
        jobs.reverse()
        return {"ok": True, "data": jobs}

    async def get_graph_job(self, job_id: str) -> dict[str, Any]:
        """读取指定图谱测试任务。"""
        job = self._state.jobs.get(job_id)
        if job is None:
            return {"ok": False, "error": "任务不存在"}
        return {"ok": True, "data": job}

    async def embed_graph_to_milvus(
        self, data: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """兼容旧接口，提交图谱向量化测试任务。"""
        return await self.submit_graph_embed_milvus_job(data)

    async def submit_graph_embed_milvus_job(
        self, data: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """创建一个已成功的图谱向量化测试任务。"""
        _ = data
        job = self._state.create_job("graph_embed_milvus", "图谱向量化任务已提交。")
        return {"ok": True, "data": {**job, "message": "图谱向量化任务已提交。"}}

    async def get_graph_embed_status(self) -> dict[str, Any]:
        """返回固定图谱向量库状态。"""
        return {
            "ok": True,
            "data": {
                "collection_name": "test_graph_collection",
                "entities_count": 2,
                "relationships_count": 1,
            },
        }


class DeterministicAdminDocumentsService:
    """确定性管理员文档测试服务。"""

    def __init__(self) -> None:
        """初始化内存态文档任务表。"""
        self._counter = itertools.count(1)
        self._jobs: dict[str, dict[str, Any]] = {}

    def _new_job(self, file_name: str) -> dict[str, Any]:
        """创建一个已成功的文本向量化测试任务。"""
        job_id = f"doc-job-{next(self._counter)}"
        job = {
            "job_id": job_id,
            "job_type": "text_vector_embed",
            "status": "succeeded",
            "progress_text": f"{file_name} 已完成向量化",
            "created_at": "2026-04-23T10:00:00Z",
            "started_at": "2026-04-23T10:00:01Z",
            "finished_at": "2026-04-23T10:00:02Z",
            "result": {"file_name": file_name, "message": "任务已提交到后台。"},
        }
        self._jobs[job_id] = job
        return job

    async def upload_document(self, file: Any) -> dict[str, Any]:
        """兼容旧接口，提交文本向量化测试任务。"""
        return await self.submit_text_embedding_job(file)

    async def submit_text_embedding_job(self, file: Any) -> dict[str, Any]:
        """根据上传文件名创建文本向量化测试任务。"""
        filename = str(getattr(file, "filename", "") or "document.txt")
        job = self._new_job(filename)
        return {"ok": True, "data": {**job, "message": "任务已提交到后台。"}}

    async def list_text_embedding_jobs(self, limit: int = 10) -> dict[str, Any]:
        """列出最近文本向量化测试任务。"""
        jobs = list(self._jobs.values())[-limit:]
        jobs.reverse()
        return {"ok": True, "data": jobs}

    async def get_text_embedding_job(self, job_id: str) -> dict[str, Any]:
        """读取指定文本向量化测试任务。"""
        job = self._jobs.get(job_id)
        if job is None:
            return {"ok": False, "error": "任务不存在"}
        return {"ok": True, "data": job}

    async def get_document_stats(self) -> dict[str, Any]:
        """返回固定文档向量库统计数据。"""
        return {
            "ok": True,
            "success": True,
            "collection_name": "test_documents_collection",
            "row_count": 12,
        }


def build_deterministic_test_overrides() -> BackendAppTestOverrides:
    """创建用于前端 E2E 和 app 装配测试的统一覆盖项。"""

    graph_state = DeterministicGraphState()
    session_state = DeterministicSessionState()
    memory_service = DeterministicSessionMemoryService()
    return BackendAppTestOverrides(
        startup_behavior=StartupRuntimeBehavior(
            initialize_database=False,
            check_databases=False,
            prewarm_retrieval_resources=False,
            bootstrap_mcp=True,
            bootstrap_a2a=True,
            close_neo4j_on_shutdown=False,
        ),
        mcp_server=DeterministicMcpServer(),
        a2a_service=DeterministicA2AService(
            agent_name="Hypertension Orchestrator Agent",
            answer_prefix="基于当前测试知识库，建议：",
        ),
        a2a_graph_service=DeterministicA2AService(
            agent_name="Hypertension Graph Agent", answer_prefix="图谱代理："
        ),
        a2a_retrieval_service=DeterministicA2AService(
            agent_name="Hypertension Retrieval Agent", answer_prefix="检索代理："
        ),
        session_memory_service=memory_service,
        readiness_service=DeterministicReadinessService(),
        a2a_event_stream_service=DeterministicA2AEventStreamService(),
        session_turn_service=DeterministicSessionTurnService(
            session_state, memory_service
        ),
        session_stream_service=DeterministicSessionStreamService(session_state),
        sessions_history_service=DeterministicSessionsHistoryService(session_state),
        admin_runtime_service=DeterministicAdminRuntimeService(),
        admin_sessions_service=DeterministicAdminSessionsService(
            session_state, memory_service
        ),
        admin_observability_service=DeterministicAdminObservabilityService(),
        graph_public_service=DeterministicGraphPublicService(graph_state),
        graph_admin_service=DeterministicGraphAdminService(graph_state),
        admin_documents_service=DeterministicAdminDocumentsService(),
    )
