# 主子 Agent 重构说明（按你的目标流程）

## 目标流程（第一性原则）

1. 主 Agent 只做决策层工作：问题分解、轮次控制、是否补检索。
2. 主 Agent 同一时刻只调用一个子 Agent。
3. 子 Agent 只接收主 Agent 给定的问题（或子问题），负责生成具体查询语句并执行查询。
4. 子 Agent 可以在内部决定数据库并发策略。
5. 子 Agent 不主动补检索、不扩展检索轮次；是否补检索完全由主 Agent 决定。

---

## 现状与目标的差距

1. 主 Agent 的批处理入口会混合 graph/rag/passthrough 三类请求，职责边界不清。
2. 主 Agent 的批处理对 graph/rag 是“逐条单请求调用子 Agent”，没有把批量权下放给子 Agent。
3. 主 Agent 内存在“重复参数自动改写”逻辑，这属于隐式补检索策略。
4. 主 Agent 仍保留 passthrough 直接调用 MCP 的旁路，绕过子 Agent。

---

## 需要修改的地方

### A. 主 Agent 调用边界改造

1. 文件：app/agent/iterative_agent.py  
函数：_invoke_subagent_batch

修改目标：
- 一次调用只允许单一子 Agent 类型。
- 移除逐条单请求循环，改为整批请求一次性交给对应子 Agent。

修改要点：
- 保留 graph_requests 与 rag_requests 的分流。
- 若同时存在两类请求，不在本函数内混跑，改为由上层拆分后分两次调用。
- 每次调用把完整 requests 列表传入子 Agent，而不是 requests=[single_request]。

2. 文件：app/agent/iterative_agent.py  
函数：_run_subquery_batch_schedule

修改目标：
- 主 Agent 做“调度决策”，不做“子 Agent 内部批处理策略”。

修改要点：
- 保留主 Agent 的子问题分解、去重、补检索决策。
- 在下发阶段显式拆为两段：先图谱子 Agent 批次，再 RAG 子 Agent 批次（顺序串行）。
- 每段调用时保证单次只调一个子 Agent。

3. 文件：app/agent/iterative_agent.py  
函数：_run_mixed_subquery_schedule

修改目标：
- 与 _run_subquery_batch_schedule 保持同一调度约束。

修改要点：
- 禁止把混合工具列表一次传入 _invoke_subagent_batch。
- 先按工具类型拆分，再串行调用。

4. 文件：app/agent/iterative_agent.py  
函数：_invoke_tool

修改目标：
- 单工具调用路径只走子 Agent，不再保留主 Agent 直连 MCP 的分支（除非你明确保留某个非检索工具白名单）。

修改要点：
- graph 工具只走 GraphSubAgent。
- rag_search 只走 RagSubAgent。
- 非白名单工具直接报错并回到主 Agent 决策，不执行直连。

### B. 子 Agent 责任收敛改造

5. 文件：app/agent/graph_subagent.py  
函数：invoke_batch

修改目标：
- 子 Agent 只处理主 Agent 给定请求，不做额外补检索轮次。

修改要点：
- 保留参数规范化与批量执行。
- 明确禁止隐式追加“二次查询/兜底查询/替代工具调用”。
- 若无信号，仅原样返回 no_signal，由主 Agent 决定下一步。

6. 文件：app/agent/rag_subagent.py  
函数：invoke_batch

修改目标：
- 与图谱子 Agent 一致：只执行收到的问题，不自行扩检索。

修改要点：
- 保留批量调用和统计。
- 禁止内部发起额外请求。

### C. 主 Agent 补检索权统一

7. 文件：app/agent/iterative_agent.py  
函数：run（主循环）

修改目标：
- 保持现有终答门控、双库平衡、回溯机制作为唯一补检索入口。

修改要点：
- 所有补检索触发都写入主 Agent 决策轨迹。
- 子 Agent 返回 no_signal 后，必须回到主 Agent 决策层。

---

## 需要删除的地方

1. 文件：app/agent/iterative_agent.py  
函数：_invoke_subagent_batch

删除内容：
- graph_requests 的逐条循环调用（requests=[single_request]）。
- rag_requests 的逐条循环调用（requests=[single_request]）。

删除原因：
- 该模式把批处理决策放在主 Agent，违背“子 Agent 自主决定并发”的目标。

2. 文件：app/agent/iterative_agent.py  
函数：_invoke_subagent_batch

删除内容：
- passthrough_requests 直连 mcp_server.invoke_batch_json 的旁路。

删除原因：
- 绕过子 Agent，破坏主/子职责边界。

3. 文件：app/agent/iterative_agent.py  
函数：_rewrite_repeated_arguments 及其调用点

删除内容：
- _rewrite_repeated_arguments 方法。
- run 主循环中 called_times >= 1 时自动改写 arguments 的分支。

删除原因：
- 自动改写属于隐式补检索策略，应由主 Agent 显式决策，不应隐藏在执行路径。

---

## 最小落地顺序（避免大重写）

1. 先改 _invoke_subagent_batch：去掉逐条调用与 passthrough。  
2. 再改 _run_subquery_batch_schedule 与 _run_mixed_subquery_schedule：把混合请求拆成两次串行子 Agent 调用。  
3. 再删 _rewrite_repeated_arguments 及 run 内对应分支。  
4. 最后统一校验 graph_subagent.py 与 rag_subagent.py：确保无主动补检索逻辑。

---

## 验收标准（按你要求逐条验收）

1. 主 Agent 任一时刻只有一个子 Agent 调用在执行。  
2. 子 Agent 每次只处理收到的问题集合，不自行发起额外补检索。  
3. 主 Agent 成为唯一补检索决策源（回溯、双库平衡、终答门控）。  
4. 主 Agent 不再存在直连 MCP 的检索旁路。  
5. 调用链日志能清楚区分：主 Agent 决策日志与子 Agent 执行日志。
