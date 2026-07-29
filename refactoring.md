# 项目重构记录与后续路线

## 目标

本轮重构以“减少职责混杂、保留外部行为、先建立可验证边界”为原则。系统仍定位为研究与毕业设计原型，不将基础测试通过解释为医疗质量或生产可用性已经得到证明。

## 已完成的第一阶段

### 后端

- 新增 `app/backend/domain/job_status.py`，统一图谱任务与文档任务的状态值。
- 将 Neo4j 管理端 CRUD、标签查询、关系查询和只读 Cypher 收口到 `graph_crud_mixin.py`。
- 将图谱导入解析缓存收口到 `graph_import_cache_mixin.py`。
- 将图谱导入 run/chunk 持久化收口到 `graph_import_repository_mixin.py`。
- 将会话 LLM/工具 JSONL 日志读取收口到 `session_log_repository.py`。
- `graph_admin_service.py` 只保留任务编排、解析、抽取、写入和任务观测等核心流程，文件规模由约 2700 行降到约 2000 行。
- 保留 `GraphAdminService`、`SessionStreamService` 等既有入口，路由和依赖注入接口未改变。

### 安全与正确性

- 图谱节点列表的 label 在拼接 Cypher 前统一经过标识符校验。
- 只读 Cypher 改为按关键字边界检查，合法字段 `created_at` 不再被 `create` 子串误判。
- 禁止 `CALL`、写操作和多语句输入，避免管理员只读入口被用于过程调用或数据修改。

### 前端

- 将图谱规范化、邻接表、连通分量、标签邻域过滤、采样和初始坐标计算迁移到纯模型 `graph_canvas_model.ts`。
- `KnowledgeGraphCanvas.tsx` 从约 500 行降到约 320 行，只负责画布状态、力导向参数和交互渲染。
- `ForceGraphCanvas.tsx` 直接依赖模型类型，消除对上层 UI 组件的类型反向依赖。
- 将图谱管理页的属性解析、属性/关系展示、任务卡片和三个表单弹窗拆成独立组件。
- 为图谱模型增加纯函数测试，覆盖悬空边过滤、标签邻域过滤和邻接关系。

## 已完成的第二阶段

### 类型与依赖边界

- 在 Conda `hypertension-qa` 环境中核对项目依赖，`pip check` 无破损依赖，异步测试、Ruff、mypy 和 Black 均可执行。
- 将服务对 `database.py` 中 Manager 重导出的依赖改为直接依赖对应仓储模块，移除数据库基础模块与具体 Manager 之间的循环重导出。
- 对观测日志、会话流水和外部工具结果先做字典/列表归一化，再进入业务计算；`admin_sessions_service.py` 与 `session_stream_service.py` 不再依赖隐式的可空动态值。
- Neo4j 必需连接配置改为启动时快速失败，避免 `None` 延迟流入驱动初始化。
- LangGraph 子代理移除当前版本不支持、每次都会进入异常回退的 `output_keys` 调用，并将可选导入的运行时工厂与静态类型分离。
- LangSmith 适配器移除同名函数重定义和过期位置参数兼容分支，按项目锁定版本使用关键字元数据接口。
- 第三方未提供 `py.typed` 的模块只在具体导入点标注，不设置全局 mypy 忽略。

### 质量门禁结果

- 后端自动化测试：64 passed，异步测试实际执行。
- Python 规范检查：`ruff check app tests` 通过。
- Python 类型检查：`mypy app` 检查 134 个源文件，0 errors。
- 新增架构组件 Black 检查：8 个文件通过。
- 前端类型、ESLint、模块边界和 Stylelint 全部通过。
- 前端测试：24 个测试文件、72 项测试通过。
- 前端 Vite 生产构建通过，PWA 产物生成成功。

## 已完成的第三阶段

### 图谱导入解析边界

- 新增 `graph_import_parser.py`，独立承担上传文件校验、Docling 与内置回退解析、按句分块和解析缓存读写。
- `graph_admin_service.py` 改为通过解析组件编排导入，不再保留文档解析的双实现，规模由约 1970 行降至约 1780 行。
- 为纯文本分块、无标点硬切和缓存复用增加独立回归测试。
- 修复图谱抽取 lane 状态列表位于异常分支之后、正常任务会引用未初始化变量的问题，并为 lane 初始状态增加契约测试。

### 后台任务运行时边界

- 新增 `background_job_runtime.py`，统一管理任务注册与去重、状态快照、阶段 trace、心跳、软超时、查询和异步 runner 生命周期。
- 主服务只初始化并调用后台任务运行时，上传协议处理、图谱抽取和 Milvus 嵌入入口保持不变。
- `graph_admin_service.py` 进一步降至约 1130 行。
- 新增同资源活跃任务去重、成功 runner 状态收口和软超时仅告警测试。

### 图谱导入写入边界

- 新增 `graph_import_writer.py`，负责单块抽取结果的节点/关系顺序写入、块状态落库、run 失败收口和持久化进度重算。
- lane 队列仍决定块顺序，写入组件不参与并发调度，避免并发状态和数据库状态相互耦合。
- `graph_admin_service.py` 降至约 1090 行。
- 新增节点先于关系、批次数统计和 Neo4j 写入失败同步标记 chunk/run 的测试。

### 图谱抽取 lane 边界

- 新增 `graph_extraction_lane_pool.py`，迁移 lane 状态模型、错误分类、状态更新、阶段回调包装、单块 LLM 图谱抽取和并发队列调度循环。
- 401/403/404 被归类为永久错误，429、超时和网络错误被归类为可重试错误；lane 活跃/可恢复计数由同一组件维护。
- lane pool 负责 worker 重试/退避、结果缓冲和按块序调用写入器，主服务只准备续跑上下文并消费汇总结果。
- 删除仅由测试调用、已无生产调用方的暂停错误归一和暂停结果构造私有方法。
- `graph_admin_service.py` 降至约 455 行，达到约 500 行的阶段目标。
- 新增 lane 初始化、退避状态迁移、错误分类和两块文本按序调度测试。

### 本阶段质量门禁

- 后端非数据库自动化测试：71 passed，3 deselected。
- Python 规范检查：`ruff check app tests` 通过。
- Python 类型检查：`mypy app` 检查 138 个源文件，0 errors。
- 新解析组件、后台任务运行时、图谱写入器、lane 组件与对应测试通过 Black 检查。
- Conda `hypertension-qa` 环境执行 `pip check`，无破损依赖。

## 已完成的第四阶段（Evidence 契约）

### 第一批：共享证据模型

- 新增 `Evidence` 强类型模型，统一字段为 `source_type / source_id / document_id / chunk_id / page / section / content / score / retrieval_method / provenance`。
- `SubAgentResult` 和 `EvidenceBundle` 改为使用 Evidence 模型，不再把任意字典声明为标准证据。
- 新增 `app/a2a/evidence.py`，分别将 RAG 文档命中和 KG 实体、关系、Cypher 记录归一为同一结构。
- RAG、KG 子代理的结果归一化节点已接入共享契约；KG 原始记录保存在 `provenance.raw`，避免统一字段时丢失数据。
- 子代理重试词提取同步改为消费 Evidence 字段和 provenance，不再依赖旧的 `{hit}`、`{entity}`、`{relationship}` 包装。

### 本阶段质量门禁

- Evidence 契约测试：4 passed，覆盖 RAG、KG 和 SubAgentResult 强类型校验。
- 后端非数据库自动化测试：75 passed，3 deselected。
- Python 规范检查：`ruff check app tests` 通过。
- Python 类型检查：`mypy app` 检查 139 个源文件，0 errors。
- 新 Evidence 模型、归一化组件和测试通过 Black 检查。
- Conda `hypertension-qa` 环境执行 `pip check`，无破损依赖。

### 第二批：RAG 工具与检索路径

- MCP `rag_search` 直接输出 `evidence`，删除长期并存的专用 `hits` 响应；RAG 子代理和工具结果摘要同步消费新契约。
- 检索文档元数据记录实际执行路径：稠密降级为 `dense`，混合召回按执行情况记录 `dense_rrf` 或 `dense_sparse_rrf`，成功重排后追加 `_rerank`。
- RAG Evidence 的 `retrieval_method` 直接继承检索层元数据，不再统一写死为 `hybrid_rerank`。
- 新增向量命中检索阶段和 MCP `rag_search` Evidence 输出测试，并为 Milvus 初始化失败降级响应增加契约断言。

### 第二批质量门禁

- Evidence 契约测试：7 passed。
- 后端非数据库自动化测试：78 passed，3 deselected。
- Python 规范检查：`ruff check app tests` 通过。
- Python 类型检查：`mypy app` 检查 139 个源文件，0 errors。
- 新 Evidence 归一化组件、模型与契约测试通过 Black 检查。
- Conda `hypertension-qa` 环境执行 `pip check`，无破损依赖。

### 第三批：KG 工具直接输出 Evidence

- MCP `kg_query` 将 Neo4j 的 Cypher、关键词、全节点浏览和关键词回退结果直接转换为 Evidence，`retrieval_method` 标注实际查询模式。
- MCP `kg_entity_relation_search` 将实体和关系直接合并为 Evidence；原始记录、证据类型、Milvus 集合名和检索元数据保存在 provenance。
- KG 子代理统一消费工具输出的 `evidence`，删除对 `records / entities / relationships` 的二次转换。
- KG 工具的 blocked、unavailable、消歧、社区检测和 token 统计等控制元数据保持独立，不混入 Evidence 标准字段。
- 新增 Neo4j 与 KG Milvus 工具正常路径契约测试，并覆盖 Milvus 初始化失败时的空 Evidence 降级响应。

### 第三批质量门禁

- Evidence 契约测试：9 passed。
- 后端非数据库自动化测试：80 passed，3 deselected。
- Python 规范检查：`ruff check app tests` 通过。
- Python 类型检查：`mypy app` 检查 139 个源文件，0 errors。
- Evidence 模型、归一化组件和契约测试通过 Black 检查。
- MCP 工具 YAML 通过安全解析校验。
- Conda `hypertension-qa` 环境执行 `pip check`，无破损依赖。

### 第四批：历史检索与终答引用

- 检索记忆升级为 v3，按问题持久化 Evidence，不再把 `query_records / citations` 作为历史事实来源。
- 读取 v2 检索记忆时在存储边界自动转换为 Evidence，并以 `legacy_history` 标记迁移来源；后续保存会自然升级为 v3。
- 会话流式服务从主控观测提取、校验并去重 Evidence，再写入检索记忆；超长 provenance 会保留关键追溯字段并标记截断。
- `history_read` 统一返回 Evidence，缺少参数或未匹配时也返回明确的空 Evidence 契约。
- 主控观测、提示词压缩、证据摘要、数值冲突检测和终答提示词只消费 Evidence；`query_records` 仅暂留给管理端展示。
- 恢复可执行的 `call_history_read` 主控动作，使历史问题索引能够实际取回 Evidence，而不是只暴露记录数量。
- 终答提示词使用 `[source_id p页码]` 或 `[source_id]` 引用标签，证据摘要从 Evidence 确定性生成 citations 元数据，禁止建立第二套引用来源。

### 第四批质量门禁

- Evidence 契约测试：11 passed。
- 后端非数据库自动化测试：82 passed，3 deselected。
- Python 规范检查：`ruff check app tests` 通过。
- Python 类型检查：`mypy app` 检查 139 个源文件，0 errors。
- Evidence 模型、归一化组件和契约测试通过 Black 检查。
- MCP 工具 YAML 通过安全解析校验。
- Conda `hypertension-qa` 环境执行 `pip check`，无破损依赖。

## 已完成的第五阶段（API 与流式契约）

### 第一批：问答 NDJSON 事件

- 新增 `app/backend/domain/stream_events.py`，用严格 Pydantic 判别联合统一 `job / session / msg / reply / persisted / done / error` 七类公开事件。
- `reply` 改为只携带非空 `delta`；`done` 只携带状态、处理步骤、工具观测、Evidence 摘要和 telemetry，不再重复回答正文。
- A2A 任务不存在或执行失败时输出显式 `error`，不再用带错误正文的 `done` 模拟异常终态。
- 会话服务在边界重新校验注入事件，依次输出免责增量、`persisted` 和最终 `done`；`done` 成为成功流的最后一个事件。
- `/process` 与 `/process_with_session` 的响应类型由 `text/plain` 改为 `application/x-ndjson`，并关闭代理缓冲。
- 前端 Zod schema 与后端字段逐项对齐并启用严格校验；删除将畸形 JSON 事件降级为普通进度文本的兼容分支。
- 聊天页只按 `delta` 追加正文，删除累计全文、重复片段和增量片段三种语义并存时所需的重叠合并逻辑；显式 `error` 会进入统一失败反馈。
- 确定性测试运行时同步使用同一事件模型，避免测试桩继续传播旧协议。
- Ragas 流式评测入口改为顺序累加 `reply.delta` 并读取显式 `error`，不再从 `done.msg` 回退正文或错误。
- 删除项目内无任何调用方、仍生成旧 `reply.msg / done.msg` 的进程内消息队列模块，避免形成第二套流协议。

### 第一批质量门禁

- 流事件契约测试覆盖正文/终态分离、旧字段拒绝和 A2A 失败事件；会话服务测试覆盖 `persisted` 先于最终 `done`。
- 后端非数据库自动化测试：86 passed，3 deselected。
- Python 规范检查：`ruff check app tests` 通过。
- Python 类型检查：`mypy app` 检查 139 个源文件，0 errors。
- 本轮涉及的 Python 文件通过 Black 检查。
- 前端测试：24 个测试文件、74 项测试通过。
- 前端 TypeScript、ESLint、模块边界、Stylelint 和 Vite 生产构建通过。
- 本轮涉及的前端文件通过 Prettier；全仓库仍有历史格式债务，继续归入独立的格式基线任务。
- Conda `hypertension-qa` 环境执行 `pip check`，无破损依赖。

## 当前架构边界

```text
router
  -> application service
      -> domain contract
      -> repository/cache component
      -> external runtime (Neo4j / Milvus / LLM / OCR)

page/workspace
  -> query + mutation orchestration
  -> presentational components
  -> pure model
  -> API client + runtime schema
```

边界规则：

1. Router 只做协议适配、鉴权和响应映射。
2. Service 负责用例编排，不直接承担大段文件缓存或数据库 CRUD。
3. Repository 只读写持久化数据，不决定业务流程。
4. 纯模型不得依赖 React 生命周期或浏览器 API。
5. 外部 API 数据先经过运行时 schema，再进入页面状态。
6. 内部必需配置缺失时快速失败，不使用静默默认值掩盖错误。

## 后续重构优先级

### P0：统一 Evidence 契约（已完成）

定义并贯穿 `source_type / source_id / document_id / chunk_id / page / section / content / score / retrieval_method / provenance`。KG、dense、sparse、rerank 和历史检索必须输出同一种 Evidence，终答引用只能消费该契约。

已完成：

- Evidence 模型及 RAG/KG 子代理归一化。
- SubAgentResult、EvidenceBundle 强类型约束。
- MCP `rag_search` 直接输出 Evidence，RAG 子代理不再二次转换专用 hits。
- dense、sparse、RRF 和 rerank 的实际执行路径写入 `retrieval_method`。
- MCP `kg_query`、`kg_entity_relation_search` 直接输出 Evidence，KG 子代理不再二次转换专用结果结构。
- 会话检索记忆、`history_read`、主控证据摘要和终答引用统一消费 Evidence。
- 旧 v2 检索记忆可在读取时迁移为 v3 Evidence。

验收：

- 任一答案引用可追溯到文档页码或图谱来源。
- 同一检索结果不再在 Agent、MCP、服务和前端中重复转换为不同字典结构。

后续测试增强：

- 使用真实数据库快照验证 v2 到 v3 的持久化升级。
- 在固定问答集上统计终答引用标签与 Evidence 来源的一致率。

### P0：图谱任务执行器收口（已完成）

已完成以下组件拆分：

- `graph_import_parser`
- `background_job_runtime`
- `graph_import_writer`
- `graph_extraction_lane_pool`

完成情况：

- `graph_admin_service.py` 已控制在约 455 行，只保留依赖装配、任务入口、续跑编排和 Milvus 嵌入入口。
- lane 状态、分块解析、后台任务运行时和顺序写入均已有独立组件与单元测试。

后续测试增强：

- 为 lane 永久失败、429 退避后恢复和最大重试耗尽增加更多调度级参数化测试。
- 增加进程重启后的真实数据库续跑集成测试。

### P1：统一 Python 格式基线

全量 `black --check app tests` 仍发现 100 个历史文件未采用统一格式。该工作应作为独立的纯格式化变更完成，避免与架构调整混合后降低可审查性。

验收：

- 单独提交全项目 Black 格式化，不夹带行为修改。
- CI 固定执行 Ruff、Black、mypy 和异步 pytest。
- CI 明确区分通过、跳过和缺少依赖。

### P1：图谱质量治理

建立高血压领域本体、别名归一、关系方向、来源、时间和置信度约束；抽取结果先进入 staging，通过质量门槛后再写正式图。

指标：

- 重复实体率
- 孤立节点率
- 最大连通分量占比
- 关系来源覆盖率
- 金标多跳路径命中率

### P1：检索消融与评测

固定并经领域人员复核的问题集，分别运行 KG-only、dense-only、sparse-only、hybrid、rerank 和 Agent。质量报告同时记录 Recall@K、MRR、nDCG、证据命中率、延迟、token、失败率。

### P1：API 与流式契约

用 Pydantic 明确定义增量 `reply`、终止 `done`、错误和任务事件。`done` 不携带重复正文；消息落库、记忆刷新和客户端断流的补偿语义必须有集成测试。

第一批已完成：

- Pydantic 与 Zod 已共享相同的七类事件和必需字段语义。
- `reply.delta` 是唯一正文增量，`done` 不携带正文且位于成功流末尾。
- A2A 失败使用 `error`，路由明确声明 NDJSON 响应类型。
- 消息落库完成事件先于 `done`，已有服务层顺序回归测试。

待完成：

- 增加真实 HTTP 客户端中途断开时的补偿集成测试，确认已输出的部分正文只落库一次。
- 增加消息落库失败、记忆刷新调度失败和客户端重连后的协议级测试。
- 为 `/process`、`/process_with_session` 和 A2A 任务事件生成共享的 OpenAPI/JSON Schema 文档。

### P2：数据库迁移与版本管理

将启动时建表迁移到正式 migration；为 MySQL、Neo4j 和 Milvus schema 建立显式版本。缓存键绑定文件哈希、解析版本、分块版本、抽取模型版本和 embedding 版本。

### P2：可观测性与安全

- request/session/turn/agent/tool/LLM/job 使用统一 trace id。
- token 和耗时在产生源记录并向上聚合。
- 管理接口增加更细粒度权限、审计和速率限制。
- 增加依赖扫描、密钥扫描和容器镜像扫描。
- 对上传文件执行 MIME、扩展名、大小、页数和解压炸弹检查。

## 每阶段完成标准

- 行为变化先有测试，结构变化至少有回归测试。
- 运行对应单元、集成、前端组件、构建和边界检查。
- 不保留新旧双实现。
- 不混入无关格式化或数据文件。
- 文档同步记录输入、输出、失败语义和未验证边界。
