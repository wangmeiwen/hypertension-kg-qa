# 高血压知识图谱问答系统 API 接口文档

## 接口概览

| 模块 | 接口数量 | 说明 |
|------|---------|------|
| 问答与会话 | 3 | 核心问答流程、Agent 运行 |
| A2A 协议 | 7 | Agent-to-Agent 协议接口 |
| 会话历史 | 12 | 会话 CRUD、消息管理、文件上传 |
| 图谱查询 | 2 | 公开图谱可视化、实体详情 |
| 系统与 MCP | 5 | 健康检查、工具调用 |
| 管理员认证 | 3 | 登录、登出、状态检查 |
| 管理员文档 | 4 | 文档上传、解析、嵌入 |
| 管理员图谱 | 16 | 图谱管理、Cypher 执行、任务管理 |
| 管理员运行时 | 5 | 性能监控、任务队列 |
| 管理员会话 | 7 | 会话管理、记忆重建 |
| 管理员可观测性 | 2 | Agent 运行监控 |
| **总计** | **66** | |

---

## 一、问答与会话接口

### 1.1 流式问答处理

**POST** `/process`

处理流式问答请求入口。

**请求参数**:
```json
{
  "input": "高血压患者如何用药？",
  "user_id": "user_123",
  "session_id": "session_456"
}
```

**响应**: `StreamingResponse` (text/plain)

---

### 1.2 Agent 运行

**POST** `/agent/run`

运行 Agent（A2A 架构）。

**请求参数**:
```json
{
  "input": "ACEI和ARB有什么区别？",
  "session_id": "session_456",
  "user_id": "user_123",
  "strategy": "iterative",
  "max_turns": 10
}
```

**响应**:
```json
{
  "ok": true,
  "result": {...},
  "trace_id": "trace_xxx"
}
```

---

### 1.3 带会话的问答处理

**POST** `/process_with_session`

处理问答并保存到会话历史。

**请求参数**:
```json
{
  "input": "高血压的诊断标准是什么？",
  "session_id": "session_456",
  "user_id": "user_123",
  "image": null,
  "file_name": ""
}
```

**响应**: `StreamingResponse`

---

## 二、A2A 协议接口

### 2.1 获取 Agent Card

**GET** `/a2a/agent-card`

返回 A2A Agent Card。

**响应**:
```json
{
  "ok": true,
  "agent_card": {...},
  "trace_id": "trace_xxx"
}
```

---

### 2.2 获取所有 Agent Cards

**GET** `/a2a/agents/cards`

返回当前可发现的 Agent Card 列表。

---

### 2.3 A2A RPC 调用

**POST** `/a2a/rpc`

执行 A2A RPC 流程。

---

### 2.4 列出任务

**GET** `/a2a/tasks`

**查询参数**:
- `context_id`: 上下文标识
- `limit`: 数量限制 (默认 20)

---

### 2.5 获取任务详情

**GET** `/a2a/tasks/{task_id}`

**路径参数**:
- `task_id`: 任务标识

---

### 2.6 获取任务事件流

**GET** `/a2a/tasks/{task_id}/events`

**路径参数**:
- `task_id`: 任务标识

**查询参数**:
- `cursor`: 游标位置 (默认 0)
- `poll_ms`: 轮询间隔 (默认 500ms)
- `timeout_seconds`: 超时时间 (默认 300s)

---

### 2.7 取消任务

**POST** `/a2a/tasks/{task_id}/cancel`

**路径参数**:
- `task_id`: 任务标识

---

## 三、会话历史接口

### 3.1 创建会话

**POST** `/sessions`

**请求参数**:
```json
{
  "user_id": "user_123",
  "title": "高血压用药咨询"
}
```

---

### 3.2 获取会话列表

**GET** `/sessions`

**查询参数**:
- `user_id`: 用户标识
- `limit`: 数量限制 (默认 50)

---

### 3.3 获取会话详情

**GET** `/sessions/{session_id}`

---

### 3.4 更新会话

**PUT** `/sessions/{session_id}`

**请求参数**:
```json
{
  "title": "新的会话标题"
}
```

---

### 3.5 删除会话

**DELETE** `/sessions/{session_id}`

---

### 3.6 添加消息

**POST** `/sessions/{session_id}/messages`

**请求参数**:
```json
{
  "role": "user",
  "content": "高血压患者如何用药？"
}
```

---

### 3.7 获取会话消息

**GET** `/sessions/{session_id}/messages`

**查询参数**:
- `user_id`: 用户标识

---

### 3.8 回撤最后一个问题

**POST** `/sessions/{session_id}/rollback-last-question`

---

### 3.9 回撤到指定问题

**POST** `/sessions/{session_id}/rollback-to-question`

**请求参数**:
```json
{
  "message_id": 123
}
```

---

### 3.10 上传图片/文件

**POST** `/upload_image`
**POST** `/upload_file`

**请求**: `multipart/form-data`
- `file`: 上传的文件

**响应**:
```json
{
  "ok": true,
  "url": "https://...",
  "file_name": "xxx.jpg"
}
```

---

### 3.11 取消 QA 任务

**POST** `/qa/jobs/{job_id}/cancel`

---

## 四、图谱查询接口（公开）

### 4.1 图谱可视化

**GET** `/graph/visualization`

**查询参数**:
- `keyword`: 关键词过滤
- `limit`: 节点数量限制 (默认 200)

**响应**:
```json
{
  "nodes": [...],
  "edges": [...]
}
```

---

### 4.2 获取实体详情

**GET** `/graph/entity/{entity_id}`

**路径参数**:
- `entity_id`: 实体标识

---

## 五、系统与 MCP 接口

### 5.1 数据库状态

**GET** `/db/status`

**响应**:
```json
{
  "neo4j": "connected",
  "milvus": "connected",
  "mysql": "connected"
}
```

---

### 5.2 健康检查

**GET** `/health/readiness`

**响应**:
```json
{
  "ok": true,
  "data": {
    "level": "ready",
    "ready": true,
    "checks": {...}
  }
}
```

---

### 5.3 获取 Agent 工具

**GET** `/agent/tools`

返回 Agent 策略与可用工具信息。

---

### 5.4 MCP 工具列表

**GET** `/mcp/tools`

返回 MCP 服务中注册的工具列表。

---

### 5.5 MCP 工具调用

**POST** `/mcp/invoke`

**请求参数**:
```json
{
  "tool_name": "kg_query",
  "server_name": "default",
  "payload": {...},
  "user_id": "user_123",
  "session_id": "session_456"
}
```

---

## 六、管理员认证接口

> **注意**: 以下管理员接口需要登录认证

### 6.1 管理员登录

**POST** `/admin/login`

**请求参数**:
```json
{
  "username": "admin",
  "password": "xxx"
}
```

---

### 6.2 检查管理员状态

**GET** `/admin/status`

---

### 6.3 管理员登出

**POST** `/admin/logout`

---

## 七、管理员文档接口

### 7.1 上传文档

**POST** `/admin/documents/upload`

上传 DOCX 文档并嵌入向量库。

**请求**: `multipart/form-data`
- `file`: DOCX 文件

---

### 7.2 获取文档统计

**GET** `/admin/documents/stats`

获取文档向量库统计信息。

---

### 7.3 Docling 解析文档

**POST** `/admin/docling/parse`

使用 Docling 解析文档。

---

### 7.4 Docling 解析并嵌入

**POST** `/admin/docling/parse-and-embed`

Docling 解析并嵌入向量库。

---

## 八、管理员图谱接口

### 8.1 获取节点标签

**GET** `/admin/graph/labels`

获取所有节点标签。

---

### 8.2 获取关系类型

**GET** `/admin/graph/relationship-types`

获取所有关系类型。

---

### 8.3 获取节点列表

**GET** `/admin/graph/nodes`

**查询参数**:
- `label`: 节点标签过滤
- `keyword`: 关键词过滤
- `limit`: 数量限制 (默认 100)
- `offset`: 偏移量 (默认 0)

---

### 8.4 获取节点详情

**GET** `/admin/graph/nodes/{node_id}`

**路径参数**:
- `node_id`: 节点 ID

---

### 8.5 创建节点

**POST** `/admin/graph/nodes`

**请求参数**:
```json
{
  "label": "Drug",
  "properties": {
    "name": "氨氯地平",
    "category": "CCB"
  }
}
```

---

### 8.6 更新节点

**PUT** `/admin/graph/nodes/{node_id}`

---

### 8.7 删除节点

**DELETE** `/admin/graph/nodes/{node_id}`

---

### 8.8 创建关系

**POST** `/admin/graph/relationships`

**请求参数**:
```json
{
  "source_id": 1,
  "target_id": 2,
  "type": "TREATS",
  "properties": {}
}
```

---

### 8.9 删除关系

**DELETE** `/admin/graph/relationships/{rel_id}`

---

### 8.10 执行 Cypher

**POST** `/admin/graph/execute-cypher`

执行只读 Cypher 查询。

**请求参数**:
```json
{
  "query": "MATCH (n:Drug) RETURN n LIMIT 10"
}
```

---

### 8.11 抽取并导入图谱

**POST** `/admin/graph/extract-and-import`

从上传文件抽取并导入图谱。

---

### 8.12 提交抽取导入任务

**POST** `/admin/graph/jobs/extract-import`

提交文件抽取入图谱后台任务。

---

### 8.13 查询图谱任务列表

**GET** `/admin/graph/jobs`

**查询参数**:
- `limit`: 数量限制 (默认 10)

---

### 8.14 查询图谱任务状态

**GET** `/admin/graph/jobs/{job_id}`

---

### 8.15 嵌入图谱到 Milvus

**POST** `/admin/graph/embed-to-milvus`

将知识图谱嵌入到 Milvus 向量库。

---

### 8.16 提交嵌入任务

**POST** `/admin/graph/jobs/embed-milvus`

提交图谱嵌入后台任务。

---

### 8.17 获取嵌入状态

**GET** `/admin/graph/embed-status`

获取知识图谱嵌入状态。

---

## 九、管理员运行时接口

### 9.1 查看运行时特性开关

**GET** `/admin/runtime/flags`

---

### 9.2 性能基线报告

**GET** `/admin/perf/baseline`

**查询参数**:
- `days`: 天数 (默认 1)

---

### 9.3 QA 队列状态

**GET** `/admin/qa/queue/status`

获取 A2A 任务状态快照。

---

### 9.4 取消 QA 任务

**POST** `/admin/qa/jobs/{job_id}/cancel`

---

### 9.5 获取统计数据

**GET** `/admin/stats`

---

## 十、管理员会话接口

### 10.1 获取所有会话

**GET** `/admin/sessions`

**查询参数**:
- `limit`: 数量限制 (默认 100)
- `offset`: 偏移量 (默认 0)

---

### 10.2 获取会话消息

**GET** `/admin/sessions/{session_id}/messages`

---

### 10.3 获取会话观测详情

**GET** `/admin/sessions/{session_id}/observability`

获取指定会话的按轮次观测详情。

---

### 10.4 获取会话记忆

**GET** `/admin/sessions/{session_id}/memory`

获取指定会话记忆快照。

---

### 10.5 重建会话记忆

**POST** `/admin/sessions/{session_id}/memory/rebuild`

重建指定会话记忆。

---

### 10.6 清空会话消息

**DELETE** `/admin/sessions/{session_id}/messages`

清空指定会话历史消息。

---

### 10.7 删除会话

**DELETE** `/admin/sessions/{session_id}`

---

## 十一、管理员可观测性接口

### 11.1 Agent 观测汇总

**GET** `/admin/agent/observability-summary`

**查询参数**:
- `limit`: 数量限制 (默认 200)

---

### 11.2 Agent 运行明细

**GET** `/admin/agent/observability-runs`

**查询参数**:
- `limit`: 数量限制 (默认 50)

---

## 通用响应格式

### 成功响应
```json
{
  "ok": true,
  "data": {...},
  "trace_id": "trace_xxx"
}
```

### 错误响应
```json
{
  "ok": false,
  "error": "error_message",
  "trace_id": "trace_xxx"
}
```

---

## 认证说明

- **公开接口**: 无需认证（如 `/graph/visualization`, `/health/readiness`）
- **用户接口**: 通过 `user_id` 和 `session_id` 标识
- **管理员接口**: 需要登录获取 Session Cookie

---

*文档生成时间: 2025年*
