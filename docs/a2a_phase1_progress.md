# A2A Phase 1 进展记录

更新日期：2026-03-30

## 已完成

1. 新增 A2A 适配模块（进程内实现）
- `app/a2a/models.py`
- `app/a2a/service.py`
- `app/a2a/__init__.py`

2. 新增 A2A 后端入口
- `GET /a2a/agent-card`
- `GET /a2a/agents/cards`（多 Agent 卡片发现）
- `POST /a2a/rpc`（JSON-RPC）
- `GET /a2a/tasks`
- `GET /a2a/tasks/{task_id}`
- `GET /a2a/tasks/{task_id}/events`（SSE 事件流）
- `POST /a2a/tasks/{task_id}/cancel`

3. 任务模型与生命周期
- 状态：`queued/running/completed/failed/canceled`
- 支持取消请求与轮询查询
- 支持进度事件累积（`progress_events`）
- 支持基于 `cursor` 的断线续读

4. 与现有链路打通
- A2A 任务执行复用 `process_query`
- 透传 `strategy/skill/max_turns/session_id/user_id`
- 透传 `cancel_check/progress_callback`
- `message/stream` 与 `tasks/resubscribe` 返回可订阅 `stream.url`

5. 运行时开关
- 新增 `ENABLE_A2A_ORCHESTRATION` 到运行时快照

6. 子 Agent 独立 A2A 化（Phase 2 增量）
- 新增子代理执行入口：`_run_graph_a2a_query`、`_run_retrieval_a2a_query`、`_run_evaluator_a2a_query`
- 新增子代理服务构造：`get_a2a_graph`、`get_a2a_retrieval`、`get_a2a_evaluator`
- 新增子代理 API（Graph/Retrieval/Evaluator 各一套）：
  - `POST /a2a/<agent>/rpc`
  - `GET /a2a/<agent>/tasks`
  - `GET /a2a/<agent>/tasks/{task_id}`
  - `GET /a2a/<agent>/tasks/{task_id}/events`
  - `POST /a2a/<agent>/tasks/{task_id}/cancel`

7. 旧链路下线，仅保留 A2A（Phase 3）
- `/agent/run` 强制走 A2A，不再执行 legacy pipeline
- `/process` 与 `/process_with_session` 改为基于 A2A 任务流返回消息
- 启动阶段不再启动旧 `qa_job_manager` worker 队列
- 管理端 `/admin/qa/queue/status` 改为返回 A2A 任务快照
- 兼容取消接口 `/qa/jobs/{job_id}/cancel`、`/admin/qa/jobs/{job_id}/cancel` 改为取消 A2A task
- 轮询参数可通过环境变量调整：
  - `AGENT_RUN_A2A_TIMEOUT_SECONDS`（默认 180）
  - `AGENT_RUN_A2A_POLL_MS`（默认 250）

8. 官方 A2A SDK 集成（Phase 4）
- `app/a2a/service.py` 改为基于官方 SDK 组件实现：
  - `DefaultRequestHandler`
  - `InMemoryTaskStore`
  - `InMemoryQueueManager`
  - `AgentExecutor` + `TaskUpdater`
- 依赖新增：`a2a-sdk[http-server]==0.3.25`
- 对外继续保留现有 JSON-RPC 兼容接口（`message/send`、`message/stream`、`tasks/get/list/cancel/resubscribe`）

9. A2A 契约测试（增量）
- 新增：`tests/test_a2a_contract.py`
- 覆盖：`message/stream`、`tasks/resubscribe`、任务完成 artifact、任务取消
- 本地执行（hypertension-qa）：
  - `python -m pytest -q tests/test_a2a_contract.py`

10. 端到端压测脚本（增量）
- 新增：`tests/a2a_http_benchmark.py`
- 能力：并发提交 A2A 请求、轮询任务终态、统计成功率/吞吐/延迟分位（p50/p95/p99）
- 示例：
  - `python tests/a2a_http_benchmark.py --base-url http://127.0.0.1:8000 --requests 20 --concurrency 5`

## 待推进（下一阶段）

1. 在真实环境执行压测并固化容量基线（QPS/延迟/SLA）
2. 任务持久化与重启恢复（当前为进程内内存）
3. 更细粒度流控（单任务订阅数/最大连接时长）
