# 高血压知识图谱问答系统 API 接口文档

本文档按当前代码实现整理，基于 [app/backend/routers](/D:/graduate/HypertensionKG_QA_System/app/backend/routers) 与对应 service 的真实行为。

## 接口概览

| 模块 | 接口数量 | 说明 |
|------|---------:|------|
| 问答与会话 | 3 | 核心问答入口 |
| A2A 协议 | 17 | Orchestrator / Graph / Retrieval 三组接口 |
| 会话历史 | 10 | 会话 CRUD、消息、回滚、上传、取消任务 |
| 图谱查询 | 2 | 公开图谱可视化与实体详情 |
| 系统与 MCP | 5 | 数据库状态、readiness、MCP 工具 |
| 管理员认证 | 3 | 登录、状态、登出 |
| 管理员文档 | 5 | 文档上传与文本嵌入后台任务 |
| 管理员图谱 | 17 | 图谱管理、后台任务、嵌入状态 |
| 管理员运行时 | 5 | flags、基线、队列、统计 |
| 管理员会话 | 7 | 会话、记忆、日志清理 |
| 管理员可观测性 | 2 | Agent 汇总与运行明细 |
| **总计** | **76** | |

## 通用说明

### 1. 认证

- 公开接口无需认证。
- 普通会话接口依赖 `user_id` 与 `session_id` 进行归属校验，很多接口如果缺 `user_id` 会直接返回 `user_id_required`。
- 管理员接口不是 `Session Cookie` 认证，而是：
  1. `POST /admin/login` 获取 `token`
  2. 后续请求头带 `Authorization: Bearer <token>`

### 2. 响应格式

本项目没有严格统一成单一响应 schema，常见有三类：

- 标准业务响应：

```json
{
  "ok": true,
  "data": {}
}
```

- 带额外字段响应：

```json
{
  "ok": true,
  "data": [],
  "total": 123
}
```

- 某些历史接口直接把数据放在顶层：

```json
{
  "ok": true,
  "message": "cancel_requested",
  "job_id": "xxx",
  "status": "running"
}
```

### 3. 流式接口

以下接口返回 `StreamingResponse(text/plain)`：

- `POST /process`
- `POST /process_with_session`
- `GET /a2a/tasks/{task_id}/events`
- `GET /a2a/graph/tasks/{task_id}/events`
- `GET /a2a/retrieval/tasks/{task_id}/events`

### 4. Readiness

- `GET /health/readiness` 在 `ready` / `degraded` 时返回 HTTP `200`
- 在 `unavailable` 时返回 HTTP `503`
- 核心问答接口在 `unavailable` 时会被阻断

---

## 一、问答接口

### 1. `POST /process`

基础流式问答入口。

请求体：

```json
{
  "input": "高血压患者如何用药？",
  "user_id": "user_123",
  "session_id": "session_456"
}
```

说明：

- `user_id` 为空时，后端会自动生成一个 UUID
- `session_id` 可为空
- 返回流式文本行

### 2. `POST /agent/run`

A2A 主控入口。

请求体：

```json
{
  "input": "ACEI 和 ARB 有什么区别？",
  "session_id": "session_456",
  "user_id": "user_123",
  "strategy": "iterative",
  "max_turns": 4
}
```

说明：

- `input` 不能为空，否则返回 `input_required`
- `strategy` 当前返回的能力说明里包含 `iterative` 与 `graph`
- 该接口受重型接口速率限制约束

### 3. `POST /process_with_session`

带会话保存的流式问答入口。

请求体：

```json
{
  "input": "高血压的诊断标准是什么？",
  "session_id": "session_456",
  "user_id": "user_123",
  "image": null,
  "file_name": ""
}
```

说明：

- 返回流式文本行
- 该接口会在进入主流程前检查会话合法性，并保存消息历史
- 该接口受重型接口速率限制约束

---

## 二、A2A 协议接口

### Orchestrator

- `GET /a2a/agent-card`
- `GET /a2a/agents/cards`
- `POST /a2a/rpc`
- `GET /a2a/tasks`
- `GET /a2a/tasks/{task_id}`
- `GET /a2a/tasks/{task_id}/events`
- `POST /a2a/tasks/{task_id}/cancel`

### Graph Agent

- `POST /a2a/graph/rpc`
- `GET /a2a/graph/tasks`
- `GET /a2a/graph/tasks/{task_id}`
- `GET /a2a/graph/tasks/{task_id}/events`
- `POST /a2a/graph/tasks/{task_id}/cancel`

### Retrieval Agent

- `POST /a2a/retrieval/rpc`
- `GET /a2a/retrieval/tasks`
- `GET /a2a/retrieval/tasks/{task_id}`
- `GET /a2a/retrieval/tasks/{task_id}/events`
- `POST /a2a/retrieval/tasks/{task_id}/cancel`

通用查询参数：

- `context_id`: 任务上下文
- `limit`: 列表数量，默认 `20`
- `cursor`: 事件流游标，默认 `0`
- `poll_ms`: 轮询间隔，默认 `500`
- `timeout_seconds`: 事件流超时，默认 `300`

说明：

- `/a2a/*/events` 为流式返回
- `/a2a/*/cancel` 可带可选 JSON body，但不是必填

---

## 三、会话历史接口

### 1. `POST /sessions`

创建新会话。

请求体：

```json
{
  "user_id": "user_123",
  "title": "高血压用药咨询"
}
```

说明：

- `user_id` 为空时会自动生成 UUID

### 2. `GET /sessions`

获取用户会话列表。

查询参数：

- `user_id`：必填
- `limit`：默认 `50`

缺少 `user_id` 时返回：

```json
{
  "ok": false,
  "error": "user_id_required",
  "data": []
}
```

### 3. `GET /sessions/{session_id}`

获取单个会话及其消息。

查询参数：

- `user_id`：必填

### 4. `PUT /sessions/{session_id}`

更新会话标题。

请求体：

```json
{
  "user_id": "user_123",
  "title": "新的会话标题"
}
```

说明：

- `user_id` 必填
- `title` 必填

### 5. `DELETE /sessions/{session_id}`

删除会话。

查询参数：

- `user_id`：必填

### 6. `POST /sessions/{session_id}/messages`

向会话添加消息。

请求体：

```json
{
  "user_id": "user_123",
  "role": "user",
  "content": "高血压患者如何用药？",
  "think_content": ""
}
```

### 7. `GET /sessions/{session_id}/messages`

获取会话消息。

查询参数：

- `user_id`：必填

### 8. `POST /sessions/{session_id}/rollback-to-question`

回撤到指定问题。

请求体：

```json
{
  "user_id": "user_123",
  "message_id": 123
}
```

### 9. `POST /upload_file`

聊天附件上传入口。

请求：

- `multipart/form-data`
- 字段名：`file`

支持类型：

- 图片：`.jpg .jpeg .png .gif .webp .bmp`
- 文档：`.md .doc .docx .pdf .ppt .txt`

真实响应示例：

```json
{
  "ok": true,
  "data": {
    "filename": "uuid.pdf",
    "original_name": "指南.pdf",
    "url": "/uploads/uuid.pdf",
    "base64": "",
    "is_image": false
  }
}
```

说明：

- 如果是图片，`base64` 会返回 data URL
- 如果是文档，`base64` 为空字符串

### 10. `POST /qa/jobs/{job_id}/cancel`

兼容接口，用于取消 A2A 任务。

请求体：

```json
{
  "user_id": "user_123",
  "session_id": "session_456"
}
```

说明：

- `user_id` 和 `session_id` 都必填
- 实际会校验任务元数据是否属于当前用户/会话

---

## 四、公开图谱接口

### 1. `GET /graph/visualization`

获取图谱可视化数据。

查询参数：

- `keyword`：可选
- `limit`：默认 `200`，服务内部会收敛到 `10 ~ 500`

真实成功响应结构：

```json
{
  "ok": true,
  "nodes": [],
  "links": [],
  "count": 0
}
```

注意：

- 字段名是 `links`，不是 `edges`

### 2. `GET /graph/entity/{entity_id}`

获取实体详情与入边/出边关系。

成功响应结构：

```json
{
  "ok": true,
  "entity": {
    "id": "1",
    "labels": ["Disease"],
    "name": "高血压",
    "properties": {}
  },
  "relationships": {
    "incoming": [],
    "outgoing": []
  }
}
```

---

## 五、系统与 MCP 接口

### 1. `GET /db/status`

返回 Neo4j / Milvus 端口可达性检查结果。

注意：

- 这里不返回 MySQL 状态
- 返回的是结构化对象，不是 `"connected"` 这种字符串

典型响应：

```json
{
  "all_running": false,
  "neo4j": {
    "name": "Neo4j",
    "host": "127.0.0.1",
    "bolt_port": 7687,
    "bolt_ok": true,
    "is_running": true
  },
  "milvus": {
    "name": "Milvus",
    "host": "127.0.0.1",
    "port": 19531,
    "is_running": false
  },
  "message": "部分数据库未启动"
}
```

### 2. `GET /health/readiness`

返回服务就绪性报告。

成功响应：

```json
{
  "ok": true,
  "data": {
    "level": "ready",
    "ready": true,
    "checked_at": "2026-04-11 16:00:00",
    "critical_not_ready": [],
    "optional_issues": [],
    "dependencies": [],
    "db": {},
    "mcp": {},
    "runtime_flags": {}
  }
}
```

说明：

- `level` 取值：`ready` / `degraded` / `unavailable`
- 当 `level=unavailable` 时，HTTP 状态码为 `503`

### 3. `GET /agent/tools`

返回 Agent 策略与 MCP 工具信息。

返回字段：

- `default_strategy`
- `supported_strategies`
- `iterative_actions`
- `mcp_tools`

### 4. `GET /mcp/tools`

返回已注册 MCP 工具列表。

典型响应：

```json
{
  "ok": true,
  "tools": []
}
```

### 5. `POST /mcp/invoke`

调用 MCP 工具。

请求体：

```json
{
  "tool_name": "kg_query",
  "server_name": "local",
  "payload": {
    "keyword": "高血压 分级",
    "limit": 5
  },
  "user_id": "user_123",
  "session_id": "session_456"
}
```

说明：

- `tool_name` 必填
- `server_name` 可为空；为空时只用 `tool_name`
- 该接口受重型接口速率限制约束

---

## 六、管理员认证接口

### 1. `POST /admin/login`

请求体：

```json
{
  "username": "admin",
  "password": "xxx"
}
```

成功响应：

```json
{
  "ok": true,
  "message": "登录成功",
  "token": "..."
}
```

### 2. `GET /admin/status`

返回当前 Bearer Token 对应的登录状态。

### 3. `POST /admin/logout`

退出当前登录状态。

---

## 七、管理员文档接口

这组接口都需要：

```http
Authorization: Bearer <token>
```

### 1. `POST /admin/documents/upload`

兼容旧接口，本质上也是提交文本嵌入后台任务。

请求：

- `multipart/form-data`
- 字段：`file`

### 2. `GET /admin/documents/stats`

返回文本向量库统计信息。

### 3. `POST /admin/text-embedding/jobs/embed`

提交文本嵌入后台任务。

请求：

- `multipart/form-data`
- 字段：`file`

说明：

- 支持 `.pdf .docx .doc .pptx .ppt .html .htm .txt .md .jpg .jpeg .png`
- 文件上限 50MB

### 4. `GET /admin/text-embedding/jobs`

查询最近文本嵌入任务。

查询参数：

- `limit`：默认 `10`

### 5. `GET /admin/text-embedding/jobs/{job_id}`

查询单个文本嵌入任务状态。

---

## 八、管理员图谱接口

这组接口都需要管理员 Bearer Token。

### 图谱基础管理

- `GET /admin/graph/labels`
- `GET /admin/graph/relationship-types`
- `GET /admin/graph/nodes`
- `GET /admin/graph/nodes/{node_id}`
- `POST /admin/graph/nodes`
- `PUT /admin/graph/nodes/{node_id}`
- `DELETE /admin/graph/nodes/{node_id}`
- `POST /admin/graph/relationships`
- `DELETE /admin/graph/relationships/{rel_id}`
- `POST /admin/graph/execute-cypher`

### 图谱导入与嵌入

- `POST /admin/graph/extract-and-import`
- `POST /admin/graph/jobs/extract-import`
- `GET /admin/graph/jobs`
- `GET /admin/graph/jobs/{job_id}`
- `POST /admin/graph/embed-to-milvus`
- `POST /admin/graph/jobs/embed-milvus`
- `GET /admin/graph/embed-status`

### 关键说明

#### `POST /admin/graph/relationships`

真实字段名是：

```json
{
  "source_id": 1,
  "target_id": 2,
  "rel_type": "TREATS",
  "properties": {}
}
```

不是旧文档里的 `type`。

#### `POST /admin/graph/execute-cypher`

请求体：

```json
{
  "query": "MATCH (n) RETURN n LIMIT 10"
}
```

说明：

- 接口会禁止包含 `create / merge / delete / remove / set / drop / alter`

#### `POST /admin/graph/extract-and-import`

#### `POST /admin/graph/embed-to-milvus`

这两个接口名字看起来像“立即执行”，但当前实现实际上只是对应后台任务提交别名：

- `extract-and-import` -> 提交图谱抽取任务
- `embed-to-milvus` -> 提交图谱嵌入任务

调用方应该继续轮询：

- `GET /admin/graph/jobs/{job_id}`
- `GET /admin/graph/embed-status`

#### `GET /admin/graph/nodes`

查询参数：

- `label`
- `keyword`
- `limit`，默认 `100`
- `offset`，默认 `0`

响应除 `ok` / `data` 外，还会带 `total`

---

## 九、管理员运行时接口

### 1. `GET /admin/runtime/flags`

返回运行时 feature flags 快照。

### 2. `GET /admin/perf/baseline`

查询参数：

- `days`：默认 `1`，服务内部会限制到 `1 ~ 7`

返回内容包括：

- `api_latency`
- `event_loop_lag_ms`
- `queue`
- `runtime_flags`
- `active_threads`

### 3. `GET /admin/qa/queue/status`

返回 A2A 运行时队列快照。

### 4. `POST /admin/qa/jobs/{job_id}/cancel`

管理员取消 QA 任务，不需要再传 `user_id` / `session_id`。

### 5. `GET /admin/stats`

返回：

- `total_sessions`
- `total_messages`
- `today_sessions`
- `today_messages`

---

## 十、管理员会话接口

### 1. `GET /admin/sessions`

查询参数：

- `limit`：默认 `100`
- `offset`：默认 `0`

响应会额外包含 `total`

### 2. `GET /admin/sessions/{session_id}/messages`

返回结构：

```json
{
  "ok": true,
  "session": {},
  "messages": []
}
```

### 3. `GET /admin/sessions/{session_id}/observability`

返回会话级 turn 观测详情。

返回结构：

```json
{
  "ok": true,
  "session": {},
  "turns": []
}
```

### 4. `GET /admin/sessions/{session_id}/memory`

返回指定会话的记忆快照。

### 5. `POST /admin/sessions/{session_id}/memory/rebuild`

清空并重建该会话记忆。

### 6. `DELETE /admin/sessions/{session_id}/messages`

清空消息历史，同时尝试：

- 清空会话记忆
- 删除 `logs/sessions/<session_id>` 下的日志产物

响应可能包含：

- `removed_count`
- `memory_cleared`
- `log_artifacts_deleted`

### 7. `DELETE /admin/sessions/{session_id}`

删除会话，并尽量同步清理记忆与日志目录。

---

## 十一、管理员可观测性接口

### 1. `GET /admin/agent/observability-summary`

查询参数：

- `limit`：默认 `200`

返回聚合指标，例如：

- `total_runs`
- `avg_duration_ms`
- `avg_execution_tool_success_rate`
- `avg_attempted_action_success_rate`
- `avg_tool_success_rate`
- `total_tokens`
- `total_llm_calls`

### 2. `GET /admin/agent/observability-runs`

查询参数：

- `limit`：默认 `50`

返回最近运行明细列表，以及 `count`。

---

## 错误码与常见错误文本

以下错误文本在当前代码里比较常见：

- `user_id_required`
- `title_required`
- `session_not_found`
- `role_and_content_required`
- `message_id_required`
- `user_id_and_session_id_required`
- `tool_name_required`
- `job_not_found`
- `job_not_found_or_finished`
- `service_unavailable`
- `invalid_entity_id:*`
- `graph_query_error:*`
- `mcp_runtime_error:*`

---

## 代码参考

- 路由入口：[/app/backend/routers](/D:/graduate/HypertensionKG_QA_System/app/backend/routers)
- 会话接口实现：[/app/backend/services/sessions_history_service.py](/D:/graduate/HypertensionKG_QA_System/app/backend/services/sessions_history_service.py)
- 系统与 MCP：[/app/backend/routers/system_mcp.py](/D:/graduate/HypertensionKG_QA_System/app/backend/routers/system_mcp.py)
- 管理员图谱：[/app/backend/services/graph_admin_service.py](/D:/graduate/HypertensionKG_QA_System/app/backend/services/graph_admin_service.py)
- 管理员文档：[/app/backend/services/admin_documents_service.py](/D:/graduate/HypertensionKG_QA_System/app/backend/services/admin_documents_service.py)
- 可观测性：[/app/backend/services/admin_observability_service.py](/D:/graduate/HypertensionKG_QA_System/app/backend/services/admin_observability_service.py)
