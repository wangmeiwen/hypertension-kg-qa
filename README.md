# HypertensionKG QA System

高血压医学知识图谱问答系统，采用 Agentic RAG 架构，融合：

- Neo4j（知识图谱）
- Milvus（向量检索）
- FastAPI + React（服务与界面）

## 核心能力

- 多轮问答与会话记忆
- 知识图谱检索与可视化
- RAG 向量检索
- 管理后台（会话管理、图谱管理、文档处理）
- 服务 readiness 分级（ready/degraded/unavailable）

## 项目结构重构说明

目录重构与文件归位方案见：

- [file.md](file.md)

该文档定义了：

- 文件夹重命名建议
- 各目录应存放的文件类型
- 顶层目录顺序规范
- 分阶段迁移策略

## 当前目录（现状）

```text
.
├─ app/                # 主应用代码（后端、前端、Agent、MCP）
├─ config/             # MCP 等配置
├─ data/               # 数据与图谱元数据
├─ model/              # 本地模型
├─ scripts/            # 运维与开发脚本
├─ tests/              # 测试与工具脚本
├─ docs/               # 文档
├─ uploads/            # 上传目录
└─ requirements.txt
```

## 文件级说明（按目录）

### 根目录

- README.md: 项目总览、启动方式、运维说明。
- AGENT_IMPROVEMENT_ROADMAP.md: Agent 能力迭代路线图与阶段目标。
- requirements.txt: Python 依赖清单。
- file.md: 目录重构规范与迁移策略说明。

### app 目录

#### app

- app/__init__.py: Python 包标记文件。

#### app/agent

- app/agent/__init__.py: agent 子包标记文件。
- app/agent/agent_state.py: Agent 运行状态结构定义（状态字段、流程上下文）。
- app/agent/iterative_agent.py: 迭代式 Agent 主执行器（规划、调用工具、反思、终答）。
- app/agent/langgraph_nodes.py: LangGraph 节点编排与问答入口 process_query。
- app/agent/llm_client.py: LLM 调用封装（请求、响应结构、token 等）。
- app/agent/session_memory.py: 会话记忆压缩与上下文构建。

#### app/backend

- app/backend/__init__.py: backend 子包标记文件。
- app/backend/main.py: FastAPI 主应用与核心 API 路由（问答、管理、readiness、图谱、文档）。

#### app/backend/repositories

- app/backend/repositories/__init__.py: repositories 子包标记文件。
- app/backend/repositories/database.py: MySQL 数据访问层（会话、消息、统计等 CRUD）。

#### app/common

- app/common/__init__.py: common 子包标记文件。
- app/common/auth_simple.py: 管理员认证与 token 会话状态管理。
- app/common/config.py: 环境变量与系统配置读取。
- app/common/db_manager.py: Neo4j/Milvus 端口可达性检查。
- app/common/msg_queue.py: 流式消息队列管理与并发隔离键构建。
- app/common/path_utils.py: 路径处理与项目路径辅助方法。
- app/common/perf_logger.py: 统一性能日志记录与落盘。

#### app/document_parser

- app/document_parser/__init__.py: document_parser 子包标记文件。
- app/document_parser/docling_parser.py: 基于 Docling 的多格式解析。
- app/document_parser/docx_parser.py: DOCX 专用解析逻辑。

#### app/frontend

- app/frontend/__init__.py: frontend 子包标记文件。
- app/frontend/index.html: Vite 前端入口 HTML。
- app/frontend/package.json: 前端依赖与 npm 脚本配置。
- app/frontend/package-lock.json: npm 锁文件，保证依赖版本可复现。
- app/frontend/vite.config.js: Vite 构建与开发服务器配置。
- app/frontend/README.md: 前端模块说明文档。

#### app/frontend/src

- app/frontend/src/main.jsx: React 挂载入口。
- app/frontend/src/App.jsx: 主页面容器（聊天、登录、后台入口）。
- app/frontend/src/api.js: 前后端 API 调用封装。
- app/frontend/src/styles.css: 全局样式与设计系统。
- app/frontend/src/SessionSidebar.jsx: 会话侧栏（列表、切换、删除）。
- app/frontend/src/AdminDashboard.jsx: 管理后台页面（会话管理、Agent 观测、readiness 状态条）。
- app/frontend/src/GraphView.jsx: 图谱可视化展示组件。
- app/frontend/src/GraphManager.jsx: 图谱管理面板（节点/关系 CRUD、执行查询）。
- app/frontend/src/DoclingParser.jsx: Docling 文档解析管理页面。
- app/frontend/src/EntityDetailModal.jsx: 实体详情弹窗与关系信息展示。
- app/frontend/src/ToolTracePanel.jsx: 工具调用轨迹展示组件。

#### app/knowledge_graph

- app/knowledge_graph/__init__.py: knowledge_graph 子包标记文件。
- app/knowledge_graph/extract_graph_data_utils.py: 文本到图谱结构抽取逻辑。
- app/knowledge_graph/neo4j_manager.py: Neo4j 读写与管理封装。

#### app/mcp

- app/mcp/__init__.py: mcp 子包标记文件。
- app/mcp/server.py: MCP 服务器与工具调用协议实现。
- app/mcp/tool_manager.py: 按 YAML 动态注册 MCP 工具。
- app/mcp/tools.py: 内置工具实现（kg_query/rag_search/kg_entity_relation_search）与运行态检查。

#### app/rag_core

- app/rag_core/__init__.py: rag_core 子包标记文件。
- app/rag_core/document_embedding.py: 文档分块与向量化入库流程。
- app/rag_core/kg_milvus_store.py: 知识图谱实体关系向量库封装。
- app/rag_core/vector_store.py: 通用向量检索与存储能力。

### config 目录

- config/mcp_tools.yaml: MCP 工具注册配置与开关。

### tests 目录（维护脚本）

- tests/drop_kg_milvus_collection.py: 清理知识图谱专用 Milvus 集合。
- tests/drop_milvus_collection.py: 清理通用 Milvus 集合。
- tests/drop_neo4j_database.py: 清空 Neo4j 图数据库。

## 快速启动

### 1) 安装依赖

```bash
pip install -r requirements.txt
```

### 2) 启动后端

```bash
uvicorn app.backend.main:app --reload --host 0.0.0.0 --port 8000
```

### 3) 启动前端

```bash
cd app/frontend
npm install
npm run dev
```

## Readiness 与可用性

后端提供 `GET /health/readiness`，返回服务就绪分级：

- `ready`：核心依赖可用。
- `degraded`：核心可用，但部分可选能力异常。
- `unavailable`：核心依赖未就绪，核心问答入口会返回 `503`。

核心问答入口（`/process`、`/agent/run`、`/process_with_session`）在 `unavailable` 时会阻断请求，避免“服务在线但不可答”。

管理后台已接入只读 readiness 状态条：

- 展示 `READY/DEGRADED/UNAVAILABLE` 徽标
- 展示最近检查时间与原因列表
- 支持手动刷新，并每 30 秒自动刷新

## 依赖服务

- Neo4j 5.x（默认 bolt://localhost:7687）
- MySQL 8.x（默认 localhost:3306）
- Milvus 2.x（默认 localhost:19530）

## 后续维护建议

1. 新功能优先遵循 [file.md](file.md) 的目标目录规范。
2. 重构采用分批迁移，避免一次性改动过大。
3. 迁移后同步更新启动命令、import 路径和部署脚本。
