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

### 第二批：断流与持久化补偿

- 流消费者被取消时将本轮状态收口为 `cancelled`，在 `finally` 中最多尝试两次落库已输出的部分回答；成功后只写一次助手消息、观测记录和记忆刷新任务，不向已断开的客户端继续发送事件。
- 正常回答完成后，助手消息落库最多尝试两次；瞬时失败恢复后仍输出 `persisted → done`，连续失败则以 `assistant_persist_failed` 可恢复错误终止，不再伪装成成功 `done`。
- 消息仓储返回空消息 ID 视为落库失败，避免没有可追踪消息 ID 时继续发布成功终态。
- 记忆刷新任务创建器改为可注入依赖；调度失败时主动关闭未提交协程，避免未等待协程泄漏。
- 记忆刷新调度失败或后台执行失败只写观测错误，不改变已经成功落库的 `persisted → done` 序列。
- 持久化完成事件与终态输出不再依赖工具/会话观测日志成功，观测失败不会遮蔽已经落库的消息 ID。

### 第二批质量门禁

- 新增 4 项会话流补偿测试，覆盖消费者取消、永久落库失败、瞬时落库恢复、记忆刷新调度失败和后台刷新失败。
- 后端非数据库自动化测试：90 passed，3 deselected。
- Python 规范检查：`ruff check app tests` 通过。
- Python 类型检查：`mypy app` 检查 139 个源文件，0 errors。
- 本批涉及文件通过 Black 检查。
- Conda `hypertension-qa` 环境执行 `pip check`，无破损依赖。

### 第三批：ASGI 断连、重连恢复与 Schema 版本

- 按项目当前 Uvicorn ASGI 2.3 运行方式模拟 `http.disconnect`，验证 Starlette 会取消 NDJSON body iterator，并由会话流补偿逻辑接管部分回答落库。
- 增加应用工厂级重连测试：`persisted` 事件中的用户/助手消息 ID 与重新请求会话历史得到的消息 ID 完全一致。
- 流事件契约导出带 `$id`、判别字段和版本号的 JSON Schema，并通过 `/schemas/stream-events.json` 公开。
- `/process` 与 `/process_with_session` 的 OpenAPI 响应声明 NDJSON 媒体类型、Schema 地址和 `X-Stream-Schema-Version` 响应头。
- 所有 NDJSON 响应携带 `X-Stream-Schema-Version: 1.0.0`；前端在解析正文前校验版本，不兼容时以 `schema_version_error` 快速失败。

### 第三批质量门禁

- 新增 ASGI 断连传播、重连消息 ID 和 JSON Schema 契约测试。
- 后端非数据库自动化测试：93 passed，3 deselected。
- Python 规范检查：`ruff check app tests` 通过。
- Python 类型检查：`mypy app` 检查 139 个源文件，0 errors。
- 本批涉及文件通过 Black 检查。
- 前端测试：24 个测试文件、75 项测试通过。
- 前端 TypeScript、ESLint、模块边界、Stylelint、Prettier 目标文件和 Vite 生产构建通过。
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

### P1：统一 Python 格式基线（已完成）

- 对 `app` 与 `tests` 全量执行 Black，统一 91 个历史文件的格式，未混入应用行为修改。
- `tests/conftest.py` 使用文件级 E402 豁免，明确保留测试启动前插入项目根目录的导入顺序。
- 后端 CI 的触发范围扩展到全部 Python 源文件，并固定执行 Black、Ruff、mypy 和异步 pytest；各门禁使用独立步骤，失败位置可直接定位。
- 本地验收：Black 检查 163 个文件通过，Ruff 全部通过，mypy 检查 139 个源文件 0 errors，非数据库自动化测试 93 passed、3 deselected。

### P1：图谱质量治理（进行中）

建立高血压领域本体、别名归一、关系方向、来源、时间和置信度约束；抽取结果先进入 staging，通过质量门槛后再写正式图。

第一批已完成：

- 新增独立的内存 staging 质量层，限定 24 类实体和 33 类关系的主客体方向；非法类型、反向关系、悬空端点、越界置信度和非法年份不会生成 Neo4j 查询。
- 统一 Unicode、空白、模型声明的 `canonical_name` 和首批高血压领域别名；节点与关系端点使用同一规范名，重复记录合并未冲突属性。
- 文档哈希、文件名、块序号、抽取时间、模型名和质量策略版本由导入运行时注入，模型不能伪造这些来源字段。
- Neo4j 关系按 `source_id + source_chunk_index` 保留来源粒度，不再由后续文档静默覆盖同一三元组的来源。
- 单块质量报告记录重复实体率、重复关系率、孤立节点率、最大连通分量占比、关系来源覆盖率和置信度覆盖率；任务阶段可直接观察当前块指标。
- 图谱导入策略版本升级为 v3，旧的未完成任务不会绕过新增质量层续跑。

第一批质量门禁：

- 新增 5 项图谱 staging 测试，覆盖别名与规范名、关系方向、悬空端点、来源注入、年份/置信度约束、连通性指标和写库查询隔离。
- 后端非数据库自动化测试：98 passed，3 deselected。
- Python 规范检查：Black 检查 165 个文件通过，Ruff 全部通过。
- Python 类型检查：mypy 检查 140 个源文件，0 errors。
- Conda `hypertension-qa` 环境执行 `pip check`，无破损依赖。

第二批已完成：

- 新增全图只读质量快照，基于 Neo4j 节点与关系投影计算重复实体、无名节点、孤立节点、最大连通分量、悬空关系、关系来源、关系置信度和当前质量策略覆盖率。
- `/admin/graph/quality-report` 对节点与关系规模设置显式上限；超限时拒绝返回部分指标，避免将截断样本误报为全图质量。
- 报告明确标记为 `best_effort_read`，不将多次只读查询包装成强一致事务快照。
- 图谱管理页新增质量卡片，可查看并刷新核心指标与策略版本。
- 真实 Neo4j 路由回归已验证质量查询的 Cypher 可执行，并覆盖至少两个节点和一条关系的快照。

第二批质量门禁：

- 新增 4 项全图快照算法/服务测试，并扩展管理路由与真实 Neo4j 回归。
- 后端非数据库自动化测试：102 passed，3 deselected；真实 Neo4j 定向测试：1 passed。
- Python 规范检查：Black 检查 168 个文件通过，Ruff 全部通过。
- Python 类型检查：mypy 检查 142 个源文件，0 errors。
- 前端测试：24 个测试文件、76 项测试通过。
- 前端 TypeScript、ESLint、模块边界、Stylelint、目标文件 Prettier 和 Vite 生产构建通过。
- Conda `hypertension-qa` 环境执行 `pip check`，无破损依赖。

后续验收：

- 重复实体率
- 孤立节点率
- 最大连通分量占比
- 关系来源覆盖率
- 金标多跳路径命中率
- 用真实 Neo4j 快照计算全图指标，并与单块 staging 指标区分。
- 由领域人员复核并扩充别名表、关系签名和金标多跳路径。

### P1：检索消融与评测（进行中）

第一批已完成：

- `VectorStore` 增加显式 `dense / sparse / hybrid / rerank` 四种模式；线上默认路径仍为 `rerank`，消融不再靠修改内部实现或伪造中间结果。
- `rag_search` MCP 工具开放 `retrieval_mode`，各模式继续返回统一 Evidence；非法模式返回显式错误。
- 新增确定性检索指标模块与离线汇总脚本，统一计算 Recall@K、MRR、nDCG、证据命中率、延迟、token 和失败率。
- 指标优先使用稳定 Evidence ID 精确匹配；为兼容旧数据集保留规范化文本包含匹配，并在报告中显式记录匹配策略。
- 汇总器拒绝重复题目、重复运行项、未知模式和模式缺题，避免不完整实验被静默补零。
- 支持 KG-only、dense-only、sparse-only、hybrid、rerank 和 Agent 六种结果文件；报告会标注缺失模式和数据集领域复核状态。
- 现有 30 题数据没有稳定 Evidence ID，也没有可确认的领域复核状态，因此本批只建立可复现的模式与指标契约，不把历史 Ragas 分数表述为可信消融结论。

第一批质量门禁：

- 检索模式、指标与命令行入口定向测试：17 passed。
- 后端非数据库自动化测试：108 passed，3 deselected。
- Python 格式检查：Black 检查 172 个文件，全部通过。
- Python 规范检查：`ruff check app tests` 通过。
- Python 类型检查：`mypy app` 检查 144 个源文件，0 errors。
- Conda `hypertension-qa` 环境执行 `pip check`，无破损依赖。

后续验收：

- 由至少两名领域人员复核问题、年份、口径、参考答案和金标 Evidence ID。
- 在同一 Milvus/Neo4j 快照上采集六种模式的完整运行结果并生成版本化报告。
- 将检索排序指标与 Ragas 答案忠实度、相关性指标分开报告，禁止相互替代。
- 增加图谱金标多跳路径命中率，并记录每次实验的索引、模型和配置版本。

### P1：API 与流式契约（已完成）

用 Pydantic 明确定义增量 `reply`、终止 `done`、错误和任务事件。`done` 不携带重复正文；消息落库、记忆刷新和客户端断流的补偿语义必须有集成测试。

第一批已完成：

- Pydantic 与 Zod 已共享相同的七类事件和必需字段语义。
- `reply.delta` 是唯一正文增量，`done` 不携带正文且位于成功流末尾。
- A2A 失败使用 `error`，路由明确声明 NDJSON 响应类型。
- 消息落库完成事件先于 `done`，已有服务层顺序回归测试。

第二批已完成：

- 流消费者取消后按 `cancelled` 收口，并在服务层验证部分正文只落库一次。
- 消息落库采用最多两次的有界重试，耗尽后输出可恢复错误。
- 记忆刷新调度和后台执行失败不改变已持久化回答的成功终态。

第三批已完成：

- ASGI 2.3 `http.disconnect` 可传播为 body iterator 取消。
- 客户端重连后可从会话历史恢复 `persisted` 对应的消息 ID。
- `/process`、`/process_with_session` 和前端通过版本化 JSON Schema 与响应头完成契约握手。

### P2：数据库迁移与版本管理（已完成）

第一批已完成：

- 将 MySQL 的 9 张业务表 DDL 和两项历史补列从仓储启动函数迁出，建立按版本排序、带名称和 SHA-256 校验和的 migration。
- 新增 `schema_migrations` 表；已应用迁移发生改名、内容漂移或数据库版本高于应用时立即拒绝继续迁移。
- 建库语句不再硬编码 `hypertension_kg`，改为校验并引用 `MYSQL_DATABASE` 对应库名。
- Neo4j 建立 v1 migration：为现有节点补齐 `KnowledgeEntity` 基础标签，建立实体身份与规范名索引，并用包含迁移校验和的哨兵索引持久化版本。
- 后续图谱抽取和后台手工建点统一写入 `KnowledgeEntity` 与 `entity_type`，不再依赖一次性迁移维持 schema。
- 启动流程只在 Neo4j 健康检查通过时执行迁移；不可用时保持降级启动，迁移异常进入统一启动错误日志。
- Milvus 文档集合与图谱集合建立显式 v1 schema 合同和稳定指纹；已有集合会校验字段集合、类型、向量维度、主键、auto-id 和动态字段设置，发现不兼容时拒绝加载。
- 保留已有集合和数据，不为版本化静默删库重建。

第一批真实依赖验证：

- MySQL 真实库已应用 `baseline_tables` v1 和 `graph_import_resume_contract` v2，版本、名称和 64 位校验和查询正常。
- Neo4j 真实库已建立三个 `hypertension_*_v1` 索引；1748 个现有节点均带 `KnowledgeEntity` 标签。
- 两个现有 Milvus 集合均通过 v1 schema 校验，文档集合指纹为 `6fd5dd48ba61`，图谱集合指纹为 `478c1ecf0fed`。
- 真实 MySQL、Neo4j、Milvus 集成测试：3 passed。

第一批质量门禁：

- 后端非数据库自动化测试：120 passed，3 deselected。
- Python 格式检查：Black 检查 179 个文件，全部通过。
- Python 规范检查：`ruff check app tests` 通过。
- Python 类型检查：`mypy app` 检查 148 个源文件，0 errors。
- Conda `hypertension-qa` 环境执行 `pip check`，无破损依赖。

第二批已完成：

- 图谱解析缓存从 v2 升级为 v3，命中校验同时绑定文件 SHA-256、解析器版本、分块大小、分块策略版本、抽取管线版本和抽取模型配置签名。
- 抽取模型签名只使用 lane id、模型名和配置来源，不写入 API 密钥或服务地址；lane 顺序变化不会造成无效失效。
- 解析器、抽取管线或模型配置变化时返回具体失效原因并重新解析，不再继续复用语义已过期的缓存。
- 文档向量新增版本化身份模块；Milvus 主键和 `chunk_id` 同时绑定文件哈希、解析/分块/embedding 管线版本、分块参数、文本哈希和 embedding 模型签名。
- 同一文件重新嵌入时先以确定性主键 upsert 新向量，成功后再按可信 SHA-256 `source_hash` 清理不在本次主键集合内的旧管线向量，避免失败时先删掉可用数据。
- 检索输出继续携带 `source_hash`、解析/分块/embedding 版本和模型签名，便于离线评测追踪索引来源。
- Milvus 真实集合已验证可按动态 `source_hash` 字段执行删除过滤；本批未重新嵌入真实指南文件，避免无验收数据写入。

第二批质量门禁：

- 后端非数据库自动化测试：125 passed，3 deselected。
- Python 格式检查：Black 检查 181 个文件，全部通过。
- Python 规范检查：`ruff check app tests` 通过。
- Python 类型检查：`mypy app` 检查 149 个源文件，0 errors。
- Conda `hypertension-qa` 环境执行 `pip check`，无破损依赖。

第三批已完成：

- 新增管理员只读接口 `/admin/storage/schema-report`，并发返回 MySQL、Neo4j、Milvus 的期望版本、实际版本、待迁移项、哨兵、集合名和完整指纹。
- 三类存储分别隔离错误；单库不可用或 schema 不兼容时报告整体 `incompatible`，同时保留其他存储的有效诊断结果。
- 诊断使用轻量 Milvus 客户端直接读取集合描述，不触发 embedding 或 reranker 模型加载。
- 真实 MySQL、Neo4j、Milvus 联合诊断结果为 `compatible`，实际版本分别为 MySQL v2、Neo4j v1、Milvus 两个集合 v1。
- 空 MySQL 在 CI 初始化步骤执行完整 migration；单元测试覆盖历史库补列、迁移内容漂移、版本超前、历史缺口、Neo4j 重复哨兵和 Milvus 字段/维度不兼容。

第三批质量门禁：

- 后端非数据库自动化测试：128 passed，3 deselected。
- Python 格式检查：Black 检查 184 个文件，全部通过。
- Python 规范检查：`ruff check app tests` 通过。
- Python 类型检查：`mypy app` 检查 151 个源文件，0 errors。
- Conda `hypertension-qa` 环境执行 `pip check`，无破损依赖。

后续增强（不阻断本阶段）：

- 为升级前缺少 `source_hash` 的历史 Milvus 行提供只读审计和显式清理工具，不自动按同名文件删除。

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
