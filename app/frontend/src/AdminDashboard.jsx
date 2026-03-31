import { useState, useEffect } from "react";
import {
  getAdminSessions,
  getAdminSessionMessages,
  deleteAdminSession,
  clearAdminSessionMessages,
  getAdminStats,
  getAdminAgentObservabilitySummary,
  getAdminAgentObservabilityRuns,
  getReadinessStatus
} from "./api";
import GraphManager from "./GraphManager";
import DoclingParser from "./DoclingParser";

export default function AdminDashboard({ onLogout }) {
  const [sessions, setSessions] = useState([]);
  const [total, setTotal] = useState(0);
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [selectedSession, setSelectedSession] = useState(null);
  const [sessionMessages, setSessionMessages] = useState([]);
  const [showDetail, setShowDetail] = useState(false);

  // 标签页状态
  const [activeTab, setActiveTab] = useState("sessions");

  // Agent 可观测性状态
  const [agentObsSummary, setAgentObsSummary] = useState(null);
  const [agentObsRuns, setAgentObsRuns] = useState([]);
  const [agentObsLoading, setAgentObsLoading] = useState(false);
  const [selectedObsRun, setSelectedObsRun] = useState(null);

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

  const handleViewSession = async (session) => {
    try {
      const data = await getAdminSessionMessages(session.id);
      setSelectedSession(data.session);
      setSessionMessages(data.messages);
      setShowDetail(true);
    } catch (err) {
      setError(err.message);
    }
  };

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

  const formatTime = (timestamp) => {
    if (!timestamp) return "-";
    return new Date(timestamp).toLocaleString("zh-CN");
  };

  const truncateText = (text, maxLength = 50) => {
    if (!text) return "";
    return text.length > maxLength ? text.slice(0, maxLength) + "..." : text;
  };

  const formatJson = (value) => {
    if (value === undefined) return "undefined";
    if (value === null) return "null";
    try {
      return JSON.stringify(value, null, 2);
    } catch {
      return String(value);
    }
  };

  const openObsRunDetail = (run) => setSelectedObsRun(run);
  const closeObsRunDetail = () => setSelectedObsRun(null);

  const formatErrorTypes = (errors) => {
    if (!errors || Object.keys(errors).length === 0) {
      return "-";
    }
    return Object.entries(errors).map(([k, v]) => `${k}:${v}`).join(" | ");
  };

  const getToolObservations = (run) => {
    const observations = run?.tool_observations || [];
    if (!Array.isArray(observations)) {
      return [];
    }
    return observations.filter((item) => {
      if (!item || typeof item !== "object") {
        return false;
      }
      const action = String(item.action || "").trim();
      const hasToolName = Boolean(item.tool_name || item.requested_tool_name);
      if (!hasToolName) {
        return false;
      }
      return ["call_tool", "preload_graph_nodes", "hyde_call_tool", "subquery_parallel_call", "subquery_dependent_call"].includes(action);
    });
  };

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
          Docling解析
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
            <h3>最近运行记录</h3>
            <div className="sessions-table-wrapper">
              <table className="sessions-table token-table">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>时间</th>
                    <th>会话ID</th>
                    <th>耗时(ms)</th>
                    <th>LLM调用</th>
                    <th>工具尝试</th>
                    <th>策略拦截</th>
                    <th>已执行工具调用</th>
                    <th>执行成功率</th>
                    <th>尝试成功率</th>
                    <th>总Tokens</th>
                    <th>策略错误</th>
                    <th>执行错误</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {agentObsRuns.map((run) => (
                    <tr key={run.rank}>
                      <td>{run.rank}</td>
                      <td>{formatTime(run.timestamp)}</td>
                      <td className="user-id" title={run.session_id || ""}>{run.session_id ? `${run.session_id.slice(0, 12)}...` : "-"}</td>
                      <td>{run.duration_ms.toFixed(2)}</td>
                      <td>{run.llm_calls}</td>
                      <td>{run.attempted_tool_actions ?? run.tool_calls}</td>
                      <td>{run.blocked_tool_actions ?? 0}</td>
                      <td>{run.tool_calls}</td>
                      <td>{((run.execution_tool_success_rate ?? run.tool_success_rate) * 100).toFixed(1)}%</td>
                      <td>{((run.attempted_action_success_rate ?? run.tool_success_rate) * 100).toFixed(1)}%</td>
                      <td>{run.total_tokens?.toLocaleString()}</td>
                      <td className="error-types-cell">
                        {formatErrorTypes(run.strategy_error_types)}
                      </td>
                      <td className="error-types-cell">
                        {formatErrorTypes(run.execution_error_types)}
                      </td>
                      <td>
                        <button
                          className="view-detail-btn"
                          onClick={() => openObsRunDetail(run)}
                        >
                          查看详情
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {agentObsRuns.length === 0 && !agentObsLoading && (
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

      {/* Docling 解析标签 */}
      {activeTab === "docling" && (
        <div className="docling-section">
          <DoclingParser />
        </div>
      )}

      {selectedObsRun && (
        <div className="modal-overlay" onClick={closeObsRunDetail}>
          <div className="modal session-detail-modal obs-detail-modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3>运行详情 #{selectedObsRun.rank}</h3>
              <button className="close-btn" onClick={closeObsRunDetail}>×</button>
            </div>

            <div className="session-info">
              <p><strong>时间:</strong> {formatTime(selectedObsRun.timestamp)}</p>
              <p><strong>会话ID:</strong> {selectedObsRun.session_id || "-"}</p>
              <p><strong>耗时:</strong> {selectedObsRun.duration_ms.toFixed(2)} ms</p>
              <p><strong>LLM调用:</strong> {selectedObsRun.llm_calls}</p>
              <p><strong>工具尝试:</strong> {selectedObsRun.attempted_tool_actions ?? selectedObsRun.tool_calls}</p>
              <p><strong>策略拦截:</strong> {selectedObsRun.blocked_tool_actions ?? 0}</p>
              <p><strong>已执行工具调用:</strong> {selectedObsRun.tool_calls}</p>
              <p><strong>执行成功率:</strong> {((selectedObsRun.execution_tool_success_rate ?? selectedObsRun.tool_success_rate) * 100).toFixed(1)}%</p>
              <p><strong>尝试成功率:</strong> {((selectedObsRun.attempted_action_success_rate ?? selectedObsRun.tool_success_rate) * 100).toFixed(1)}%</p>
              <p><strong>总Tokens:</strong> {selectedObsRun.total_tokens?.toLocaleString()}</p>
              <p><strong>策略错误:</strong> {formatErrorTypes(selectedObsRun.strategy_error_types)}</p>
              <p><strong>执行错误:</strong> {formatErrorTypes(selectedObsRun.execution_error_types)}</p>
            </div>

            <div className="messages-list obs-run-detail-list">
              <h4>完整工具调用过程</h4>
              {getToolObservations(selectedObsRun).length === 0 && (
                <div className="empty-messages">该次运行暂无工具调用详情（通常是旧日志数据）。</div>
              )}

              {getToolObservations(selectedObsRun).map((item, index) => (
                <details key={index} className="tool-trace-item-detail" open={index === 0}>
                  <summary>
                    第{item.turn || index + 1}轮 · 工具 {item.tool_name || item.requested_tool_name || "-"}
                    {item.used_fallback ? "（已自动切换）" : ""}
                  </summary>
                  <div className="tool-detail-section">
                    <h4>工具名称</h4>
                    <pre className="tool-code">{formatJson({ requested_tool_name: item.requested_tool_name, actual_tool_name: item.tool_name })}</pre>
                  </div>
                  <div className="tool-detail-section">
                    <h4>参数</h4>
                    <pre className="tool-code">{formatJson(item.arguments)}</pre>
                  </div>
                  <div className="tool-detail-section">
                    <h4>返回</h4>
                    <pre className="tool-code">{formatJson(item.result)}</pre>
                  </div>
                </details>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* 会话详情弹窗 */}
      {showDetail && selectedSession && (
        <div className="modal-overlay" onClick={() => setShowDetail(false)}>
          <div className="modal session-detail-modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3>会话详情</h3>
              <button className="close-btn" onClick={() => setShowDetail(false)}>×</button>
            </div>
            <div className="session-info">
              <p><strong>会话ID:</strong> {selectedSession.id}</p>
              <p><strong>用户ID:</strong> {selectedSession.user_id}</p>
              <p><strong>标题:</strong> {selectedSession.title}</p>
              <p><strong>创建时间:</strong> {formatTime(selectedSession.created_at)}</p>
              <p><strong>更新时间:</strong> {formatTime(selectedSession.updated_at)}</p>
            </div>
            <div className="messages-list">
              <h4>消息记录 ({sessionMessages.length} 条)</h4>
              {sessionMessages.map((msg) => (
                <div key={msg.id} className={`message-item ${msg.role}`}>
                  <div className="message-header">
                    <span className="message-role">
                      {msg.role === "user" ? "用户" : "助手"}
                    </span>
                    <span className="message-time">{formatTime(msg.created_at)}</span>
                  </div>
                  <div className="message-content">{msg.content}</div>
                  {msg.think_content && (
                    <details className="think-content">
                      <summary>思考过程</summary>
                      <pre>{msg.think_content}</pre>
                    </details>
                  )}
                </div>
              ))}
              {sessionMessages.length === 0 && (
                <div className="empty-messages">该会话暂无消息</div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
