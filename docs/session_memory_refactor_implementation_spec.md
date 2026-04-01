# 高血压 KG QA 会话记忆重构详细实现方案

## 1. 文档目标

本文件用于指导实现模型直接落地高血压知识图谱问答项目的会话记忆重构。

目标不是在现有链路上“补一个摘要能力”，而是直接把当前多轮问答流程重构为一条新的、单一的、记忆驱动主链路，替换现在分散的：

- 历史消息持久化
- 未打通的数据库 session memory
- 规则式多轮追问理解
- 旧兼容路径

实现要求：

- 只保留一条新路线
- 不保留旧兼容分支
- 不增加 feature flag
- 不保留 legacy/off/双轨运行逻辑
- 只做 session 级记忆
- 持久化只使用 MySQL `session_memory` 表

模型配置上，只增加一个环境变量：

- `SESSION_MEMORY_MODEL_NAME`

若未设置，则自动回落到：

- `MODEL_NAME`

---

## 2. 最终架构

### 2.1 单次 `/process_with_session` 的固定处理流程

一次请求必须严格按以下顺序执行：

1. 用户消息写入 `messages`
2. 调用 `SessionMemoryService.ensure_memory_current(session_id, current_message_id)`，确保当前 session 的持久化记忆是最新的
3. 读取最近 3 到 4 轮消息、`summary_text`、`retrieval_summary_text`
4. 调用 `Resolver LLM`，将当前用户输入改写为结构化独立问题 `ResolvedQuery`
5. 将 `original_input_text`、`resolved_query`、`memory_context` 传入 A2A 主链路
6. orchestrator 只根据 `resolved_query` 做任务拆解、检索、调度和终答
7. 助手消息写入 `messages`
8. 调用 `Summarizer LLM`，将本轮新增消息和运行产物折叠回 `session_memory`

### 2.2 本次重构后的唯一记忆来源

运行时和持久化层统一约束如下：

- 运行时记忆来源只有 `session_memory.summary_text`
- 检索记忆来源只有 `session_memory.retrieval_summary_text`
- 进程内 `SessionMemory` 不再存在
- 历史消息 `messages` 只作为事实原始来源，不再直接承担“多轮理解器”的职责
- `Resolver LLM` 是唯一追问改写器
- `Summarizer LLM` 是唯一摘要更新器

---

## 3. 数据模型

### 3.1 持久化表

继续使用现有表：

- `session_memory`

字段不变：

- `session_id`
- `summary_text`
- `retrieval_summary_text`
- `created_at`
- `updated_at`

不新增列，不做数据库迁移。

### 3.2 `summary_text` 结构

`summary_text` 必须保存为 JSON 字符串，结构固定如下：

```json
{
  "version": 1,
  "last_message_id": 123,
  "conversation_goal": "用户正在询问老年高血压患者的联合用药建议",
  "confirmed_facts": [
    "用户明确关注老年高血压人群",
    "用户连续追问药物联合使用场景"
  ],
  "working_assumptions": [
    "当前问题可能默认围绕高血压指南语境，而非个体化处方"
  ],
  "answered_points": [
    "已回答 ACEI/ARB 联用相关一般结论"
  ],
  "open_questions": [
    "是否有特定并发症或肾功能状态限制"
  ],
  "risk_notes": [
    "不得将一般医学知识表述为患者个体诊疗建议"
  ]
}
```

### 3.3 `retrieval_summary_text` 结构

`retrieval_summary_text` 必须保存为 JSON 字符串，结构固定如下：

```json
{
  "version": 1,
  "last_message_id": 123,
  "focus_entities": [
    "老年高血压",
    "ACEI",
    "ARB"
  ],
  "covered_topics": [
    "联合用药一般原则",
    "证据来源定位"
  ],
  "evidence_gaps": [
    "特定人群限制条件",
    "明确禁忌或慎用场景"
  ],
  "recommended_queries": [
    "老年高血压 ACEI ARB 联合用药 指南",
    "ACEI ARB 联用 禁忌 高血压"
  ],
  "retrieval_risk_flags": [
    "存在高风险药物组合语义"
  ]
}
```

### 3.4 数据约束

两个 JSON 必须同时满足以下硬约束：

- `version` 必须为整数
- `last_message_id` 必须为整数
- 所有列表字段只允许 `string[]`
- 每个数组最多 8 项
- 每项最长 160 字符
- `conversation_goal` 最长 200 字符
- 空值统一转为空字符串或空数组
- 不允许未知字段参与运行时逻辑
- JSON 解析失败视为 memory 不可用，直接清空并走重建

### 3.5 医疗安全约束

以下规则必须固化到 summarizer prompt 和 sanitize 逻辑中：

- `confirmed_facts` 只能保存用户明确表达过的稳定会话事实，或本轮已有明确证据支持的稳定结论
- `working_assumptions` 只能保存待确认推断，禁止写成事实口吻
- 不记录患者身份类隐私
- 不记录密码、密钥、系统配置
- 不把模型猜测持久化为已确认事实
- 不把一般医学知识包装成个体化处方建议

---

## 4. 运行时类型

### 4.1 `ResolvedQuery`

新增运行时结构化对象 `ResolvedQuery`，固定字段如下：

```json
{
  "standalone_question": "对于老年高血压患者，ACEI 和 ARB 联合使用是否推荐？依据是什么？",
  "intent": "evidence_verification",
  "subqueries": [
    "老年高血压患者 ACEI 和 ARB 联合使用是否推荐",
    "相关指南或原文依据是什么"
  ],
  "must_cover": [
    "target_population",
    "intervention",
    "recommendation",
    "evidence_source"
  ],
  "entities": [
    "老年高血压",
    "ACEI",
    "ARB"
  ],
  "filters": [
    "指南语境",
    "治疗建议"
  ],
  "preferred_sources": [
    "kg",
    "rag"
  ],
  "carry_over_context": [
    "当前会话持续关注联合用药问题"
  ]
}
```

#### 字段语义

- `standalone_question`: 已补全上下文后的独立问题
- `intent`: 问题意图标签，供 orchestrator 参考
- `subqueries`: 拆分后的子问题列表
- `must_cover`: 回答必须覆盖的要点
- `entities`: 当前轮最重要实体
- `filters`: 适用范围、条件、限制
- `preferred_sources`: 建议优先使用 `kg`、`rag` 或混合
- `carry_over_context`: 从会话历史延续下来的必要上下文

### 4.2 `MemoryContext`

统一传递的运行时 memory 结构如下：

```json
{
  "summary": { "...": "..." },
  "retrieval_summary": { "...": "..." },
  "last_message_id": 123,
  "resolved_from_history": true,
  "recent_messages_digest": [
    "用户：那老年人呢",
    "助手：上一轮已回答一般联合用药原则"
  ]
}
```

### 4.3 `RuntimeMemoryPayload`

建议新增一个运行时返回对象，至少包含：

```json
{
  "original_input_text": "那老年人呢",
  "resolved_query": { "...": "..." },
  "memory_context": { "...": "..." }
}
```

---

## 5. 新增文件

### 5.1 `app/backend/services/session_memory_types.py`

职责：

- 定义统一数据结构
- 避免到处使用松散字典

建议定义：

- `SessionSummary`
- `RetrievalSummary`
- `SessionMemorySnapshot`
- `ResolvedQuery`
- `MemoryContext`
- `RuntimeMemoryPayload`

优先使用：

- `TypedDict`
- 或 `dataclass`

不要定义为无约束 `dict[str, Any]` 后在全项目四处散用。

### 5.2 `app/backend/services/session_memory_prompts.py`

职责：

- 存放双 LLM 的 system prompt
- 存放 JSON schema 描述
- 存放医疗安全约束文案

至少包含：

- `RESOLVE_QUERY_SYSTEM_PROMPT`
- `SUMMARIZE_TURN_SYSTEM_PROMPT`
- `RESOLVED_QUERY_SCHEMA_TEXT`
- `SESSION_SUMMARY_SCHEMA_TEXT`
- `RETRIEVAL_SUMMARY_SCHEMA_TEXT`

### 5.3 `app/backend/services/session_memory_llm.py`

职责：

- 封装 `Resolver LLM`
- 封装 `Summarizer LLM`
- 统一读取 `SESSION_MEMORY_MODEL_NAME`
- 统一 JSON 解析
- 统一低温度调用
- 统一失败兜底

建议暴露方法：

- `resolve_query(...) -> ResolvedQuery`
- `summarize_turn(...) -> SessionMemorySnapshot`

#### 实现要求

- 复用现有 `LLMClient`
- 不引入新 SDK
- 温度固定低值，例如 `0.1`
- 输出必须是 JSON
- 若 JSON 不可解析，则内部兜底，不把坏结果继续透传

### 5.4 `app/backend/services/session_memory_service.py`

这是本次重构的核心模块。

职责：

- 读取 memory
- 解析 memory
- sanitize memory
- 计算是否 stale
- 按消息增量重建 memory
- 请求前构造 `RuntimeMemoryPayload`
- 请求后更新 memory

必须提供的方法：

- `load_memory(session_id) -> SessionMemorySnapshot`
- `save_memory(session_id, snapshot) -> None`
- `clear_memory(session_id) -> None`
- `ensure_memory_current(session_id, current_message_id) -> SessionMemorySnapshot`
- `build_runtime_context(session_id, input_text, current_message_id) -> RuntimeMemoryPayload`
- `refresh_after_turn(session_id, assistant_message_id, turn_artifacts) -> None`
- `rebuild_from_messages(session_id) -> SessionMemorySnapshot`

建议私有方法：

- `_parse_summary(raw: str) -> SessionSummary`
- `_parse_retrieval_summary(raw: str) -> RetrievalSummary`
- `_sanitize_summary(data) -> SessionSummary`
- `_sanitize_retrieval_summary(data) -> RetrievalSummary`
- `_get_latest_message_id(session_id) -> int`
- `_get_messages_after(session_id, last_message_id) -> list[dict]`
- `_get_recent_messages(session_id, limit) -> list[dict]`
- `_build_recent_messages_digest(messages) -> list[str]`
- `_build_fallback_resolved_query(input_text) -> ResolvedQuery`

---

## 6. 修改文件

### 6.1 `app/common/config.py`

新增配置项：

- `SESSION_MEMORY_MODEL_NAME = os.getenv("SESSION_MEMORY_MODEL_NAME", self.MODEL_NAME)`

不新增任何 memory feature flag。

### 6.2 `app/backend/main.py`

#### 必须修改的函数

- `process_with_session`
- `generate_data_with_session`
- `_generate_a2a_stream_events`
- `_run_a2a_query`
- rollback 相关接口
- admin memory 接口

#### `process_with_session` 新逻辑

用户消息入库后必须立即：

1. 拿到 `user_message_id`
2. 调用 `SessionMemoryService.build_runtime_context(session_id, input_text, user_message_id)`
3. 拿到：
   - `original_input_text`
   - `resolved_query`
   - `memory_context`
4. 将这些内容传给 `generate_data_with_session`

#### `generate_data_with_session` 新增入参

- `original_input_text`
- `resolved_query`
- `memory_context`

在 assistant 消息落库后必须调用：

- `SessionMemoryService.refresh_after_turn(...)`

传入的 `turn_artifacts` 至少包括：

- `processing_steps`
- `tool_observations`
- `evidence_summary`
- `assistant_reply`

#### `_generate_a2a_stream_events` 新逻辑

构造 A2A message metadata 时新增：

- `original_input_text`
- `resolved_query`
- `memory_context`

#### `_run_a2a_query` 新逻辑

不能再只解析：

- `strategy`
- `max_turns`

还必须继续向下传：

- `original_input_text`
- `resolved_query`
- `memory_context`

#### rollback 接口要求

保留：

- 原消息删除逻辑
- `SessionMemoryManager.clear_summary(session_id)`

但必须明确：

- rollback 之后 memory 直接失效
- 不在 rollback 里做旧摘要复用
- 不在 rollback 里起兼容重算分支
- 下次请求前统一走 `ensure_memory_current`

#### 新增 admin 接口

新增：

- `GET /admin/sessions/{session_id}/memory`
- `POST /admin/sessions/{session_id}/memory/rebuild`

不新增用户侧 memory API。

### 6.3 `app/a2a/agent/__init__.py`

`process_query` 新增参数：

- `original_input_text: str = ""`
- `resolved_query: dict | None = None`
- `memory_context: dict | None = None`

并将其继续传给：

- `IterativeAgent.run`

### 6.4 `app/a2a/agent/iterative_agent.py`

`run` 新增参数：

- `original_input_text`
- `resolved_query`
- `memory_context`

原有 `strategy` 可保留参数名以减少上游改动，但不再承载旧多路径语义。

### 6.5 `app/a2a/orchestrator/executor.py`

初始化 `OrchestratorState` 时新增：

- `original_input_text`
- `resolved_query`
- `memory_context`

同时保证输出结构中能保留本轮 memory update 所需运行产物。

### 6.6 `app/a2a/orchestrator/state.py`

新增字段：

- `original_input_text: str`
- `resolved_query: dict[str, Any]`
- `memory_context: dict[str, Any]`

### 6.7 `app/a2a/orchestrator/nodes.py`

#### `node_parse_input`

新增职责：

- 标准化 `resolved_query`
- 标准化 `memory_context`
- 如果 `resolved_query` 缺失，则构造最小兜底对象

#### `node_decompose_and_plan`

必须改为：

- 不再直接读取 `input_text`
- 只读取 `resolved_query`
- 使用结构化对象做任务拆解

### 6.8 `app/a2a/orchestrator/task_decomposer.py`

该文件需要大改。

#### 删除内容

- `_split_question`
- `_infer_intent`
- `_extract_entities`
- 所有依赖正则和关键词的多轮理解逻辑

#### 新职责

将 `ResolvedQuery` 转换为 `SubTask` 列表。

新的输入签名建议为：

```python
def decompose_to_tasks(resolved_query: dict[str, Any], constraints: SubAgentConstraints) -> list[SubTask]:
```

#### 固定映射规则

- `preferred_sources` 包含 `rag` 且问题偏出处/原文/证据时，优先给 `rag`
- `preferred_sources` 包含 `kg` 且问题偏实体关系/结构化知识时，优先给 `kg`
- 混合问题按 `subqueries` 分任务
- `must_cover` 直接映射到 `SubTask.must_cover`
- `entities` 直接映射到实体提示
- 若 `subqueries` 为空，则退化为一个单任务

### 6.9 `app/a2a/agent/session_memory.py`

直接删除文件。

### 6.10 `app/common/feature_flags.py`

不新增任何 memory 相关 flag。

---

## 7. 需要删除的旧逻辑

以下逻辑明确视为旧路线，必须删除：

- 进程内 `SessionMemory`
- 多轮追问依赖规则拆解的上下文理解逻辑
- `/agent/run` 中任何 `legacy/off` 路径判断
- memory 功能相关 feature flag
- 面向旧路线的兼容提示和 fallback 说明

若 `/agent/run` 仍保留：

- `a2a_mode`
- `strategy`

则处理要求为：

- `a2a_mode` 不再支持 `legacy/off`
- `strategy` 不再决定不同实现，只作为固定值透传或忽略

---

## 8. 双 LLM 策略

### 8.1 Resolver LLM

#### 目标

把当前用户输入补全成独立问题，重点解决：

- “那老年人呢”
- “这个药呢”
- “原文依据呢”
- “刚才那个方案在 CKD 里呢”

这类对历史上下文有依赖的追问。

#### 输入

- 当前输入
- 最近 3 到 4 轮消息
- `summary_text`
- `retrieval_summary_text`

#### 输出

严格 JSON，必须匹配 `ResolvedQuery`。

#### 失败兜底

若发生以下任一情况：

- JSON 解析失败
- 关键字段缺失
- 模型超时
- 输出不合法

则返回最小合法结构：

```json
{
  "standalone_question": "当前原始输入",
  "intent": "direct_answer",
  "subqueries": ["当前原始输入"],
  "must_cover": ["answer"],
  "entities": [],
  "filters": [],
  "preferred_sources": ["kg"],
  "carry_over_context": []
}
```

不回退到旧规则理解。

### 8.2 Summarizer LLM

#### 目标

将本轮新增消息和运行产物折叠回持久化 memory，而不是每次生成一大段自然语言摘要。

#### 输入

- 旧 `summary_text`
- 旧 `retrieval_summary_text`
- 自旧 `last_message_id` 之后的消息增量
- 本轮运行产物：
  - `processing_steps`
  - `tool_observations`
  - `evidence_summary`
  - `assistant_reply`

#### 输出

严格 JSON，返回新的：

- `summary_text`
- `retrieval_summary_text`

#### 失败策略

- 当前请求不失败
- 记录日志
- 下次请求前再补偿更新

---

## 9. Memory 更新策略

### 9.1 请求前：强一致

`ensure_memory_current(session_id, current_message_id)` 固定执行逻辑：

1. 读取当前 memory
2. 读取最新消息 ID
3. 若满足以下任一条件，则触发重建：
   - memory 不存在
   - memory JSON 坏掉
   - `last_message_id < latest_message_id`
4. 返回最新 snapshot

### 9.2 重建策略

分为两种：

- 增量重建：
  - 从 `last_message_id + 1` 之后的消息开始 fold
- 全量重建：
  - memory 不存在或坏掉时，从全部消息开始

### 9.3 fold 方式

固定采用分批折叠：

- batch 大小建议固定为 10
- 每批调用一次 `Summarizer LLM`
- 新 summary 作为下一批输入
- 最终写回数据库

### 9.4 请求后：最佳努力

助手消息落库后：

- 立即调用 `refresh_after_turn`

若失败：

- 不影响当前流式响应
- 不做当前请求内重试
- 交由下次请求前的 `ensure_memory_current` 补偿

---

## 10. API 变更

### 10.1 保留接口

- `/process_with_session`
- `/sessions/{session_id}`
- `/sessions/{session_id}/messages`
- `/sessions/{session_id}/rollback-last-question`
- `/sessions/{session_id}/rollback-to-question`

### 10.2 新增接口

#### `GET /admin/sessions/{session_id}/memory`

返回示例：

```json
{
  "ok": true,
  "data": {
    "summary": {
      "version": 1,
      "last_message_id": 18
    },
    "retrieval_summary": {
      "version": 1,
      "last_message_id": 18
    },
    "updated_at": "2026-04-01T12:00:00"
  }
}
```

#### `POST /admin/sessions/{session_id}/memory/rebuild`

行为要求：

1. 清空旧 memory
2. 从当前 session 全量消息重建
3. 返回新 snapshot

### 10.3 不新增接口

不新增：

- 用户侧 memory 查看接口
- 用户侧 memory 删除接口
- 前端 memory 管理界面

---

## 11. Prompt 要求

### 11.1 Resolver Prompt 必须包含

- 当前任务是“把问题补全成独立问题”，不是回答问题
- 只能依据历史消息和已有 memory 进行补全
- 不得编造患者条件
- 输出必须是 JSON
- 即使上下文不足，也要返回最小合法结构

### 11.2 Summarizer Prompt 必须包含

- 当前任务是“更新会话记忆”，不是回答问题
- 必须区分：
  - 已确认事实
  - 待确认假设
- 不要写个体化处方建议
- 不得把模型推测写入 `confirmed_facts`
- 输出必须是严格 JSON

---

## 12. 测试方案

### 12.1 单元测试

新增：

- `tests/test_session_memory_service.py`
- `tests/test_task_decomposer_from_resolved_query.py`

覆盖内容：

- 空 memory 解析
- 坏 JSON 解析
- 超长字段裁剪
- 未知字段剔除
- `last_message_id` 判断
- 增量重建与全量重建切换
- resolver 失败兜底
- summarizer 失败补偿
- `preferred_sources` 映射
- `must_cover/entities` 透传

### 12.2 集成测试

新增：

- `tests/test_session_memory_integration.py`

覆盖场景：

#### 场景 1：多轮追问改写

1. 第一轮问一般治疗方案
2. 第二轮问“那老年人呢”
3. 第三轮问“原文依据呢”

断言：

- `resolved_query.standalone_question` 含完整上下文
- orchestrator 使用的是补全后的问题

#### 场景 2：会话重启恢复

1. 先完成两轮问答并写入 memory
2. 模拟新请求

断言：

- memory 被正确读取
- 不依赖只从 `messages` 临时拼接理解

#### 场景 3：rollback 一致性

1. 先问某个药物联用问题
2. 再 rollback
3. 再问“刚才那个场景在老年人里呢”

断言：

- 被回滚事实不再出现在 summary 中
- `resolved_query` 不再引用被回滚内容

#### 场景 4：post-turn 失败补偿

1. 模拟 `Summarizer LLM` 报错
2. 下一轮请求前触发 `ensure_memory_current`

断言：

- memory 能自动补齐

### 12.3 回归测试

必须保证：

- `/process_with_session` 仍支持流式返回
- assistant 消息仍正常写入 `messages`
- rollback API 仍正常删除消息
- Graph/RAG 子代理协议不变
- A2A artifact 结构不变

---

## 13. 验收标准

实现完成后必须满足以下标准：

1. 连续 5 轮多轮问答中，追问能被改写成独立问题
2. 同一 session 重启后再次提问，memory 仍能生效
3. rollback 后旧事实不会残留到后续追问改写和任务拆解中
4. `session_memory` JSON 始终可解析、可裁剪、可重建
5. 摘要更新失败不会影响当前用户响应
6. 项目中不存在第二条 memory 路线或 legacy 分支
7. 修改 `SESSION_MEMORY_MODEL_NAME` 后，无需改代码即可切换双 LLM 使用的模型

---

## 14. 推荐实施顺序

建议实现模型严格按以下顺序执行：

1. 新增 `session_memory_types.py`
2. 新增 `session_memory_prompts.py`
3. 新增 `session_memory_llm.py`
4. 新增 `session_memory_service.py`
5. 在 `config.py` 增加 `SESSION_MEMORY_MODEL_NAME`
6. 修改 `process_with_session`，打通 `build_runtime_context`
7. 修改 `_generate_a2a_stream_events`
8. 修改 `_run_a2a_query`
9. 修改 `process_query`
10. 修改 `IterativeAgent.run`
11. 修改 `OrchestratorExecutor.run`
12. 修改 `OrchestratorState`
13. 重构 `task_decomposer.py`
14. 修改 `nodes.py` 只使用 `resolved_query`
15. 在 `generate_data_with_session` 接入 post-turn summary refresh
16. 新增 admin memory 接口
17. 删除 `app/a2a/agent/session_memory.py`
18. 删除 legacy/off 兼容逻辑
19. 新增单元测试和集成测试
20. 清理 README 和注释中对旧 memory 路线的描述

---

## 15. 实现时的硬约束

实现模型必须遵守以下约束：

- 不得增加 feature flag
- 不得保留旧 memory 路线作为 fallback
- 不得把追问理解继续留给规则拆解逻辑
- 不得引入新的外部模型 SDK
- 不得修改数据库 schema
- 不得在运行时消费未知 JSON 字段
- 不得把坏 JSON 当作可用记忆继续运行

---

## 16. 给实现模型的最终说明

请将这次改造视为一次“替换式重构”，而不是“增量补丁”。

优先级顺序如下：

1. 单一路径
2. 职责清晰
3. 记忆一致性
4. 多轮追问改写效果
5. 失败补偿
6. 测试覆盖

不要为了减少改动而保留不再适合的新旧混合逻辑。若旧逻辑与新架构职责重叠，应直接删除旧逻辑。
