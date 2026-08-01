"""会话问答 JSONL 流事件契约。"""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal, Mapping, TypeAlias, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

STREAM_EVENT_SCHEMA_VERSION = "1.0.0"


class StreamEventModel(BaseModel):
    """所有公开流事件共享的严格校验规则。"""

    model_config = ConfigDict(extra="forbid")


class JobStreamEvent(StreamEventModel):
    """Agent 任务已创建。"""

    type: Literal["job"] = "job"
    job_id: str
    request_id: str
    task_id: str


class SessionStreamEvent(StreamEventModel):
    """本轮会话及用户消息已准备完成。"""

    type: Literal["session"] = "session"
    session_id: str
    user_message_id: int


ProgressStage: TypeAlias = Literal["orchestrator", "subagent", "answer"]
DoneStatus: TypeAlias = Literal["done", "failed", "cancelled"]


class ProgressStreamEvent(StreamEventModel):
    """可展示但不属于回答正文的进度事件。"""

    type: Literal["msg"] = "msg"
    msg: str
    stage: ProgressStage
    task_id: str


class ReplyStreamEvent(StreamEventModel):
    """回答正文增量。"""

    type: Literal["reply"] = "reply"
    delta: str = Field(min_length=1)
    task_id: str


class DoneStreamEvent(StreamEventModel):
    """任务终态元数据，不重复携带回答正文。"""

    type: Literal["done"] = "done"
    status: DoneStatus
    task_id: str
    traces: list[dict[str, Any]] = Field(default_factory=list)
    processing_steps: list[str] = Field(default_factory=list)
    tool_observations: list[dict[str, Any]] = Field(default_factory=list)
    evidence_summary: dict[str, Any] = Field(default_factory=dict)
    telemetry: dict[str, Any] = Field(default_factory=dict)


class ErrorStreamEvent(StreamEventModel):
    """流式任务无法继续时的显式终止事件。"""

    type: Literal["error"] = "error"
    code: str
    message: str
    task_id: str = ""
    recoverable: bool = False


class PersistedStreamEvent(StreamEventModel):
    """助手消息已持久化。"""

    type: Literal["persisted"] = "persisted"
    session_id: str
    user_message_id: int
    assistant_message_id: int


StreamEvent = Annotated[
    Union[
        JobStreamEvent,
        SessionStreamEvent,
        ProgressStreamEvent,
        ReplyStreamEvent,
        DoneStreamEvent,
        ErrorStreamEvent,
        PersistedStreamEvent,
    ],
    Field(discriminator="type"),
]

_STREAM_EVENT_ADAPTER: TypeAdapter[StreamEvent] = TypeAdapter(StreamEvent)


def validate_stream_event(event: StreamEvent | Mapping[str, Any]) -> StreamEvent:
    """在服务边界将流事件规整为强类型模型。"""
    if isinstance(event, StreamEventModel):
        return event
    return _STREAM_EVENT_ADAPTER.validate_python(event)


def stream_event_payload(event: StreamEvent | Mapping[str, Any]) -> dict[str, Any]:
    """返回可安全输出的流事件字典。"""
    return validate_stream_event(event).model_dump()


def stream_event_json_line(event: StreamEvent | Mapping[str, Any]) -> str:
    """将事件序列化为单行 UTF-8 JSON。"""
    return json.dumps(stream_event_payload(event), ensure_ascii=False) + "\n"


def stream_event_json_schema() -> dict[str, Any]:
    """返回可供客户端生成类型和校验版本的完整 JSON Schema。"""
    schema = _STREAM_EVENT_ADAPTER.json_schema()
    schema["$id"] = "/schemas/stream-events.json"
    schema["title"] = "Hypertension QA Stream Event"
    schema["x-schema-version"] = STREAM_EVENT_SCHEMA_VERSION
    return schema
