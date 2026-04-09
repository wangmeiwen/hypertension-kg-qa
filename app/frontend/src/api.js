// 模块职责：统一封装前后端接口调用、管理员鉴权和错误处理。
const BASE_URL = import.meta.env.VITE_BACKEND_URL || "http://127.0.0.1:8000";
const ADMIN_TOKEN_KEY = "admin_token";

// 读取本地缓存的管理员 token，不存在时返回空字符串。
function getAdminToken() {
  return localStorage.getItem(ADMIN_TOKEN_KEY) || "";
}

// 写入管理员 token，空值不写入。
function setAdminToken(token) {
  if (token) {
    localStorage.setItem(ADMIN_TOKEN_KEY, token);
  }
}

// 清理本地缓存的管理员 token。
function clearAdminToken() {
  localStorage.removeItem(ADMIN_TOKEN_KEY);
}

// 带鉴权头发送管理员请求，并统一处理未登录状态。
async function adminFetch(url, options = {}) {
  const token = getAdminToken();
  const headers = {
    ...(options.headers || {}),
    ...(token ? { Authorization: `Bearer ${token}` } : {})
  };
  const response = await fetch(url, { ...options, headers });
  let preview = null;
  try {
    preview = await response.clone().json();
  } catch {
    preview = null;
  }

  if (response.status === 401 || String(preview?.error || "").includes("未登录")) {
    clearAdminToken();
    throw new Error("未登录");
  }
  return response;
}

// 获取图谱可视化数据，支持关键词过滤与返回数量限制。
export async function fetchGraph(keyword = "", limit = 200) {
  const params = new URLSearchParams();
  if (keyword.trim()) {
    params.set("keyword", keyword.trim());
  }
  params.set("limit", String(limit));
  const response = await fetch(`${BASE_URL}/graph/visualization?${params.toString()}`);
  const data = await response.json();
  if (!response.ok || !data?.ok) {
    throw new Error(data?.error || `请求错误: ${response.status}`);
  }
  return data;
}

// 获取后端 readiness 状态，用于展示依赖可用性。
export async function getReadinessStatus() {
  const response = await fetch(`${BASE_URL}/health/readiness`);
  const data = await response.json();
  if (data?.data) {
    return data.data;
  }

  throw new Error(data?.error ?? `请求错误: ${response.status}`);
}

// ==================== 会话历史 API ====================

// 创建新会话，userId 为用户标识，title 为会话标题。
export async function createSession(userId, title = "新会话") {
  const response = await fetch(`${BASE_URL}/sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_id: userId, title })
  });
  const data = await response.json();
  if (!response.ok || !data?.ok) {
    throw new Error(data?.error || `请求错误: ${response.status}`);
  }
  return data.data;
}

// 拉取用户会话列表，limit 控制返回数量。
export async function listSessions(userId, limit = 50) {
  const params = new URLSearchParams();
  params.set("user_id", userId);
  params.set("limit", String(limit));
  const response = await fetch(`${BASE_URL}/sessions?${params.toString()}`);
  const data = await response.json();
  if (!response.ok || !data?.ok) {
    throw new Error(data?.error || `请求错误: ${response.status}`);
  }
  return data.data;
}

// 按会话 ID 获取完整会话详情和消息。
export async function getSession(sessionId, userId) {
  const params = new URLSearchParams();
  params.set("user_id", userId);
  const response = await fetch(`${BASE_URL}/sessions/${sessionId}?${params.toString()}`);
  const data = await response.json();
  if (!response.ok || !data?.ok) {
    throw new Error(data?.error || `请求错误: ${response.status}`);
  }
  return data.data;
}

// 更新指定会话标题。
export async function updateSession(sessionId, userId, title) {
  const response = await fetch(`${BASE_URL}/sessions/${sessionId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_id: userId, title })
  });
  const data = await response.json();
  if (!response.ok || !data?.ok) {
    throw new Error(data?.error || `请求错误: ${response.status}`);
  }
  return data;
}

// 删除指定会话。
export async function deleteSession(sessionId, userId) {
  const params = new URLSearchParams();
  params.set("user_id", userId);
  const response = await fetch(`${BASE_URL}/sessions/${sessionId}?${params.toString()}`, {
    method: "DELETE"
  });
  const data = await response.json();
  if (!response.ok || !data?.ok) {
    throw new Error(data?.error || `请求错误: ${response.status}`);
  }
  return data;
}

// 将会话回滚到指定问题消息节点。
export async function rollbackToQuestion(sessionId, userId, messageId) {
  const response = await fetch(`${BASE_URL}/sessions/${sessionId}/rollback-to-question`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_id: userId, message_id: messageId })
  });
  const data = await response.json();
  if (!response.ok || !data?.ok) {
    throw new Error(data?.error || `请求错误: ${response.status}`);
  }
  return data.data;
}

// 取消正在执行的问答任务。
export async function cancelQaJob(jobId, userId, sessionId) {
  const response = await fetch(`${BASE_URL}/qa/jobs/${jobId}/cancel`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_id: userId, session_id: sessionId })
  });
  const data = await response.json();
  if (!response.ok || !data?.ok) {
    throw new Error(data?.error || `请求错误: ${response.status}`);
  }
  return data;
}

// 以流式方式发送问题并逐块产出事件，供聊天界面实时消费。
export async function* streamProcessWithSession(input, userId, sessionId, imageData = null, fileName = "") {
  const payload = { input, user_id: userId, session_id: sessionId };
  if (imageData) {
    payload.image = imageData;
  }
  if (fileName) {
    payload.file_name = fileName;
  }
  const response = await fetch(`${BASE_URL}/process_with_session`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });

  if (!response.ok || !response.body) {
    yield { type: "done", msg: `请求错误: ${response.status}` };
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let index = buffer.indexOf("\n");
    while (index !== -1) {
      const line = buffer.slice(0, index).trim();
      buffer = buffer.slice(index + 1);
      if (line) {
        try {
          yield JSON.parse(line);
        } catch {
          yield { type: "msg", msg: line };
        }
      }
      index = buffer.indexOf("\n");
    }
  }

  const tail = buffer.trim();
  if (tail) {
    try {
      yield JSON.parse(tail);
    } catch {
      yield { type: "msg", msg: tail };
    }
  }
}

// ==================== 文件上传 API ====================

// 上传聊天附件，返回服务端识别结果（包含是否图片等信息）。
export async function uploadChatFile(file) {
  const formData = new FormData();
  formData.append("file", file);

  const response = await fetch(`${BASE_URL}/upload_file`, {
    method: "POST",
    body: formData
  });

  const data = await response.json();
  if (!data?.ok) {
    throw new Error(data?.error || "文件上传失败");
  }
  return data.data;
}

// ==================== 简化的管理员登录 API ====================

// 管理员登录并缓存 token。
export async function adminLogin(username, password) {
  const response = await fetch(`${BASE_URL}/admin/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password })
  });
  const data = await response.json();
  if (!data?.ok) {
    throw new Error(data?.error || "登录失败");
  }
  setAdminToken(data?.token || "");
  return data;
}

// 检查当前管理员登录状态，失效时自动清理 token。
export async function checkAdminStatus() {
  const response = await adminFetch(`${BASE_URL}/admin/status`);
  const data = await response.json();
  if (!data?.ok) {
    clearAdminToken();
    return false;
  }
  if (!data?.logged_in) {
    clearAdminToken();
  }
  return Boolean(data?.logged_in);
}

// 管理员退出登录，并确保本地 token 被清理。
export async function adminLogout() {
  try {
    const response = await adminFetch(`${BASE_URL}/admin/logout`, {
      method: "POST"
    });
    return await response.json();
  } finally {
    clearAdminToken();
  }
}

// ==================== 管理员后台 API ====================

// 分页获取后台会话列表。
export async function getAdminSessions(limit = 100, offset = 0) {
  const response = await adminFetch(`${BASE_URL}/admin/sessions?limit=${limit}&offset=${offset}`);
  const data = await response.json();
  if (!data?.ok) {
    throw new Error(data?.error || "获取会话列表失败");
  }
  return data;
}

// 获取指定会话的消息列表。
export async function getAdminSessionMessages(sessionId) {
  const response = await adminFetch(`${BASE_URL}/admin/sessions/${sessionId}/messages`);
  const data = await response.json();
  if (!data?.ok) {
    throw new Error(data?.error || "获取会话消息失败");
  }
  return data;
}

// 获取指定会话的可观测性事件与流转数据。
export async function getAdminSessionObservability(sessionId) {
  const response = await adminFetch(`${BASE_URL}/admin/sessions/${sessionId}/observability`);
  const data = await response.json();
  if (!data?.ok) {
    throw new Error(data?.error || "获取会话观测详情失败");
  }
  return data;
}

// 删除后台指定会话。
export async function deleteAdminSession(sessionId) {
  const response = await adminFetch(`${BASE_URL}/admin/sessions/${sessionId}`, {
    method: "DELETE"
  });
  const data = await response.json();
  if (!data?.ok) {
    throw new Error(data?.error || "删除会话失败");
  }
  return data;
}

// 清空后台指定会话的历史消息。
export async function clearAdminSessionMessages(sessionId) {
  const response = await adminFetch(`${BASE_URL}/admin/sessions/${sessionId}/messages`, {
    method: "DELETE"
  });
  const data = await response.json();
  if (!data?.ok) {
    throw new Error(data?.error || "清空会话历史记录失败");
  }
  return data;
}

// 获取后台统计信息。
export async function getAdminStats() {
  const response = await adminFetch(`${BASE_URL}/admin/stats`);
  const data = await response.json();
  if (!data?.ok) {
    throw new Error(data?.error || "获取统计数据失败");
  }
  return data.data;
}

// ==================== Agent 可观测性 API ====================

// 获取 Agent 可观测性汇总指标。
export async function getAdminAgentObservabilitySummary(limit = 200) {
  const response = await adminFetch(`${BASE_URL}/admin/agent/observability-summary?limit=${limit}`);
  const data = await response.json();
  if (!data?.ok) {
    throw new Error(data?.error || "获取Agent可观测汇总失败");
  }
  return data.data;
}

// 获取 Agent 最近运行记录。
export async function getAdminAgentObservabilityRuns(limit = 50) {
  const response = await adminFetch(`${BASE_URL}/admin/agent/observability-runs?limit=${limit}`);
  const data = await response.json();
  if (!data?.ok) {
    throw new Error(data?.error || "获取Agent运行记录失败");
  }
  return data.data;
}

// ==================== 知识图谱 API ====================

// 获取单个实体的详细信息和关系。
export async function getEntityDetail(entityId) {
  const response = await fetch(`${BASE_URL}/graph/entity/${entityId}`);
  const data = await response.json();
  if (!data?.ok) {
    throw new Error(data?.error || "获取实体详情失败");
  }
  return data;
}

// ==================== 知识图谱管理 API (管理员) ====================

// 获取图谱所有节点标签。
export async function getGraphLabels() {
  const response = await adminFetch(`${BASE_URL}/admin/graph/labels`);
  const data = await response.json();
  if (!data?.ok) {
    throw new Error(data?.error || "获取节点标签失败");
  }
  return data.data;
}

// 获取图谱关系类型列表。
export async function getGraphRelationshipTypes() {
  const response = await adminFetch(`${BASE_URL}/admin/graph/relationship-types`);
  const data = await response.json();
  if (!data?.ok) {
    throw new Error(data?.error || "获取关系类型失败");
  }
  return data.data;
}

// 按标签和关键字分页查询节点。
export async function getGraphNodes({ label = "", keyword = "", limit = 100, offset = 0 }) {
  const params = new URLSearchParams();
  if (label) params.append("label", label);
  if (keyword) params.append("keyword", keyword);
  params.append("limit", limit);
  params.append("offset", offset);
  
  const response = await adminFetch(`${BASE_URL}/admin/graph/nodes?${params}`);
  const data = await response.json();
  if (!data?.ok) {
    throw new Error(data?.error || "获取节点列表失败");
  }
  return data;
}

// 查询单个节点详情。
export async function getGraphNode(nodeId) {
  const response = await adminFetch(`${BASE_URL}/admin/graph/nodes/${nodeId}`);
  const data = await response.json();
  if (!data?.ok) {
    throw new Error(data?.error || "获取节点详情失败");
  }
  return data.data;
}

// 创建节点，label 为标签，properties 为属性对象。
export async function createGraphNode(label, properties) {
  const response = await adminFetch(`${BASE_URL}/admin/graph/nodes`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ label, properties })
  });
  const data = await response.json();
  if (!data?.ok) {
    throw new Error(data?.error || "创建节点失败");
  }
  return data.data;
}

// 更新节点属性。
export async function updateGraphNode(nodeId, properties) {
  const response = await adminFetch(`${BASE_URL}/admin/graph/nodes/${nodeId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ properties })
  });
  const data = await response.json();
  if (!data?.ok) {
    throw new Error(data?.error || "更新节点失败");
  }
  return data.data;
}

// 删除节点及其关联关系。
export async function deleteGraphNode(nodeId) {
  const response = await adminFetch(`${BASE_URL}/admin/graph/nodes/${nodeId}`, {
    method: "DELETE"
  });
  const data = await response.json();
  if (!data?.ok) {
    throw new Error(data?.error || "删除节点失败");
  }
  return data;
}

// 创建关系，sourceId/targetId 为两端节点 ID。
export async function createGraphRelationship(sourceId, targetId, relType, properties = {}) {
  const response = await adminFetch(`${BASE_URL}/admin/graph/relationships`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      source_id: sourceId,
      target_id: targetId,
      rel_type: relType,
      properties
    })
  });
  const data = await response.json();
  if (!data?.ok) {
    throw new Error(data?.error || "创建关系失败");
  }
  return data.data;
}

// 删除关系。
export async function deleteGraphRelationship(relId) {
  const response = await adminFetch(`${BASE_URL}/admin/graph/relationships/${relId}`, {
    method: "DELETE"
  });
  const data = await response.json();
  if (!data?.ok) {
    throw new Error(data?.error || "删除关系失败");
  }
  return data;
}

// 执行管理员 Cypher 查询。
export async function executeCypherQuery(query) {
  const response = await adminFetch(`${BASE_URL}/admin/graph/execute-cypher`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query })
  });
  const data = await response.json();
  if (!data?.ok) {
    throw new Error(data?.error || "执行查询失败");
  }
  return data.data;
}

// 上传文件并触发图谱抽取入库。
export async function extractAndImportGraph(file, maxChars = 12000) {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("max_chars", String(maxChars));

  const response = await adminFetch(`${BASE_URL}/admin/graph/extract-and-import`, {
    method: "POST",
    body: formData
  });
  const data = await response.json();
  if (!data?.ok) {
    throw new Error(data?.error || "文件抽取导入失败");
  }
  return data.data;
}

// ==================== Docling 文档解析 API ====================

// 使用 Docling 解析文档并返回结构化内容。
export async function doclingParseDocument(file) {
  const formData = new FormData();
  formData.append("file", file);

  const response = await adminFetch(`${BASE_URL}/admin/docling/parse`, {
    method: "POST",
    body: formData
  });

  const data = await response.json();
  if (!data?.ok) {
    throw new Error(data?.error || "Docling 解析失败");
  }
  return data.data;
}

// 使用 Docling 解析文档后直接写入向量库。
export async function doclingParseAndEmbed(file) {
  const formData = new FormData();
  formData.append("file", file);

  const response = await adminFetch(`${BASE_URL}/admin/docling/parse-and-embed`, {
    method: "POST",
    body: formData
  });

  const data = await response.json();
  if (!data?.ok) {
    throw new Error(data?.error || "Docling 解析并嵌入失败");
  }
  return data.data;
}

// ==================== 知识图谱嵌入 Milvus API ====================

// 将图谱实体/关系嵌入到 Milvus，labels 为空时嵌入全部标签。
export async function embedGraphToMilvus(labels = null, batchSize = 100) {
  const payload = {};
  if (labels) payload.labels = labels;
  if (batchSize) payload.batch_size = batchSize;

  const response = await adminFetch(`${BASE_URL}/admin/graph/embed-to-milvus`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });

  const data = await response.json();
  if (!data?.ok) {
    throw new Error(data?.error || "知识图谱嵌入失败");
  }
  return data.data;
}

// 查询图谱嵌入状态和统计信息。
export async function getGraphEmbedStatus() {
  const response = await adminFetch(`${BASE_URL}/admin/graph/embed-status`);
  const data = await response.json();
  if (!data?.ok) {
    throw new Error(data?.error || "获取嵌入状态失败");
  }
  return data.data;
}
