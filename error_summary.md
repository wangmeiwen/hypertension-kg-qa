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
