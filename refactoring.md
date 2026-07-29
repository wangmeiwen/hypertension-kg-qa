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

- 新增 `graph_extraction_lane_pool.py`，迁移 lane 状态模型、错误分类、状态更新、阶段回调包装和单块 LLM 图谱抽取。
- 401/403/404 被归类为永久错误，429、超时和网络错误被归类为可重试错误；lane 活跃/可恢复计数由同一组件维护。
- `graph_admin_service.py` 降至约 940 行。
- 新增 lane 初始化、退避状态迁移和错误分类测试；并发队列调度循环仍留在主服务，下一步继续迁移。

### 本阶段质量门禁

- 后端非数据库自动化测试：70 passed，3 deselected。
- Python 规范检查：`ruff check app tests` 通过。
- Python 类型检查：`mypy app` 检查 138 个源文件，0 errors。
- 新解析组件、后台任务运行时、图谱写入器、lane 组件与对应测试通过 Black 检查。
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

### P0：统一 Evidence 契约

定义并贯穿 `source_type / source_id / document_id / chunk_id / page / section / content / score / retrieval_method / provenance`。KG、dense、sparse、rerank 和历史检索必须输出同一种 Evidence，终答引用只能消费该契约。

验收：

- 任一答案引用可追溯到文档页码或图谱来源。
- 同一检索结果不再在 Agent、MCP、服务和前端中重复转换为不同字典结构。

### P0：继续拆分图谱任务执行器

把当前 `graph_admin_service.py` 剩余职责拆为：

- `graph_extraction_lane_pool` 的并发队列调度循环

验收：

- Service 文件控制在 500 行左右。
- `graph_import_parser`、`background_job_runtime`、`graph_import_writer` 已完成；`graph_extraction_lane_pool` 已完成无共享队列状态部分，后续继续迁移调度循环。
- lane 退避、块状态迁移和顺序写入分别有独立单元测试。
- 进程重启、429、超时、401/403/404 和单块失败的状态可预测。

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
