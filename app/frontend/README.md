# Frontend (React)

基于 React + Vite 构建的高血压知识图谱问答系统前端界面。

## 功能特性

### 用户功能
- **智能问答** - 基于 Agent 的智能问答，支持流式响应
- **图片上传** - 支持上传图片进行视觉问答
- **会话管理** - 自动保存会话历史，支持多会话切换
- **图谱可视化** - 知识图谱可视化展示

### 管理员功能
- **会话管理** - 查看、删除用户会话
- **Agent 观测** - 查看运行次数、耗时、工具成功率等指标
- **知识图谱管理** - 节点和关系的增删改查
- **文档解析** - Docling 文档解析和 OCR 处理
- **Readiness 状态条** - 只读展示 `ready/degraded/unavailable` 与原因

## 技术栈

- **React 18** - UI 框架
- **Vite** - 构建工具
- **CSS Variables** - 设计系统（医疗主题配色）

## 开发启动

```bash
# 安装依赖
npm install

# 启动开发服务器
npm run dev
```

默认访问地址：

```text
http://127.0.0.1:5173
```

## 环境配置

创建 `.env` 文件配置后端地址：

```bash
# 后端 API 地址
VITE_BACKEND_URL=http://127.0.0.1:8000
```

## 页面结构

### 主界面

```
┌─────────────────────────────────────────────────────────────┐
│  Header: HypertensionKG Agentic RAG                        │
│  [智能问答] [图谱可视化]                    [新对话] [管理]  │
├──────────────┬──────────────────────────────────────────────┤
│              │                                              │
│   会话列表    │              聊天消息区域                    │
│              │                                              │
│   - 会话1    │   ┌─────────────────────────────────────┐   │
│   - 会话2    │   │ 用户: 高血压的症状有哪些？           │   │
│   - 会话3    │   └─────────────────────────────────────┘   │
│              │   ┌─────────────────────────────────────┐   │
│   [+ 新会话]  │   │ 助手: 高血压的常见症状包括...       │   │
│              │   │ [思考过程] [工具调用追踪]            │   │
│              │   └─────────────────────────────────────┘   │
│              │                                              │
│              ├──────────────────────────────────────────────┤
│              │  [📷] 输入框...                      [发送]   │
└──────────────┴──────────────────────────────────────────────┘
```

### 管理员后台

```
┌─────────────────────────────────────────────────────────────┐
│  管理员后台                                    [刷新] [退出] │
│  Readiness: [READY|DEGRADED|UNAVAILABLE]  最近检查: xx:xx   │
├─────────────────────────────────────────────────────────────┤
│  [会话管理] [Agent观测] [图谱管理] [Docling解析]            │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│                      内容区域                                │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

## 核心组件

### App.jsx
主应用组件，包含：
- 会话状态管理
- 消息列表渲染
- 图片上传处理
- 流式响应处理

### SessionSidebar.jsx
会话侧边栏组件：
- 会话列表展示
- 创建/切换会话
- 会话持久化

### AdminDashboard.jsx
管理员后台组件：
- 多标签页切换
- Readiness 状态条（自动刷新 + 手动刷新）
- Agent 观测表格与详情
- 图谱管理界面

### GraphView.jsx
知识图谱可视化组件：
- D3.js 力导向图
- 节点搜索和筛选

## API 接口

### 问答接口
```javascript
// 流式问答
streamProcessWithSession(input, userId, sessionId, imageData)

// 图片上传
uploadImage(file)
```

### 会话接口
```javascript
createSession(userId, title)
listSessions(userId)
getSession(sessionId)
updateSession(sessionId, title)
deleteSession(sessionId)
```

### 管理员接口
```javascript
adminLogin(username, password)
adminLogout()
getAdminSessions(limit, offset)
getReadinessStatus()
getAdminAgentObservabilitySummary(limit)
getAdminAgentObservabilityRuns(limit)
getGraphNodes({ label, keyword, limit, offset })
createGraphNode(label, properties)
doclingParseDocument(file)
```

### Readiness 接口说明

- `GET /health/readiness`
- 返回分级：`ready`、`degraded`、`unavailable`
- 当前端收到 `unavailable` 时，状态条会展示原因，便于运维定位依赖故障

## 设计系统

使用 CSS Variables 定义的设计系统：

```css
/* 主色调 - 医疗青色 */
--primary-500: #0891B2;
--primary-600: #0E7490;

/* CTA 颜色 - 健康绿 */
--cta-500: #22C55E;

/* 背景 */
--bg-primary: #F0FDFA;
--bg-white: #FFFFFF;

/* 文字 */
--text-primary: #134E4A;
--text-muted: #64748B;
```

## 图片上传功能

支持上传图片进行视觉问答：

1. 点击输入框左侧的图片图标
2. 选择图片文件（支持 jpg、png、gif、webp、bmp）
3. 图片大小限制：10MB
4. 上传后显示预览，可删除
5. 输入问题后发送，图片会一起发送给 LLM

## 注意事项

1. 确保后端服务已启动
2. 首次访问会自动创建用户 ID（存储在 localStorage）
3. 管理员默认账号：admin / admin123
4. 图片上传需要后端支持视觉能力的 LLM
