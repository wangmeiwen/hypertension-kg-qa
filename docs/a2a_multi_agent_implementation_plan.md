# A2A 多 Agent 体系实施规划（仅规划，不改代码）

## 0. 文档目的

本规划用于将当前项目的多 Agent 能力从“进程内编排 + MCP 工具调用”升级为“基于 A2A 协议的可互操作 Agent 网络”。

本阶段只产出方案，不改任何业务代码。

适用范围：
- `app/backend/main.py`
- `app/agent/iterative_agent.py`
- `app/agent/graph_subagent.py`
- `app/agent/rag_subagent.py`
- `app/agent/evidence_evaluator.py`
- `app/mcp/server.py`

参考协议基线：A2A Specification（建议锁定到 `v0.2.5` 语义模型进行实现，后续再评估升级）。

---

## 1. 现状架构快照

### 1.1 现有调用链

当前主链路：
1. 前端调用 `/process`、`/agent/run` 或 `/process_with_session`。
2. 后端进入 `process_query(...)`。
3. 默认走 `IterativeAgent.run(...)`。
4. `IterativeAgent` 在回合内调度 `GraphSubAgent`、`RagSubAgent`。
5. 子 Agent 通过 `MCPServer.invoke_batch_json(...)` 触发 `kg_query`、`kg_entity_relation_search`、`rag_search`。
6. 最后由 `evidence_evaluator` 做门控，返回终答。

### 1.2 现状优点

- 已具备“主编排 + 专用子 Agent + 工具层”的清晰分层。
- 已有流式任务队列、取消检查、trace_id、token_usage、readiness 机制。
- MCP 层已具备超时、重试、熔断、并发调度能力。

### 1.3 现状限制

- 子 Agent 主要是进程内对象，缺少标准化跨服务互操作协议。
- 对外暴露的是业务 API，不是标准 Agent 协议接口。
- 多 Agent 生命周期管理（任务状态、重连、跨服务追踪）未协议化。
- 编排逻辑集中在 `IterativeAgent`，可演进但复用边界偏弱。

---

## 2. A2A 目标架构

### 2.1 目标原则

- 保留现有 MCP 工具投资，不推翻重来。
- A2A 负责 Agent 间协作；MCP 继续负责 Agent 内部工具调用。
- 双栈过渡：A2A 新链路与旧链路并行，支持灰度和回滚。
- 先“协议适配”再“服务拆分”，避免一次性重构风险。

### 2.2 目标角色划分

1. Orchestrator Agent（编排代理）
- 职责：接收用户任务、分解、路由、汇总、终答门控。
- 来源：`IterativeAgent` 能力演进。

2. Graph Agent（图谱代理）
- 职责：实体关系检索、图谱查询、图谱证据结构化输出。
- 来源：`GraphSubAgent + kg_query + kg_entity_relation_search`。

3. Retrieval Agent（语义检索代理）
- 职责：向量召回、RAG 证据整合。
- 来源：`RagSubAgent + rag_search`。

4. Safety/Evaluator Agent（证据门控代理）
- 职责：证据充分性、冲突检测、风险降级、置信度计算。
- 来源：`evidence_evaluator`。

### 2.3 协议分层

- A2A 层：Agent Discovery、任务状态、流式更新、跨 Agent 消息语义。
- Domain 层：医疗问答字段（session、risk、evidence、confidence）。
- MCP 层：底层工具执行（Neo4j/Milvus/向量检索）。

---

## 3. A2A 协议映射设计

## 3.1 Agent Card 映射

每个 Agent 暴露独立 Agent Card，至少声明：
- `protocolVersion`
- `name` / `description`
- `url`
- `capabilities.streaming`
- `defaultInputModes` / `defaultOutputModes`
- `skills`

建议技能声明：
- Orchestrator：`medical_qa_orchestration`
- Graph：`hypertension_graph_retrieval`
- Retrieval：`hypertension_semantic_retrieval`
- Evaluator：`medical_evidence_gating`

## 3.2 消息与任务映射

A2A `message/send` 输入映射：
- `message.parts[].text` <- 用户输入
- `message.contextId` <- `session_id`
- `message.metadata.user_id` <- `user_id`
- `message.metadata.trace_id` <- 请求 trace id
- `message.metadata.strategy/skill` <- 当前策略与技能

A2A `Task` 输出映射：
- `Task.status.state` <- running/completed/failed/canceled
- `Task.artifacts` <- 最终答案、工具观测、证据门控摘要
- `TaskStatus.message` <- 当前进展（用于前端增量显示）

## 3.3 流式与取消映射

- `message/stream` 对应当前流式输出与进度回调。
- `tasks/cancel` 对应当前 `cancel_check` + job manager 停止语义。
- `tasks/get` 对应长任务轮询。
- `tasks/resubscribe` 对应流中断重连（当前系统尚未标准化，需新增）。

## 3.4 错误模型映射

现有错误分类统一映射到：
- A2A JSON-RPC 标准错误
- 业务扩展错误码（如 `insufficient_evidence`、`dependency_unavailable`）

---

## 4. 迁移路线图（分阶段）

## Phase 0：设计冻结与契约优先（1 周）

交付物：
- A2A 字段映射表（本规划 + JSON Schema 草案）
- Agent Card 模板
- 任务状态机定义（状态、可转移关系、超时语义）

验收标准：
- 团队对字段和状态语义无歧义。
- 能产出最小互通样例（文档级）。

## Phase 1：A2A Adapter（进程内，不拆服务）（1-2 周）

目标：
- 在现有后端中引入 A2A 接口适配层。
- 不改变 `IterativeAgent` 主逻辑，只做协议壳。

动作：
- 新增 A2A 入口路由（JSON-RPC + SSE）。
- 将现有 `process_query` 包装为 A2A Task 执行器。
- 输出标准 Task 状态与 Artifact。

验收标准：
- 现有接口与 A2A 接口对同输入输出语义一致。
- P95 延迟回归可控（<= 10%）。

## Phase 2：子 Agent A2A 化（逻辑仍可同进程）（2 周）

目标：
- Graph/Retrieval/Evaluator 形成独立 A2A Agent 边界。

动作：
- 为三个子 Agent 各自提供 Agent Card 与技能声明。
- Orchestrator 改为通过 A2A Client 调 Graph/Retrieval/Evaluator。
- 内部仍可直接复用 MCP 与现有实现。

验收标准：
- 子 Agent 能独立被发现、被调用。
- Orchestrator 不再直接依赖子 Agent 类实现细节。

## Phase 3：任务治理与可观测增强（1-2 周）

目标：
- 把任务生命周期、重连、取消、失败恢复做全。

动作：
- 实现 `tasks/get`、`tasks/cancel`、`tasks/resubscribe`。
- 统一 Task 状态持久化（至少可查询最近任务）。
- 贯通跨 Agent trace_id / task_id / context_id。

验收标准：
- 流中断可恢复。
- 取消请求在全链路可见。
- 失败定位可追踪到具体 Agent/Skill。

## Phase 4：灰度切换与旧链路收敛（1 周）

目标：
- 平稳切换生产流量到 A2A 主链路。

动作：
- 引入开关：`ENABLE_A2A_ORCHESTRATION`。
- 先 10% 灰度，再全量。
- 保留回滚窗口。

验收标准：
- 正确率、稳定性、延迟满足既定阈值。
- 无 P0/P1 级事故。

---

## 5. 目录与模块规划（建议）

建议新增（仅规划）：
- `app/a2a/`
- `app/a2a/models.py`（Task/Message/Artifact 映射）
- `app/a2a/server.py`（A2A JSON-RPC + SSE 入口）
- `app/a2a/client.py`（A2A Client 封装）
- `app/a2a/cards/`（各 Agent Card 生成与校验）
- `app/a2a/agents/`（orchestrator/graph/retrieval/evaluator 的协议适配）

保留并复用：
- `app/mcp/*` 作为 Agent 内部工具执行层。
- `app/agent/*` 作为领域能力实现层。

---

## 6. 数据契约示例

## 6.1 Agent Card 示例（Graph Agent）

```json
{
  "protocolVersion": "0.2.5",
  "name": "Hypertension Graph Agent",
  "description": "图谱检索与关系推理代理",
  "url": "https://api.example.com/a2a/graph",
  "capabilities": {
    "streaming": true,
    "pushNotifications": false
  },
  "defaultInputModes": ["application/json", "text/plain"],
  "defaultOutputModes": ["application/json"],
  "skills": [
    {
      "id": "hypertension_graph_retrieval",
      "name": "Hypertension Graph Retrieval",
      "description": "执行知识图谱查询并返回实体关系证据"
    }
  ]
}
```

## 6.2 message/send 示例（Orchestrator）

```json
{
  "jsonrpc": "2.0",
  "id": "req-123",
  "method": "message/send",
  "params": {
    "message": {
      "messageId": "msg-1",
      "role": "user",
      "kind": "message",
      "contextId": "session-abc",
      "parts": [
        { "kind": "text", "text": "老年高血压患者血压目标是多少？" }
      ],
      "metadata": {
        "user_id": "u-001",
        "trace_id": "trace-xyz",
        "strategy": "iterative"
      }
    }
  }
}
```

## 6.3 Task 结果示例（简化）

```json
{
  "jsonrpc": "2.0",
  "id": "req-123",
  "result": {
    "id": "task-001",
    "status": {
      "state": "completed",
      "message": {
        "messageId": "msg-final",
        "role": "agent",
        "kind": "message",
        "parts": [
          { "kind": "text", "text": "...最终医学回答..." }
        ]
      }
    },
    "artifacts": [
      {
        "name": "evidence_summary",
        "parts": [
          {
            "kind": "data",
            "data": {
              "confidence": 0.78,
              "evidence_count": 12,
              "high_risk": false
            }
          }
        ]
      }
    ]
  }
}
```

---

## 7. 安全与合规规划

- 传输层：生产环境全链路 HTTPS。
- 认证：统一复用现有 token 体系，并在 A2A metadata 中透传用户身份上下文。
- 鉴权：按 Agent/Skill 维度做白名单授权。
- 审计：记录任务发起方、被调 Agent、技能、输入摘要、输出摘要、trace_id。
- 隐私：医疗内容脱敏日志策略复用现有 `session_logger` 规范。

---

## 8. 测试与验收规划

## 8.1 测试层次

1. 协议一致性测试
- Agent Card 必填字段校验
- JSON-RPC 请求/响应结构校验
- Task 状态机转移校验

2. 集成测试
- Orchestrator -> Graph/Retrieval/Evaluator 端到端
- 流式中断与 `tasks/resubscribe`
- 取消任务一致性

3. 回归测试
- 与旧 `/agent/run` 输出质量对比
- readiness 与降级行为不退化

## 8.2 关键指标

- 正确率：复杂问题通过率不低于现网基线。
- 稳定性：A2A 链路错误率 <= 旧链路 + 1%。
- 性能：P95 延迟增幅 <= 10%。
- 可观测：100% 请求带 trace_id/task_id。

---

## 9. 风险与缓解

风险 1：协议落地后字段语义分歧。
- 缓解：先做 schema 和契约测试，再上实现。

风险 2：A2A 封装带来额外延迟。
- 缓解：Phase 1 同进程适配，先不拆服务；先测再拆。

风险 3：流式/取消语义不一致。
- 缓解：统一 Task 状态机，并为取消引入可验证事件。

风险 4：一次性替换造成回归。
- 缓解：双栈灰度 + feature flag + 快速回滚。

---

## 10. 里程碑与人天预估

- M1（第 1 周）：契约冻结 + Agent Card 模板 + 状态机定义。
- M2（第 2-3 周）：A2A Adapter 上线（同进程双栈）。
- M3（第 4-5 周）：子 Agent A2A 化 + Orchestrator 远程化调用。
- M4（第 6 周）：观测完善、灰度、全量与收敛。

预估投入：
- 后端/协议开发：2 人 * 4~5 周
- 测试与压测：1 人 * 2 周
- 联调与灰度：1 人 * 1 周

---

## 11. 决策建议（先做什么）

建议按“低风险高收益”顺序推进：
1. 先做 Phase 0/1，把 A2A 协议壳搭起来。
2. 再做 Phase 2，把 Graph/RAG/Evaluator 变成可发现可调用的 Agent。
3. 最后做 Phase 3/4，把任务治理、灰度和回滚体系补齐。

这样可以在不推翻现有 MCP 与业务逻辑的前提下，逐步把体系升级为标准化多 Agent 网络。
