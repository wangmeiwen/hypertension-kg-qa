const BASE_URL = import.meta.env.VITE_BACKEND_URL || "http://127.0.0.1:8000";
const ADMIN_TOKEN_KEY = "admin_token";

function getAdminToken() {
  return localStorage.getItem(ADMIN_TOKEN_KEY) || "";
}

function setAdminToken(token) {
  if (token) {
    localStorage.setItem(ADMIN_TOKEN_KEY, token);
  }
}

function clearAdminToken() {
  localStorage.removeItem(ADMIN_TOKEN_KEY);
}

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

export async function getReadinessStatus() {
  const response = await fetch(`${BASE_URL}/health/readiness`);
  const data = await response.json();
  if (data?.data) {
    return data.data;
  }

  throw new Error(data?.error ?? `请求错误: ${response.status}`);
}

export async function* streamProcess(input, userId) {
  const payload = { input, user_id: userId };
  const response = await fetch(`${BASE_URL}/process`, {
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

// ==================== 会话历史 API ====================

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

export async function updateSession(sessionId, title, userId) {
  const response = await fetch(`${BASE_URL}/sessions/${sessionId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title, user_id: userId })
  });
  const data = await response.json();
  if (!response.ok || !data?.ok) {
    throw new Error(data?.error || `请求错误: ${response.status}`);
  }
  return data;
}

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

export async function rollbackLastQuestion(sessionId, userId) {
  const response = await fetch(`${BASE_URL}/sessions/${sessionId}/rollback-last-question`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_id: userId })
  });
  const data = await response.json();
  if (!response.ok || !data?.ok) {
    throw new Error(data?.error || `请求错误: ${response.status}`);
  }
  return data.data;
}

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

export const uploadImage = uploadChatFile;

// ==================== 简化的管理员登录 API ====================

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

export function isAdminLoggedIn() {
  return Boolean(getAdminToken());
}

// ==================== 管理员后台 API ====================

export async function getAdminSessions(limit = 100, offset = 0) {
  const response = await adminFetch(`${BASE_URL}/admin/sessions?limit=${limit}&offset=${offset}`);
  const data = await response.json();
  if (!data?.ok) {
    throw new Error(data?.error || "获取会话列表失败");
  }
  return data;
}

export async function getAdminSessionMessages(sessionId) {
  const response = await adminFetch(`${BASE_URL}/admin/sessions/${sessionId}/messages`);
  const data = await response.json();
  if (!data?.ok) {
    throw new Error(data?.error || "获取会话消息失败");
  }
  return data;
}

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

export async function getAdminStats() {
  const response = await adminFetch(`${BASE_URL}/admin/stats`);
  const data = await response.json();
  if (!data?.ok) {
    throw new Error(data?.error || "获取统计数据失败");
  }
  return data.data;
}

// ==================== 文档上传 API ====================

export async function uploadDocument(file) {
  const formData = new FormData();
  formData.append("file", file);

  const response = await adminFetch(`${BASE_URL}/admin/documents/upload`, {
    method: "POST",
    body: formData
  });
  const data = await response.json();
  if (!data?.ok) {
    throw new Error(data?.error || "上传文档失败");
  }
  return data.data;
}

export async function getDocumentStats() {
  const response = await adminFetch(`${BASE_URL}/admin/documents/stats`);
  const data = await response.json();
  if (!data?.ok) {
    throw new Error(data?.error || "获取文档统计失败");
  }
  return data;
}

// ==================== Agent 可观测性 API ====================

export async function getAdminAgentObservabilitySummary(limit = 200) {
  const response = await adminFetch(`${BASE_URL}/admin/agent/observability-summary?limit=${limit}`);
  const data = await response.json();
  if (!data?.ok) {
    throw new Error(data?.error || "获取Agent可观测汇总失败");
  }
  return data.data;
}

export async function getAdminAgentObservabilityRuns(limit = 50) {
  const response = await adminFetch(`${BASE_URL}/admin/agent/observability-runs?limit=${limit}`);
  const data = await response.json();
  if (!data?.ok) {
    throw new Error(data?.error || "获取Agent运行记录失败");
  }
  return data.data;
}

// ==================== 知识图谱 API ====================

export async function getEntityDetail(entityId) {
  const response = await fetch(`${BASE_URL}/graph/entity/${entityId}`);
  const data = await response.json();
  if (!data?.ok) {
    throw new Error(data?.error || "获取实体详情失败");
  }
  return data;
}

// ==================== 知识图谱管理 API (管理员) ====================

export async function getGraphLabels() {
  const response = await adminFetch(`${BASE_URL}/admin/graph/labels`);
  const data = await response.json();
  if (!data?.ok) {
    throw new Error(data?.error || "获取节点标签失败");
  }
  return data.data;
}

export async function getGraphRelationshipTypes() {
  const response = await adminFetch(`${BASE_URL}/admin/graph/relationship-types`);
  const data = await response.json();
  if (!data?.ok) {
    throw new Error(data?.error || "获取关系类型失败");
  }
  return data.data;
}

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

export async function getGraphNode(nodeId) {
  const response = await adminFetch(`${BASE_URL}/admin/graph/nodes/${nodeId}`);
  const data = await response.json();
  if (!data?.ok) {
    throw new Error(data?.error || "获取节点详情失败");
  }
  return data.data;
}

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

export async function getGraphEmbedStatus() {
  const response = await adminFetch(`${BASE_URL}/admin/graph/embed-status`);
  const data = await response.json();
  if (!data?.ok) {
    throw new Error(data?.error || "获取嵌入状态失败");
  }
  return data.data;
}
