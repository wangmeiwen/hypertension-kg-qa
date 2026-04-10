import { useState, useEffect } from "react";
import {
  getAdminSessions,
  getAdminSessionMessages,
  getAdminSessionObservability,
  deleteAdminSession,
  clearAdminSessionMessages,
  getAdminStats,
  getAdminAgentObservabilitySummary,
  getAdminAgentObservabilityRuns,
  getReadinessStatus
} from "./api";
import GraphManager from "./GraphManager";
import DoclingParser from "./DoclingParser";

// 模块职责：管理员后台总面板，包含会话管理、Agent观测、图谱管理和文本嵌入入口。
export default function AdminDashboard({ onLogout }) {
  const [sessions, setSessions] = useState([]);
  const [total, setTotal] = useState(0);
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [selectedSession, setSelectedSession] = useState(null);
  const [sessionMessages, setSessionMessages] = useState([]);
  const [sessionObservability, setSessionObservability] = useState([]);
  const [selectedTurnId, setSelectedTurnId] = useState("");
  const [selectedFlowEvent, setSelectedFlowEvent] = useState(null);
  const [expandedTextMap, setExpandedTextMap] = useState({});
  const [showDetail, setShowDetail] = useState(false);

  // 标签页状态
  const [activeTab, setActiveTab] = useState("sessions");

  // Agent 可观测性状态
  const [agentObsSummary, setAgentObsSummary] = useState(null);
  const [agentObsRuns, setAgentObsRuns] = useState([]);
  const [agentObsLoading, setAgentObsLoading] = useState(false);

  // 服务 readiness 状态
  const [readinessData, setReadinessData] = useState(null);
  const [readinessLoading, setReadinessLoading] = useState(false);
  const [readinessError, setReadinessError] = useState("");

  useEffect(() => {
    loadData();
    loadReadiness();

    const timer = window.setInterval(() => {
      loadReadiness();
    }, 30000);

    return () => {
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    if (activeTab === "agent-observability") {
      loadAgentObservability();
    }
  }, [activeTab]);

  // 加载会话列表和基础统计信息。
  const loadData = async () => {
    setLoading(true);
    setError("");
    try {
      const [sessionsData, statsData] = await Promise.all([
        getAdminSessions(),
        getAdminStats()
      ]);
      setSessions(sessionsData.data);
      setTotal(sessionsData.total);
      setStats(statsData);
    } catch (err) {
      setError(err.message);
      if (err.message.includes("未登录")) {
        onLogout();
      }
    } finally {
      setLoading(false);
    }
  };

  // 加载 Agent 观测汇总与最近运行记录。
  const loadAgentObservability = async () => {
    setAgentObsLoading(true);
    try {
      const [summary, runs] = await Promise.all([
        getAdminAgentObservabilitySummary(300),
        getAdminAgentObservabilityRuns(50)
      ]);
      setAgentObsSummary(summary);
      setAgentObsRuns(runs);
    } catch (err) {
      setError(err.message);
      if (err.message.includes("未登录")) {
        onLogout();
      }
    } finally {
      setAgentObsLoading(false);
    }
  };

  // 拉取后端 readiness 状态并更新状态条。
  const loadReadiness = async () => {
    setReadinessLoading(true);
    setReadinessError("");
    try {
      const readiness = await getReadinessStatus();
      setReadinessData(readiness);
    } catch (err) {
      setReadinessError(err.message);
    } finally {
      setReadinessLoading(false);
    }
  };

  // 查看会话详情：同时拉取消息和流转观测数据。
  const handleViewSession = async (session) => {
    try {
      const [messageData, observabilityData] = await Promise.all([
        getAdminSessionMessages(session.id),
        getAdminSessionObservability(session.id)
      ]);
      setSelectedSession(messageData.session);
      setSessionMessages(messageData.messages);
      setSessionObservability(observabilityData.turns || []);
      setSelectedTurnId(observabilityData.turns?.[0]?.turn_id || "");
      setSelectedFlowEvent(null);
      setExpandedTextMap({});
      setShowDetail(true);
    } catch (err) {
      setError(err.message);
    }
  };

  // 删除指定会话并在详情面板中同步移除。
  const handleDeleteSession = async (sessionId) => {
    if (!confirm("确定要删除这个会话吗？此操作不可恢复。")) return;
    try {
      await deleteAdminSession(sessionId);
      loadData();
      if (selectedSession?.id === sessionId) {
        setShowDetail(false);
        setSelectedSession(null);
        setSessionMessages([]);
      }
    } catch (err) {
      setError(err.message);
    }
  };

  // 清空指定会话历史消息。
  const handleClearSessionHistory = async (sessionId) => {
    if (!confirm("确定要清空这个会话的历史记录吗？此操作会删除全部消息内容。")) return;
    try {
      await clearAdminSessionMessages(sessionId);
      await loadData();
      if (selectedSession?.id === sessionId) {
        setSessionMessages([]);
      }
    } catch (err) {
      setError(err.message);
    }
  };

  // 统一格式化完整时间。
  const formatTime = (timestamp) => {
    if (!timestamp) return "-";
    return new Date(timestamp).toLocaleString("zh-CN");
  };

  // 格式化事件时间，仅展示时分秒。
  const formatEventTime = (timestamp) => {
    if (!timestamp) return "-";
    const date = new Date(timestamp);
    if (Number.isNaN(date.getTime())) return "-";
    return date.toLocaleTimeString("zh-CN", {
      hour12: false,
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit"
    });
  };

  // 截断过长文本用于表格展示。
  const truncateText = (text, maxLength = 50) => {
    if (!text) return "";
    return text.length > maxLength ? text.slice(0, maxLength) + "..." : text;
  };

  const formatDurationMs = (value) => {
    const numeric = Number(value || 0);
    if (!Number.isFinite(numeric) || numeric <= 0) return "-";
    if (numeric < 1000) return `${numeric.toFixed(0)} ms`;
    return `${(numeric / 1000).toFixed(2)} s`;
  };

  const splitPreviewLines = (text) =>
    String(text || "")
      .split(/\r?\n/)
      .map((item) => item.trim())
      .filter(Boolean);

  const selectedTurn = sessionObservability.find((turn) => turn.turn_id === selectedTurnId) || sessionObservability[0] || null;
  const flowLaneNames = ["主控编排", "图谱代理", "检索代理"];

  // 切换某个可展开文本块的展开状态。
  const toggleExpandedText = (key) => {
    setExpandedTextMap((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  // 渲染可展开文本，避免长文本挤压布局。
  const renderExpandableText = (text, key, maxLength = 160) => {
    const value = String(text || "");
    if (!value) {
      return <span className="empty-inline">暂无内容</span>;
    }
    const expanded = Boolean(expandedTextMap[key]);
    const shouldTruncate = value.length > maxLength;
    const displayText = expanded || !shouldTruncate ? value : `${value.slice(0, maxLength)}...`;
    return (
      <div className="expandable-text-block">
        <div className="expandable-text-content">{displayText}</div>
        {shouldTruncate && (
          <button className="expand-toggle-btn" onClick={() => toggleExpandedText(key)}>
            {expanded ? "收起" : "展开全文"}
          </button>
        )}
      </div>
    );
  };

  // 将列表型观测数据逐行渲染为可读内容。
  const renderListLines = (items, prefix) => {
    if (!Array.isArray(items) || items.length === 0) {
      return <div className="empty-messages">该阶段没有可用观测数据</div>;
    }
    return items.map((item, index) => (
      <div key={`${prefix}-${index}`} className="human-readable-line">
        {typeof item === "string" ? item : item?.detail || item?.title || "-"}
      </div>
    ));
  };

  // 将错误类型计数字典格式化为一行文本。
  const formatErrorTypes = (errors) => {
    if (!errors || Object.keys(errors).length === 0) {
      return "-";
    }
    return Object.entries(errors).map(([k, v]) => `${k}:${v}`).join(" | ");
  };

  const selectedTurnQuestion = String(selectedTurn?.question || "").trim();
  const selectedTurnMemoryCompression = Array.isArray(selectedTurn?.memory_compression) ? selectedTurn.memory_compression : [];
  const selectedTurnProjectionStage = selectedTurnMemoryCompression.find(
    (item) => item && typeof item === "object" && String(item.stage_name || "").trim() === "本轮记忆投影"
  ) || null;
  const selectedTurnProjectionLines = Array.isArray(selectedTurnProjectionStage?.content_lines)
    ? selectedTurnProjectionStage.content_lines
    : [];
  const selectedTurnProjectionCountLine = selectedTurnProjectionLines.find(
    (line) => String(line || "").includes("写入检索记录数：")
  );
  const selectedTurnProjectedRecordCount = Number(
    String(selectedTurnProjectionCountLine || "").split("写入检索记录数：")[1] || 0
  ) || 0;
  const selectedTurnProjectionPreviewLine = selectedTurnProjectionLines.find(
    (line) => String(line || "").includes("检索预览：")
  );
  const selectedTurnProjectionPreviews = String(selectedTurnProjectionPreviewLine || "")
    .split("检索预览：")[1]
    ?.split("；")
    .map((item) => item.trim())
    .filter((item) => item && item !== "无")
    .slice(0, 3) || [];
  const selectedTurnHistoryRetrievalLines = splitPreviewLines(selectedTurn?.memory_before?.retrieval_question_index_text);
  const selectedTurnFilteredHistoryRetrievalText = selectedTurnHistoryRetrievalLines
    .filter((line) => !selectedTurnQuestion || !line.includes(selectedTurnQuestion))
    .join("\n");

  const selectedTurnToolObservations = Array.isArray(selectedTurn?.tool_observations) ? selectedTurn.tool_observations : [];
  const selectedTurnToolSummary = selectedTurnToolObservations.reduce(
    (acc, observation) => {
      const row = observation && typeof observation === "object" ? observation : {};
      const agent = String(row.agent || "").trim().toLowerCase();
      const toolName = String(row.tool_name || row.requested_tool_name || "").trim().toLowerCase();
      const action = String(row.action || "").trim().toLowerCase();

      acc.total += 1;
      if (agent === "kg" || toolName.includes("kg_")) {
        acc.kg += 1;
      } else if (agent === "rag" || toolName.includes("rag")) {
        acc.rag += 1;
      } else if (agent === "history" || toolName.includes("history_read") || action === "call_history_read") {
        acc.history += 1;
      }

      const queryRecords = Array.isArray(row.query_records) ? row.query_records : [];
      queryRecords.forEach((item) => {
        const preview = String(item?.query || "").trim();
        if (preview && !acc.previews.includes(preview) && acc.previews.length < 3) {
          acc.previews.push(preview);
        }
      });

      if (acc.previews.length < 3) {
        const fallbackPreview = String(row.subquery || row.question || "").trim();
        if (fallbackPreview && !acc.previews.includes(fallbackPreview)) {
          acc.previews.push(fallbackPreview);
        }
      }

      return acc;
    },
    { total: 0, kg: 0, rag: 0, history: 0, previews: [] }
  );

  const agentObsSessions = Object.values(
    agentObsRuns.reduce((acc, run) => {
      const sessionId = String(run?.session_id || "").trim();
      if (!sessionId) {
        return acc;
      }
      const timestamp = String(run?.timestamp || "");
      if (!acc[sessionId]) {
        acc[sessionId] = {
          session_id: sessionId,
          latest_timestamp: timestamp,
          turn_count: 0,
          total_llm_calls: 0,
          total_attempted_tool_actions: 0,
          total_blocked_tool_actions: 0,
          total_tool_calls: 0,
          total_tokens: 0,
        };
      }
      const row = acc[sessionId];
      row.turn_count += 1;
      row.total_llm_calls += Number(run?.llm_calls || 0);
      row.total_attempted_tool_actions += Number(run?.attempted_tool_actions ?? run?.tool_calls ?? 0);
      row.total_blocked_tool_actions += Number(run?.blocked_tool_actions || 0);
      row.total_tool_calls += Number(run?.tool_calls || 0);
      row.total_tokens += Number(run?.total_tokens || 0);
      if (!row.latest_timestamp || timestamp > row.latest_timestamp) {
        row.latest_timestamp = timestamp;
      }
      return acc;
    }, {})
  ).sort((a, b) => String(b.latest_timestamp || "").localeCompare(String(a.latest_timestamp || "")));

  // 渲染事件详情中的单个字段块。
  const renderDetailField = (label, value, key) => (
    <div className="event-detail-field" key={key}>
      <div className="event-detail-label">{label}</div>
      {renderExpandableText(value, key, 280)}
    </div>
  );

  // 渲染会话元信息项。
  const renderSessionMetaItem = (label, value) => (
    <div className="session-meta-item" key={label}>
      <div className="session-meta-label">{label}</div>
      <div className="session-meta-value">{value || "-"}</div>
    </div>
  );

  const readinessLevel = readinessData?.level ?? "unavailable";
  const readinessLevelText = {
    ready: "READY",
    degraded: "DEGRADED",
    unavailable: "UNAVAILABLE"
  }[readinessLevel] ?? "UNAVAILABLE";
  const readinessReasons = [
    ...(readinessData?.critical_not_ready ?? []).map((item) => `核心依赖未就绪: ${item}`),
    ...(readinessData?.optional_issues ?? []).map((item) => `可选能力异常: ${item}`)
  ];

  return (
    <div className="admin-dashboard">
      <div className="admin-dashboard-header">
        <h2>管理后台</h2>
        <div className="admin-dashboard-actions">
          <button onClick={loadData} disabled={loading}>
            {loading ? "刷新中..." : "刷新数据"}
          </button>
          <button onClick={onLogout} className="logout-btn">退出登录</button>
        </div>
      </div>

      {error && <div className="admin-error">{error}</div>}

      <div className={`readiness-bar readiness-${readinessLevel}`}>
        <div className="readiness-main">
          <span className="readiness-label">服务就绪状态</span>
          <span className={`readiness-badge readiness-badge-${readinessLevel}`}>
            {readinessLoading ? "CHECKING" : readinessLevelText}
          </span>
          <span className="readiness-time">
            最近检查：{readinessData?.checked_at ?? "-"}
          </span>
        </div>
        <div className="readiness-actions">
          <button
            className="btn btn-secondary"
            onClick={loadReadiness}
            disabled={readinessLoading}
          >
            {readinessLoading ? "刷新中..." : "刷新状态"}
          </button>
        </div>
        {readinessError && (
          <div className="readiness-reasons">
            <span>状态获取失败: {readinessError}</span>
          </div>
        )}
        {!readinessError && readinessReasons.length > 0 && (
          <div className="readiness-reasons">
            {readinessReasons.map((reason) => (
              <span key={reason}>{reason}</span>
            ))}
          </div>
        )}
      </div>

      {/* 标签切换 */}
      <div className="admin-tabs">
        <button
          className={activeTab === "sessions" ? "active" : ""}
          onClick={() => setActiveTab("sessions")}
        >
          会话管理
        </button>
        <button
          className={activeTab === "agent-observability" ? "active" : ""}
          onClick={() => setActiveTab("agent-observability")}
        >
          Agent观测
        </button>
        <button
          className={activeTab === "graph" ? "active" : ""}
          onClick={() => setActiveTab("graph")}
        >
          图谱管理
        </button>
        <button
          className={activeTab === "docling" ? "active" : ""}
          onClick={() => setActiveTab("docling")}
        >
          文本嵌入
        </button>
      </div>

      {/* Agent 可观测性标签 */}
      {activeTab === "agent-observability" && (
        <div className="token-stats-section">
          <div className="admin-observability-header">
            <h3>Agent 可观测性概览</h3>
            <button
              className="btn btn-secondary"
              onClick={loadAgentObservability}
              disabled={agentObsLoading}
            >
              {agentObsLoading ? "刷新中..." : "刷新观测数据"}
            </button>
          </div>

          {agentObsSummary && (
            <div className="stats-cards">
              <div className="stat-card token-card">
                <div className="stat-value">{agentObsSummary.total_runs}</div>
                <div className="stat-label">运行次数</div>
              </div>
              <div className="stat-card token-card">
                <div className="stat-value">{agentObsSummary.avg_duration_ms}</div>
                <div className="stat-label">平均耗时(ms)</div>
              </div>
              <div className="stat-card token-card">
                <div className="stat-value">{((agentObsSummary.avg_execution_tool_success_rate ?? agentObsSummary.avg_tool_success_rate) * 100).toFixed(1)}%</div>
                <div className="stat-label">执行成功率</div>
              </div>
              <div className="stat-card token-card">
                <div className="stat-value">{((agentObsSummary.avg_attempted_action_success_rate ?? agentObsSummary.avg_tool_success_rate) * 100).toFixed(1)}%</div>
                <div className="stat-label">尝试成功率</div>
              </div>
              <div className="stat-card token-card">
                <div className="stat-value">{agentObsSummary.total_tokens?.toLocaleString()}</div>
                <div className="stat-label">累计Tokens</div>
              </div>
              <div className="stat-card token-card">
                <div className="stat-value">{agentObsSummary.total_llm_calls}</div>
                <div className="stat-label">累计LLM调用</div>
              </div>
              <div className="stat-card token-card">
                <div className="stat-value">{agentObsSummary.total_tool_calls}</div>
                <div className="stat-label">累计已执行工具调用</div>
              </div>
              <div className="stat-card token-card">
                <div className="stat-value">{agentObsSummary.total_attempted_tool_actions ?? agentObsSummary.total_tool_calls}</div>
                <div className="stat-label">累计工具尝试</div>
              </div>
              <div className="stat-card token-card">
                <div className="stat-value">{agentObsSummary.total_blocked_tool_actions ?? 0}</div>
                <div className="stat-label">累计策略拦截</div>
              </div>
            </div>
          )}

          {agentObsSummary && (
            <div className="token-stats-table-section">
              <h3>错误分组统计</h3>
              <div className="session-info">
                <p><strong>策略拦截/规划错误:</strong> {formatErrorTypes(agentObsSummary.strategy_error_types)}</p>
                <p><strong>执行错误:</strong> {formatErrorTypes(agentObsSummary.execution_error_types)}</p>
              </div>
            </div>
          )}

          <div className="token-stats-table-section">
            <h3>最近活跃会话</h3>
            <div className="sessions-table-wrapper">
              <table className="sessions-table token-table">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>时间</th>
                    <th>会话ID</th>
                    <th>轮次数</th>
                    <th>累计LLM调用</th>
                    <th>累计工具尝试</th>
                    <th>策略拦截</th>
                    <th>累计已执行工具</th>
                    <th>累计Tokens</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {agentObsSessions.map((sessionEntry, index) => (
                    <tr key={sessionEntry.session_id}>
                      <td>{index + 1}</td>
                      <td>{formatTime(sessionEntry.latest_timestamp)}</td>
                      <td className="user-id" title={sessionEntry.session_id || ""}>
                        {sessionEntry.session_id ? `${sessionEntry.session_id.slice(0, 12)}...` : "-"}
                      </td>
                      <td>{sessionEntry.turn_count}</td>
                      <td>{sessionEntry.total_llm_calls}</td>
                      <td>{sessionEntry.total_attempted_tool_actions}</td>
                      <td>{sessionEntry.total_blocked_tool_actions}</td>
                      <td>{sessionEntry.total_tool_calls}</td>
                      <td>{sessionEntry.total_tokens.toLocaleString()}</td>
                      <td>
                        <button
                          className="view-detail-btn"
                          onClick={() => handleViewSession({ id: sessionEntry.session_id })}
                        >
                          查看会话流转
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {agentObsSessions.length === 0 && !agentObsLoading && (
                <div className="empty-data">暂无 Agent 观测数据，请先运行几次问答。</div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* 会话管理标签 */}
      {activeTab === "sessions" && (
        <>
          {/* 统计卡片 */}
          {stats && (
            <div className="stats-cards">
              <div className="stat-card">
                <div className="stat-value">{stats.total_sessions}</div>
                <div className="stat-label">总会话数</div>
              </div>
              <div className="stat-card">
                <div className="stat-value">{stats.total_messages}</div>
                <div className="stat-label">总消息数</div>
              </div>
              <div className="stat-card">
                <div className="stat-value">{stats.today_sessions}</div>
                <div className="stat-label">今日会话</div>
              </div>
              <div className="stat-card">
                <div className="stat-value">{stats.today_messages}</div>
                <div className="stat-label">今日消息</div>
              </div>
            </div>
          )}

          {/* 会话列表 */}
          <div className="sessions-section">
            <h3>会话列表 ({total} 条记录)</h3>
            <div className="sessions-table-wrapper">
              <table className="sessions-table">
                <thead>
                  <tr>
                    <th>会话ID</th>
                    <th>用户ID</th>
                    <th>标题</th>
                    <th>消息数</th>
                    <th>创建时间</th>
                    <th>最后更新</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {sessions.map((session) => (
                    <tr key={session.id}>
                      <td className="session-id" title={session.id}>
                        {session.id.slice(0, 8)}...
                      </td>
                      <td className="user-id" title={session.user_id}>
                        {session.user_id.slice(0, 12)}...
                      </td>
                      <td>{truncateText(session.title, 30)}</td>
                      <td>{session.message_count}</td>
                      <td>{formatTime(session.created_at)}</td>
                      <td>{formatTime(session.updated_at)}</td>
                      <td className="actions">
                        <button
                          className="view-btn"
                          onClick={() => handleViewSession(session)}
                        >
                          查看
                        </button>
                        <button
                          className="clear-history-btn"
                          onClick={() => handleClearSessionHistory(session.id)}
                        >
                          清空历史
                        </button>
                        <button
                          className="delete-btn"
                          onClick={() => handleDeleteSession(session.id)}
                        >
                          删除会话
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {sessions.length === 0 && !loading && (
                <div className="empty-state">暂无会话记录</div>
              )}
            </div>
          </div>
        </>
      )}

      {/* 图谱管理标签 */}
      {activeTab === "graph" && (
        <div className="graph-manager-section">
          <GraphManager />
        </div>
      )}

      {/* 文本嵌入标签 */}
      {activeTab === "docling" && (
        <div className="docling-section">
          <DoclingParser />
        </div>
      )}

      {/* 会话详情弹窗 */}
      {showDetail && selectedSession && (
        <div className="modal-overlay" onClick={() => setShowDetail(false)}>
          <div className="modal session-detail-modal session-observability-modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header session-detail-meta-header">
              <div className="session-meta-grid">
                {[
                  ["会话ID", selectedSession.id],
                  ["用户ID", selectedSession.user_id],
                  ["标题", selectedSession.title],
                  ["创建时间", formatTime(selectedSession.created_at)],
                  ["更新时间", formatTime(selectedSession.updated_at)],
                ].map(([label, value]) => renderSessionMetaItem(label, value))}
              </div>
              <button className="close-btn" onClick={() => setShowDetail(false)}>×</button>
            </div>
            <div className="session-observability-layout">
              <section className="turn-detail-section">
                <h4>按轮查看</h4>
                <div className="session-turn-list session-turn-list-top">
                  {sessionObservability.length === 0 && (
                    <div className="empty-messages">该会话暂无按轮观测数据</div>
                  )}
                  {sessionObservability.map((turn) => (
                    <button
                      key={turn.turn_id}
                      className={`session-turn-item ${selectedTurn?.turn_id === turn.turn_id ? "active" : ""}`}
                      onClick={() => {
                        setSelectedTurnId(turn.turn_id);
                        setSelectedFlowEvent(null);
                      }}
                    >
                      <div className="session-turn-question">{truncateText(turn.question, 120)}</div>
                      <div className="session-turn-meta">
                        <span>{formatEventTime(turn.started_at)}</span>
                        <span>{turn.status || "done"}</span>
                      </div>
                      <div className="session-turn-action">查看本轮流转</div>
                    </button>
                  ))}
                </div>
              </section>
              <div className="session-turn-detail">
                {!selectedTurn && (
                  <div className="empty-messages">该会话暂无消息</div>
                )}
                {selectedTurn && (
                  <>
                    <section className="turn-detail-section">
                      <h4>本轮问题</h4>
                      <div className="turn-question-card flow-summary-card">
                        <div className="turn-question-header">
                          <div className="turn-question-time">开始时间：{formatEventTime(selectedTurn.started_at)}</div>
                          <div className="turn-question-token">本轮 Tokens：{Number(selectedTurn.total_tokens || 0).toLocaleString()}</div>
                        </div>
                        {renderExpandableText(selectedTurn.question, `turn-question-${selectedTurn.turn_id}`, 220)}
                        <div className="turn-answer-digest">
                          <strong>回答摘要：</strong>
                          {renderExpandableText(selectedTurn.answer_digest, `turn-answer-${selectedTurn.turn_id}`, 220)}
                        </div>
                      </div>
                    </section>

                    <section className="turn-detail-section">
                      <h4>观测情况摘要</h4>
                      <div className="human-readable-card-grid flow-summary-grid">
                        <div className="human-readable-card">
                          <div className="human-readable-card-title">会话历史记录</div>
                          <div className="human-readable-hint">问答历史</div>
                          {renderExpandableText(
                            selectedTurn.memory_before?.qa_memory_text || "当前暂无问答对",
                            `qa-memory-${selectedTurn.turn_id}`,
                            180
                          )}
                          <div className="human-readable-hint" style={{ marginTop: 12 }}>历史检索问题</div>
                          {renderExpandableText(
                            selectedTurnFilteredHistoryRetrievalText || "当前暂无历史检索问题",
                            `retrieval-index-${selectedTurn.turn_id}`,
                            180
                          )}
                        </div>
                        <div className="human-readable-card">
                          <div className="human-readable-card-title">本轮对话中工具调用情况</div>
                          {selectedTurnToolSummary.total > 0 || selectedTurnProjectedRecordCount > 0 ? (
                            <>
                              <div className="human-readable-line">调用总数：{selectedTurnToolSummary.total}</div>
                              {selectedTurnToolSummary.total > 0 && (
                                <div className="human-readable-hint">
                                  kg：{selectedTurnToolSummary.kg} / rag：{selectedTurnToolSummary.rag} / history_read：{selectedTurnToolSummary.history}
                                </div>
                              )}
                              {selectedTurnProjectedRecordCount > 0 && (
                                <div className="human-readable-hint">
                                  本轮写入检索记录：{selectedTurnProjectedRecordCount}
                                </div>
                              )}
                              <div className="human-readable-hint" style={{ marginTop: 12 }}>Query / 子问题预览</div>
                              {[...selectedTurnToolSummary.previews, ...selectedTurnProjectionPreviews]
                                .filter((preview, index, rows) => preview && rows.indexOf(preview) === index)
                                .slice(0, 3).length > 0 ? (
                                [...selectedTurnToolSummary.previews, ...selectedTurnProjectionPreviews]
                                  .filter((preview, index, rows) => preview && rows.indexOf(preview) === index)
                                  .slice(0, 3)
                                  .map((preview, index) => (
                                  <div key={`tool-preview-${selectedTurn.turn_id}-${index}`} className="human-readable-line">
                                    {index + 1}. {preview}
                                  </div>
                                  ))
                              ) : (
                                <div className="human-readable-line">本轮无可展示的 query 预览</div>
                              )}
                            </>
                          ) : (
                            <div className="human-readable-line">本轮未调用工具</div>
                          )}
                        </div>
                        <div className="human-readable-card">
                          <div className="human-readable-card-title">耗时</div>
                          <div className="human-readable-line">{formatDurationMs(selectedTurn.duration_ms)}</div>
                          <div className="human-readable-hint">开始：{formatEventTime(selectedTurn.started_at)}</div>
                          <div className="human-readable-hint">结束：{formatEventTime(selectedTurn.finished_at)}</div>
                        </div>
                        <div className="human-readable-card">
                          <div className="human-readable-card-title">提示词预算/压缩情况</div>
                          <div className="human-readable-line">
                            预算：{selectedTurn.orchestrator_decision?.prompt_budget_limit ?? "-"}
                          </div>
                          <div className="human-readable-hint">
                            压缩次数：{selectedTurn.orchestrator_decision?.compression_count ?? "-"}
                          </div>
                          <div className="human-readable-hint">
                            压缩前分词数：{selectedTurn.orchestrator_decision?.prompt_token_count_before_last_decision ?? "-"}
                          </div>
                          <div className="human-readable-hint">
                            压缩后分词数：{selectedTurn.orchestrator_decision?.prompt_token_count_after_last_compression ?? "-"}
                          </div>
                          <div className="human-readable-hint">
                            超限中止：{selectedTurn.orchestrator_decision?.aborted_for_prompt_budget ? "是" : "否"}
                          </div>
                        </div>
                      </div>
                    </section>

                    <section className="turn-detail-section">
                      <h4>会话流转泳道图</h4>
                      <div className="flow-board">
                        <div className="flow-board-header">
                          <div className="flow-header-cell flow-time-header">时间</div>
                          {flowLaneNames.map((laneName) => (
                            <div key={laneName} className="flow-header-cell flow-lane-header">
                              {laneName}
                            </div>
                          ))}
                        </div>
                        <div className="flow-board-body">
                          {(selectedTurn.flow_events || []).length === 0 && (
                            <div className="empty-messages">该轮没有可用的流转事件</div>
                          )}
                          {(selectedTurn.flow_events || []).map((event, index) => (
                            <div key={event.event_id || `${selectedTurn.turn_id}-event-${index}`} className="flow-event-row">
                              <div className="flow-time-cell">{formatEventTime(event.event_time)}</div>
                              {flowLaneNames.map((laneName) => (
                                <div key={`${event.event_id || index}-${laneName}`} className={`flow-lane-cell ${laneName === event.lane ? "active" : "empty"}`}>
                                  {laneName === event.lane ? (
                                    <button
                                      className="flow-event-node"
                                      onClick={() => setSelectedFlowEvent(event)}
                                    >
                                      <div className="flow-event-stage">{event.stage || laneName}</div>
                                      <div className="flow-event-title">{event.title || "过程事件"}</div>
                                      <div className="flow-event-summary">{event.summary || "该事件暂无摘要"}</div>
                                      <div className="flow-event-actor">
                                        {(event.actor || laneName)} · 工具调用 {Number(event.detail?.tool_call_count || 0)}
                                      </div>
                                      <div className="flow-event-link">查看详情</div>
                                    </button>
                                  ) : (
                                    <div className="flow-event-placeholder"> </div>
                                  )}
                                </div>
                              ))}
                            </div>
                          ))}
                        </div>
                      </div>
                    </section>
                  </>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
      {selectedFlowEvent && (
        <div className="modal-overlay" onClick={() => setSelectedFlowEvent(null)}>
          <div className="modal flow-event-detail-modal event-detail-modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3>{selectedFlowEvent.title || "事件详情"}</h3>
              <button className="close-btn" onClick={() => setSelectedFlowEvent(null)}>×</button>
            </div>
            <div className="event-detail-meta">
              <span><strong>发生时间：</strong>{formatEventTime(selectedFlowEvent.event_time)}</span>
              <span><strong>所属泳道：</strong>{selectedFlowEvent.lane || "-"}</span>
              <span><strong>阶段：</strong>{selectedFlowEvent.stage || "-"}</span>
              <span><strong>执行方：</strong>{selectedFlowEvent.actor || "-"}</span>
            </div>
            <div className="event-detail-summary">
              <div className="event-detail-section-title">摘要</div>
              {renderExpandableText(selectedFlowEvent.summary, `flow-summary-${selectedFlowEvent.event_id}`, 240)}
            </div>
            <div className="event-detail-grid">
              {renderDetailField("模型输入提示词", selectedFlowEvent.detail?.model_input_prompt, `detail-model-input-${selectedFlowEvent.event_id}`)}
              {renderDetailField("模型输出内容", selectedFlowEvent.detail?.model_output_text, `detail-model-output-${selectedFlowEvent.event_id}`)}
              {renderDetailField("工具调用输入", selectedFlowEvent.detail?.tool_call_inputs, `detail-tool-input-${selectedFlowEvent.event_id}`)}
              {renderDetailField("工具调用结果", selectedFlowEvent.detail?.tool_call_results, `detail-tool-result-${selectedFlowEvent.event_id}`)}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
