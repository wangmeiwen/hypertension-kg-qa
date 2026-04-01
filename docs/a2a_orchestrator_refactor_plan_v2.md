# A2A 主控重构计划（按任务驱动 + 证据门控）

## 1. 目标与原则

1. 主控从“句子驱动”升级为“任务驱动”，子 Agent 只执行主控下发任务，不越权终答。  
2. 证据门控统一复用 `app/a2a/agent/gating_mechanism` 的能力，尤其是：
   - `evidence_evaluator.py`
   - `evidence_trace_utils.py`
3. 保持单版本异步链路，不做旧流程兼容分叉。  
4. 继续保持约束：单个主 Agent 请求内对子 Agent 串行调用，但跨请求允许并发。

### 1.1 执行约束补充（已确认）

1. 单轨重构：禁止双轨并行，禁止保留旧流程与新流程同时可执行。  
2. 先删后加：同功能改造必须先删除旧实现，再接入新实现。  
3. 唯一实现：改造后同一功能只能存在一套执行路径，不允许冗余代码。  
4. 不做回滚分支：按前向迁移执行，不引入回退逻辑。项目备份由外部保障。  
5. 参数环境化：轮次上限、补检索回合上限、子任务工具调用上限必须统一放入环境变量并由配置层读取。  
6. 自测优先：可自测模块先完成并验证；暂时无法自测的模块先改造其他可验证部分，不阻塞整体推进。  
7. 并发约束保持不变：单个主 Agent 请求内对子 Agent 串行，跨请求允许并发。  
8. 门控能力复用：统一复用现有 gating_mechanism，不重复实现同类证据门控逻辑。

---

## 2. 全量改造大纲（对应 11 层）

### 2.1 问题拆解层：从“按句切”升级为“按任务拆”

**改造目标**
- 将当前 `decompose_question()` 从标点切句改为任务语义拆解。
- 子任务类型至少覆盖：
  - 比较类
  - 条件类
  - 多终点类
  - 依赖类
  - 证据核验类
  - 原文定位类

**子任务结构要求**
- `intent`
- `question`
- `entities`
- `constraints`
- `dependency_on`
- `evidence_type_required`
- `must_cover`
- `priority`
- `attempt_count`
- `coverage_status`

**模块改造**
- 重构：`app/a2a/orchestrator/policies.py`
- 新增：`app/a2a/orchestrator/task_decomposer.py`

---

### 2.2 检索方向生成层：主控可指导子 Agent

**改造目标**
- 主控为每个子任务生成可执行检索意图，而不是仅透传原问题。

**新增输出字段**
- `query_focus`
- `query_terms`
- `query_directions`
- `evidence_type_required`

**示例（比较题）**
- 关系约束检索
- 直接证据检索
- 终点限定检索
- 人群条件检索

**模块改造**
- 新增：`app/a2a/orchestrator/retrieval_planner.py`
- 对接：`app/a2a/orchestrator/nodes.py`

---

### 2.3 路由层：从静态 target_agent 升级为动态路由

**改造目标**
- 路由依据证据缺口动态决定 `kg/rag`，不再直接返回 `subtask.target_agent`。

**路由输入信号**
- 子任务 `intent`
- 所需证据类型
- 当前 KG/RAG 覆盖状态
- 是否已获得 direct support
- 上轮是否 weak/empty
- 当前缺口是结构关系还是文本原文

**需支持策略**
- 先 KG 后 RAG
- 先 RAG 后 KG
- 双证据闭环
- 同任务内部切换路由
- 失败后重路由

**模块改造**
- 新增：`app/a2a/orchestrator/router.py`
- 重构接入：`select_next_subagent`（`policies.py` -> `router.py`）

---

### 2.4 调度层：从 FIFO 升级为优先级调度

**改造目标**
- 将 `node_pick_next_subtask()` 的 `pop(0)` 改为优先级队列 + 覆盖度驱动调度。

**优先级规则**
- 核心结论型任务优先
- 直接比较/直接推荐优先
- 依赖任务等待前置完成
- 已 strong 覆盖任务降权
- 连续失败任务短时降权
- 补检索任务防止无限插队

**执行模式**
- 独立任务并发
- 依赖任务串行

**模块改造**
- 新增：`app/a2a/orchestrator/scheduler.py`
- 重构：`app/a2a/orchestrator/nodes.py`, `app/a2a/orchestrator/graph.py`

---

### 2.5 补检索决策层：从 no_signal 驱动升级为 coverage 驱动

**改造目标**
- `decide_need_supplement()` 改为覆盖度缺口驱动，而非粗粒度 `no_signal` 驱动。

**决策依据**
- 是否 direct support
- 核心子任务覆盖情况
- `must_cover` 未覆盖项
- 缺 KG 关系证据还是 RAG 原文证据
- 比较缺口/条件缺口/终点缺口
- 是“换 agent”还是“改 query”

**输出结构**
- `need_supplement`
- `supplement_type`
- `target_subtask_id`
- `next_agent`
- `next_question`
- `query_hint`
- `reason`

**模块改造**
- 新增：`app/a2a/orchestrator/supplement_decider.py`
- 重构：`app/a2a/orchestrator/policies.py`, `app/a2a/orchestrator/nodes.py`

---

### 2.6 证据模型层：从简单累加升级为按任务归档

**改造目标**
- 证据从“全局桶”升级为“任务绑定证据”。

**证据分层**
- 全局证据池
- 子任务证据池
- direct evidence
- indirect evidence
- conflicting evidence
- rejected evidence

**每条证据标准字段**
- `subtask_id`
- `agent_type`
- `evidence_type`
- `support_level`
- `matched_terms`
- `covers`
- `conflicts_with`
- `score`

**模块改造**
- 新增：`app/a2a/orchestrator/evidence_store.py`
- 新增：`app/a2a/orchestrator/coverage.py`
- 重构：`node_merge_evidence`（`nodes.py`）

---

### 2.7 终答层：从摘要器升级为门控器

**改造目标**
- 终答流程拆为两层：
  - `assess_answerability`
  - `compose_final_answer`

**assess 阶段判断**
- 是否 direct support
- 核心任务覆盖情况
- 未覆盖点
- 冲突存在与否
- 结论是直接回答还是保守推断
- 是否必须输出证据不足提示
- 置信度上限

**compose 阶段三类输出**
- 可直接回答
- 可保守回答
- 不能回答，仅解释缺口

**固定文案约束**
- 若终止原因为 `max_rounds_with_insufficient_evidence`，输出：
  - `当前证据不足，建议补充更具体的问题条件后重试，例如明确人群、指标阈值或治疗场景。`

**模块改造**
- 新增：`app/a2a/orchestrator/answer_gate.py`
- 新增：`app/a2a/orchestrator/gating_adapter.py`（桥接 `gating_mechanism`）
- 重构：`build_final_answer`（`policies.py`）

---

### 2.8 状态层：补齐主控运行状态

**改造目标**
- 扩展 `OrchestratorState`，支撑调度、覆盖分析、重路由。

**新增状态字段**
- `subtask_results`
- `subtask_status`
- `coverage_map`
- `attempt_counters`
- `agent_history`
- `query_hints`
- `failed_routes`
- `no_signal_streak`
- `parallel_groups`
- `dependency_graph`
- `answerability`
- `evidence_gaps`

**模块改造**
- 重构：`app/a2a/orchestrator/state.py`
- 同步：`executor.py`, `nodes.py`, `graph.py`

---

### 2.9 图流程层：从单一路径升级为分支编排图

**改造目标**
- 当前线性循环升级为多分支编排，支持回流与并发组。

**目标节点**
- 任务分类节点
- 依赖解析节点
- 优先级调度节点
- 并发任务分发节点
- 路由决策节点
- 查询改写节点
- 证据评估节点
- 终答门控节点

**关键分支**
- 并发支路
- 失败重写支路
- 依赖等待支路
- 门控失败回流支路

**模块改造**
- 重构：`app/a2a/orchestrator/graph.py`, `app/a2a/orchestrator/nodes.py`

---

### 2.10 主控/子 Agent 协议层：统一任务合同

**主控 -> 子 Agent 请求合同**
- `subtask_id`
- `intent`
- `question`
- `query_focus`
- `query_terms`
- `evidence_type_required`
- `must_cover`
- `constraints`
- `retry_context`
- `agent_role`

**子 Agent -> 主控返回合同**
- `subtask_id`
- `queries_used`
- `signal`
- `coverage`
- `missing_points`
- `evidence`
- `direct_support`
- `suggested_next_route`

**模块改造**
- 重构：`app/a2a/models.py`
- 重构：`app/a2a/client/subagent_client.py`
- 重构：`app/a2a/agent/graph_subagent.py`, `app/a2a/agent/rag_subagent.py`
- 重构：`app/a2a/subagents/kg/*`, `app/a2a/subagents/rag/*`

---

### 2.11 Prompt 层：主控与子 Agent 职责彻底分开

**主控 Prompt 职责**
- 问题分类
- 子任务拆解
- 路由选择
- 检索方向生成
- 覆盖判断
- 终答门控

**子 Agent Prompt 职责**
- 严格执行单任务
- 按主控方向生成查询
- 返回证据与覆盖状态
- 不越权终答

**模块改造**
- 新增：`app/a2a/orchestrator/prompt_specs.py`（或 `app/a2a/prompts/`）

---

## 3. 基于 gating_mechanism 的门控融合策略

### 3.1 复用能力（直接复用）
- `evaluate_and_gate(...)` 作为终答前统一门控入口。
- `build_subquery_evidence_bindings(...)` 用于子任务覆盖度判定。
- `detect_conflicts(...)`、`assess_semantic_evidence_quality(...)`、`extract_graphrag_quality_signals(...)` 用于质量和风险约束。

### 3.2 适配改造（主控侧补齐）
- 在主控侧新增 trace 归一化，将新的子任务执行结果映射为 `evaluate_and_gate` 可消费的 `traces` 结构。
- 将 `must_cover` 与 `coverage_map` 对齐到 `missing_points/uncovered_points`。
- 门控结果反向驱动：
  - 调度降权
  - 重路由
  - 补检索类型选择（换 agent / 改 query）

---

## 4. 需要改造的核心文件清单

### 4.1 重构现有
- `app/a2a/models.py`
- `app/a2a/orchestrator/state.py`
- `app/a2a/orchestrator/policies.py`
- `app/a2a/orchestrator/nodes.py`
- `app/a2a/orchestrator/graph.py`
- `app/a2a/orchestrator/executor.py`
- `app/a2a/client/subagent_client.py`
- `app/a2a/agent/graph_subagent.py`
- `app/a2a/agent/rag_subagent.py`
- `app/a2a/subagents/kg/state.py`
- `app/a2a/subagents/kg/query_builder.py`
- `app/a2a/subagents/kg/nodes.py`
- `app/a2a/subagents/rag/state.py`
- `app/a2a/subagents/rag/query_builder.py`
- `app/a2a/subagents/rag/nodes.py`
- `app/backend/main.py`（A2A 输出语义、状态透传）

### 4.2 新增建议
- `app/a2a/orchestrator/task_decomposer.py`
- `app/a2a/orchestrator/retrieval_planner.py`
- `app/a2a/orchestrator/router.py`
- `app/a2a/orchestrator/scheduler.py`
- `app/a2a/orchestrator/supplement_decider.py`
- `app/a2a/orchestrator/evidence_store.py`
- `app/a2a/orchestrator/coverage.py`
- `app/a2a/orchestrator/gating_adapter.py`
- `app/a2a/orchestrator/answer_gate.py`
- `app/a2a/orchestrator/protocol_contracts.py`
- `app/a2a/orchestrator/prompt_specs.py`

---

## 5. 测试计划与验收标准

### 5.1 单元测试
- 比较题场景：`kg有命中但无直接对照` -> `need_more_comparison_evidence`。
- 达到最大轮次仍不足 -> reason 为 `max_rounds_with_insufficient_evidence`。
- 泛化命中不算有效信号：`no_signal=True`（按有效覆盖判定）。
- 协议合同测试：主控请求字段与子 Agent 响应字段完整性。

### 5.2 集成测试
- 复现问题：
  - `贝那普利氨氯地平片（复方制剂）与单独使用氨氯地平相比，在降压效果和靶器官保护方面有什么优势？`
- 验证：
  - 不再出现“reach_max_rounds 但被当作可直接终答”的误导。
  - 证据不足时任务状态仍为 `completed`，前端展示可重试建议，不展示失败态。
  - 单请求内子 Agent 串行、跨请求并行保持不变。

### 5.3 回归检查
- `processing_steps` / `reflection_events` 的终止原因与终答文案一致。
- `evidence_summary` 新增字段可用：
  - `direct_comparison_support`
  - `coverage_intervention`
  - `coverage_comparator`
  - `coverage_outcomes`

---

## 6. 实施顺序（单轨执行版）

1. 参数环境化先行：先落地轮次与工具上限配置，并接通主控与子 Agent 全链路读取。  
2. 协议与状态先行：先改 `models/state/protocol`，删除旧字段使用路径后一次性切到新协议。  
3. 主控核心能力替换：按任务拆解、检索规划、动态路由、优先级调度、coverage 驱动补检索。  
4. 子 Agent 对齐：按新合同执行任务并回传 `coverage`、`missing_points`、`direct_support`、`suggested_next_route`。  
5. 门控与终答收口：接入 `gating_adapter + answer_gate`，删除旧 `build_final_answer` 摘要式终答路径。  
6. 图编排收口：删除旧线性流程连接，仅保留新分支编排图。  
7. 测试回归：优先执行可运行单测与集成测试；无法立即执行的项列入待测清单并继续推进可测模块。  

---

## 7. 默认约束（本计划生效前提）

1. 保持“主 Agent + 两个子 Agent（KG/RAG）”架构，不引入新子 Agent。  
2. 保持全异步单版本实现，不引入同步回退。  
3. 子 Agent 内部并发仅限同一子任务内的多语句执行，并受 `max_tool_calls` 限制。  
4. 证据门控以 `gating_mechanism` 为基线，新增逻辑通过适配层上移到主控。  
5. 禁止双轨：不保留旧流程与新流程并存。  
6. 禁止同功能重复实现：同一职责仅允许一份可执行代码。  
7. 改造顺序遵循先删后加，不允许“保留旧代码备用”。  
8. 轮次与工具调用上限必须环境化并统一由配置层注入。  
9. 无法立即自测时，不阻塞其他可验证模块改造；但必须保留明确待测项。  
10. 不引入回滚代码或兼容分叉，以单版本异步链路为唯一目标状态。  

