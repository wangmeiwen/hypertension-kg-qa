# 开源发布与 CI 错误总结

复盘日期：2026-07-30

## 复盘范围

本文复盘 `agent/open-source-docs` 分支发布到 GitHub 后，PR #1 的持续集成依次暴露出的错误。范围包括前端依赖安装、密钥扫描、Milvus 真实数据库环境和 MySQL 异步连接，不扩展为模型效果、医学质量或生产容量结论。

这四个错误彼此独立，但在 CI 中按执行顺序逐个显现：前一个错误修复并重新触发检查后，下一个更深层的错误才会暴露。因此不能把“修复首个失败任务”视为整条发布链路已经通过。

## 总体结论

| 序号 | 失败环节 | 直接表现 | 根因 | 失败运行 | 修复提交 |
| --- | --- | --- | --- | --- | --- |
| 1 | 前端 `npm ci` | 锁文件缺少间接依赖 | npm 10 解析结果与已提交锁文件不同步 | [30470665271](https://github.com/wangmeiwen/hypertension-kg-qa/actions/runs/30470665271) | `0fc96cc` |
| 2 | `detect-secrets` | 扫描后基线文件发生变化 | 基线仍包含已删除文件和过期位置元数据 | [30471504757](https://github.com/wangmeiwen/hypertension-kg-qa/actions/runs/30471504757) | `e2ac83a` |
| 3 | Milvus 启动 | `embedded etcd can not be used under distributed mode` | 单容器内嵌 etcd 配置与 Milvus 2.6.13 的实际部署模式冲突 | [30472001355](https://github.com/wangmeiwen/hypertension-kg-qa/actions/runs/30472001355) | `a056b16` |
| 4 | MySQL 真实数据库测试 | `ping() missing 1 required positional argument: 'reconnect'` | SQLAlchemy 2.0.48 未正确适配当前 aiomysql/PyMySQL ping 接口 | [30472817139](https://github.com/wangmeiwen/hypertension-kg-qa/actions/runs/30472817139) | `605047e` |

最终，PR 事件触发的后端覆盖率、真实数据库、前端质量和安全工作流均通过。

## 1. npm 10 锁文件不一致

### 现象

GitHub Actions 使用 Node.js 20 和 npm 10.8.2 执行 `npm ci` 时失败，日志指出 `package-lock.json` 缺少以下间接依赖：

- `@emnapi/core@2.0.0-alpha.3`
- `@emnapi/runtime@2.0.0-alpha.3`

### 根因

`npm ci` 不会修改锁文件，而是严格校验 `package.json`、锁文件和当前 npm 解析规则是否一致。现有锁文件虽然能支持本地已有 `node_modules` 继续运行，但未完整记录 npm 10 在干净环境中要求的依赖图。

本地“能启动”或“单元测试能跑”不能证明锁文件可以在全新环境中完成确定性安装。

### 修复

使用与 CI 相同的 npm 10.8.2 重新生成锁文件，只更新依赖解析结果，不执行依赖安装脚本：

```powershell
npx --yes npm@10.8.2 install --package-lock-only --ignore-scripts
```

修复后使用干净安装方式重复执行 `npm ci`，确认锁文件可复现。

### 预防措施

- 修改前端依赖后必须在与 CI 相同的 npm 主版本下更新锁文件。
- 合并前至少执行一次不依赖既有 `node_modules` 的干净安装。
- 依赖变更审查应同时检查 `package.json` 和 `package-lock.json`。
- 后续可评估在 `package.json` 中声明包管理器版本，进一步减少本地与 CI 的版本漂移。

## 2. detect-secrets 基线漂移

### 现象

密钥扫描没有报告新增真实密钥，而是提示：

```text
The baseline file was updated. Probably to keep line numbers of secrets up-to-date.
Please git add .secrets.baseline
```

### 根因

`.secrets.baseline` 中仍保留已删除的 `app/frontend/playwright.config.ts` 记录，同时文件位置元数据和扫描器自过滤结果与 Linux CI 环境生成的结果不一致。

Windows 本地扫描未稳定复现这一变化，说明基线文件不仅受仓库内容影响，也受扫描器版本、操作系统和调用方式影响。

### 修复

在 Linux 容器中使用与 CI 相同的 `detect-secrets==1.5.0` 重新生成基线：

- 移除已删除文件的过期记录；
- 更新位置元数据；
- 写入基线文件自过滤器 `detect_secrets.filters.common.is_baseline_file`，避免扫描产物把自身再次纳入结果；
- 保留经过审计的已知占位值；
- 再次执行相同扫描命令，确认基线不再产生差异。

### 预防措施

- 固定 `detect-secrets` 版本。
- 基线更新必须在与 CI 等价的 Linux 环境中完成。
- 删除或移动含已知占位凭据的文件时，同步检查 `.secrets.baseline`。
- 不因基线失败直接放宽规则；应先区分真实密钥、测试占位值和过期记录。

## 3. Milvus 2.6.13 启动模式冲突

### 现象

`real-db` 任务启动 Milvus 后，健康检查端口无法连接，容器日志显示：

```text
panic: embedded etcd can not be used under distributed mode
```

MySQL 和 Neo4j 服务已经健康，真实数据库测试尚未执行，因此失败点明确位于 Milvus 环境初始化。

### 根因

原工作流直接启动单个 Milvus 容器，同时设置：

```text
ETCD_USE_EMBED=true
COMMON_STORAGETYPE=local
milvus run standalone
```

这组配置在 Milvus 2.6.13 当前镜像中被判定为不兼容：进程初始化阶段使用了分布式模式配置，却同时请求内嵌 etcd，因而主动 panic。

项目已经维护了 `docker/milvus/docker-compose.milvus2.yml`，其中按照 standalone 部署方式组合外部 etcd、MinIO 和 Milvus。CI 另写一套单容器启动逻辑造成了部署配置重复和行为漂移。

### 修复

后端工作流改为直接复用项目 Compose 配置：

- 启动 etcd、MinIO 和 Milvus；
- 使用 `19531` 作为测试连接端口、`9092` 作为健康检查端口；
- 容器异常时输出整组 Compose 日志；
- 无论测试成功或失败，都执行 `docker compose down` 回收资源。

### 验证

- 使用隔离网络和独立容器名验证 Milvus 2.6.13 健康检查返回成功。
- GitHub Actions 两次 `real-db` 任务均完成，结果为 `3 passed`。

### 预防措施

- 本地、CI 和文档统一复用同一份基础设施配置。
- 升级数据库镜像后必须从空容器重新启动，不能只验证已有容器。
- 健康检查失败时先输出服务日志，再判断是启动超时还是配置错误。
- 为临时服务增加无条件清理步骤，避免失败路径遗留资源。

## 4. SQLAlchemy 与 aiomysql 的连接预检不兼容

### 现象

Milvus 修复后，真实数据库测试实际开始执行，其中 MySQL 会话与管理路由测试失败：

```text
TypeError: AsyncAdapt_aiomysql_connection.ping()
missing 1 required positional argument: 'reconnect'
```

最终结果为：

```text
1 failed, 2 passed, 141 deselected
```

### 根因

项目在两个异步引擎中都启用了 `pool_pre_ping=True`，并使用 `aiomysql==0.3.2`。SQLAlchemy 2.0.48 的 MySQL ping 参数探测逻辑没有正确覆盖当前 aiomysql/PyMySQL 适配层，调用了无参数 `ping()`，但异步适配连接仍要求显式传入 `reconnect`。

该问题属于依赖组合的上游兼容性回归，不是业务路由或数据库 schema 错误。SQLAlchemy 2.0.50 已修复 aiomysql 和 asyncmy 方言对新 PyMySQL ping 探测逻辑的适配问题。

### 修复

将后端依赖从：

```text
sqlalchemy==2.0.48
```

升级并固定为：

```text
sqlalchemy==2.0.50
```

同时在 Conda `hypertension-qa` 环境中安装相同版本并执行依赖一致性检查。修复保留了连接池预检，没有通过关闭 `pool_pre_ping`、修改驱动源码或运行时猴子补丁绕过错误。

### 验证

- `pip check`：无损坏或冲突依赖。
- SQLAlchemy 方言重新识别为需要调用 `ping(False)`。
- GitHub Actions 的两组 `real-db` 任务均通过。
- 后端快速测试与覆盖率任务均通过。

### 预防措施

- 数据库驱动、异步适配器和 SQLAlchemy 应作为一个兼容组合升级。
- 依赖升级后必须包含至少一次真实连接池复用测试，不能只验证首次连接。
- 对上游刚发布版本，先查阅 changelog 中的数据库方言变更。
- 固定直接依赖版本，并通过 CI 的干净安装验证间接依赖解析结果。

## 为什么本地验证没有一次发现全部问题

| 差异 | 影响 |
| --- | --- |
| 本地已有 `node_modules` | 掩盖锁文件在干净 npm 10 环境中的缺项 |
| Windows 与 Linux 扫描环境不同 | `detect-secrets` 基线生成结果存在差异 |
| 本地已有可运行的 Milvus Compose 服务 | 没有覆盖 CI 新建单容器的错误启动方式 |
| 错误按流水线顺序阻断后续任务 | 前一个错误修复前，后一个错误不会执行 |
| 数据库连接池路径不同 | 首次连接成功不能覆盖后续 checkout 的 pre-ping |

因此，发布验证必须同时包含“本地快速反馈”和“与 CI 等价的干净环境复现”。

## 最终验证记录

本轮修复后的 GitHub PR 事件验证结果：

- [后端工作流](https://github.com/wangmeiwen/hypertension-kg-qa/actions/runs/30473657854)
  - `fast-coverage`：通过；
  - `real-db`：通过。
- [前端工作流](https://github.com/wangmeiwen/hypertension-kg-qa/actions/runs/30473657868)
  - 格式、类型、Lint、Stylelint、单元测试、覆盖率、构建和 E2E：通过。
- [安全工作流](https://github.com/wangmeiwen/hypertension-kg-qa/actions/runs/30473657991)
  - Python/前端依赖、密钥和固定容器镜像扫描：通过。

上述结果证明当前提交能够通过仓库定义的工程质量门禁。验证边界如下：

- `real-db` 只执行 3 个受控测试，不覆盖生产拓扑、持久化恢复、高并发、容量或故障转移；
- SQLAlchemy 修复覆盖了当前预检与连接复用路径，不代表所有事务、断线重连和长连接路径均已验证；
- `detect-secrets` 只检查当前 Git 跟踪文件和基线，不替代 Git 历史、构建产物或外部存储的泄露审计；
- 前端工作流通过不代表已经完成多浏览器、多操作系统兼容性验证；
- 同一提交的 `push` 与 `pull_request` 检查属于重复执行，不能视为两个独立环境的交叉验证；
- 尚未完成医学答案正确性、临床验证、Ragas 或人工医学评审；
- 尚未复核合并到默认分支后的 Dependabot 告警。

## 非阻断观察与后续改进

### push 与 pull_request 重复执行

当前三个工作流同时监听 `push` 和 `pull_request`。向已有 PR 的功能分支推送时，同一提交会触发两套相同检查，增加执行时间和 GitHub Actions 消耗。本轮曾出现一个重复 `push` 任务长时间停留在 Playwright 浏览器下载，而对应的 PR 任务已经通过。

后续应单独评估以下方案：

- 只对默认分支保留 `push` 检查，功能分支使用 `pull_request`；
- 或增加按工作流和分支分组的并发取消策略；
- 配置后确认不会取消默认分支发布或必需检查。

### GitHub Actions Node.js 运行时警告

GitHub runner 提示部分 action 仍以 Node.js 20 为目标，并被平台强制使用 Node.js 24。当前只是警告，没有导致本轮失败。后续应在官方 action 发布兼容版本后升级，并重新验证缓存、权限和行为。

## 发布前错误排查清单

- [ ] 使用 CI 对应的 npm 版本执行干净 `npm ci`。
- [ ] 确认锁文件、依赖清单和安全审计结果同步。
- [ ] 在 Linux 等价环境中执行密钥扫描，确认基线无差异。
- [ ] 从空容器启动 MySQL、Neo4j、etcd、MinIO 和 Milvus。
- [ ] 验证服务健康检查，而不是只检查容器进程存在。
- [ ] 执行真实数据库测试并覆盖连接池复用路径。
- [ ] 执行后端快速测试、类型检查和覆盖率门禁。
- [ ] 执行前端格式、类型、Lint、单元测试、构建和 E2E。
- [ ] 检查临时容器、测试进程和生成文件是否已清理。
- [ ] 区分工程测试通过、模型效果验证和医学质量验收。

## 经验总结

1. CI 的价值不仅是重复本地测试，更重要的是提供干净、可复现的环境。
2. 连续修复时要追踪整条流水线，不能在第一个绿色任务出现后提前结束。
3. 基础设施配置应只有一个主要来源，避免本地 Compose 与 CI 脚本长期漂移。
4. 依赖版本固定不能替代兼容性验证；固定到有缺陷的版本同样会稳定失败。
5. 安全基线是需要维护的审计产物，不是生成一次后永久不变的忽略列表。

## 附录：20 页图谱与多跳 Agent 实验错误（2026-07-30 至 2026-08-01）

### Docling 并不是本轮 OOM 根因

低内存参数下，Docling 从 1 页逐步压测到 98 页均成功，98 页峰值 RSS 为 2.643 GiB，最低可用物理内存为 1.507 GiB。真正的危险点出现在完整后端启动：KG 与 RAG embedding 模型同时预热后，可用物理内存降到约 0.03 GiB。

修复方式是让 `rag_search` 惰性创建 `VectorStore`，低内存 Agent 服务只在实际调用某类工具时加载对应模型。该修改把“解析容量”和“服务模型常驻容量”分开治理，避免把所有内存问题误归因于 PDF 页数。

### 单一模型 lane 配额耗尽导致无效重试

图谱导入在第 6/10 块收到 `AllocationQuota.FreeTierOnly`，旧分类逻辑把它当作暂时错误，在同一已耗尽模型上等待并重试 4 次后失败。环境中另外两种模型实际仍可调用，但服务将最大并行度限制为 1，因此没有机会接替。

修复包括：

- 将 `AllocationQuota` 和 `Free quota exhausted` 识别为永久 lane 错误；
- 启用 3 条已配置模型通道；
- 永久错误摘除当前 lane，待处理块重新入队，由剩余通道接替；
- 保持写入器按块序串行提交，避免并行完成顺序改变来源顺序。

后续多次 10 块完整运行均由三通道在首次尝试完成，没有再出现配额重试风暴。

### 模型规范名覆盖领域规范

质量审计发现 `ABI` 仍作为正式实体名存在，别名是“踝臂指数”。原因是模型输出的 `name` 已是项目规范名“踝臂血压指数”，但同时给出了 `canonical_name=ABI`；旧逻辑只在输入名命中别名表时纠正，规范名本身没有自映射，最终错误采用了模型提示。

修复后，项目规范名集合优先于模型 `canonical_name`。新增回归测试专门覆盖“正确 name + 错误 canonical_name”的冲突。清库重建后的非规范别名审计为 0。

### 多跳关系存在，但 Agent 看不到完整路径

首个真实问题要求从“单纯夜间高血压”找到 ABPM，再找到来源指南。底层检索实际存在两条关系，但工具先返回大量实体，主控只保留前几条 Evidence，导致来源关系被截断。Agent 因证据不足而拒绝作答，这是正确的安全反应，也是检索输出契约的问题。

第一步将关系 Evidence 放到实体 Evidence 前。随后又发现两种排序偏差：

1. 第二条关系选择成“夜间高血压 → ABPM”，与第一条共享端点但不是继续向外的第二跳；
2. 改成有向延伸后，第二条又选择成“ABPM → 夜间血压”，词面重合压过了问题中的来源意图。

最终策略先从第一跳目标节点继续向外，再依据问题关系意图排序；包含“指南、来源、出处、支持、依据”的问题优先 `FROM_GUIDELINE`、`FROM_SOURCE` 和 `SUPPORTED_BY`。

### Milvus 重建实体数量膨胀

旧查询通过关系匹配读取节点，同一实体会随关系重复出现，导致 Milvus 实体文档数量高于 Neo4j 节点数。修复后实体查询只遍历一次 `KnowledgeEntity`，关系单独构建文档，并保留领域实体类型。

真实重建对账结果包括：

- 492 个实体 + 533 条关系 = 1,025 条 Milvus 文档；
- 473 个实体 + 506 条关系 = 979 条 Milvus 文档。

两次均精确一致，证明重复来自读取方式而不是向量插入批次。

### 中断运行没有命中续跑

最后一次清库导入在约 8/10 块时被人工暂停。重启后旧运行仍是 `running`，而续跑查询只选择可恢复的失败运行，因此系统创建了新的全量运行，而不是从第 9 块继续。

当前依靠节点和语义关系的幂等 MERGE 可以安全重放同一 PDF，并由最终重复审计验证结果；但任务层仍可改进：应用启动时应根据心跳超时把陈旧 `running` 运行收敛为可恢复状态，再执行块级续跑。

### 最终多跳评测只通过 3/5

最终 5 道真实 Agent 问题中有 3 道通过，低于预设的 4/5。失败不是超时或服务异常，而是答案与金标的语义不一致：

- 老年人目标比较题回答 65~79 岁为 130/80、80 岁以上为 150/90，缺少期望的 140；图谱没有可靠区分一般目标、强化目标和适用人群条件。
- 隐蔽性高血压题找到了 ABPM，但把“昼夜节律、清晨血压”作为两个基础指标，没有回答期望的“收缩压、舒张压”；关系检索命中了相关事实，却没有严格满足“基础指标”的语义约束。

这两项结果说明关系前置、有向延伸和意图排序解决的是 Evidence 可见性与路径连贯性，不能替代规则冲突消歧。后续应冻结一次抽取快照，为冲突事实增加适用人群、测量情境、目标类型和证据出处等限定字段，再由医学人员复核金标；不能仅修改测试期望来制造通过。
