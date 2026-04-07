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

  const truncateText = (text, maxLength = 50) => {
    if (!text) return "";
    return text.length > maxLength ? text.slice(0, maxLength) + "..." : text;
  };

  const selectedTurn = sessionObservability.find((turn) => turn.turn_id === selectedTurnId) || sessionObservability[0] || null;
  const flowLaneNames = ["主控编排", "图谱代理", "检索代理", "评估代理", "外部资源"];

  const toggleExpandedText = (key) => {
    setExpandedTextMap((prev) => ({ ...prev, [key]: !prev[key] }));
  };

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

  const formatErrorTypes = (errors) => {
    if (!errors || Object.keys(errors).length === 0) {
      return "-";
    }
    return Object.entries(errors).map(([k, v]) => `${k}:${v}`).join(" | ");
  };

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

  const renderDetailField = (label, value, key) => (
    <div className="event-detail-field" key={key}>
      <div className="event-detail-label">{label}</div>
      {renderExpandableText(value, key, 280)}
    </div>
  );

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

      {/* Docling 解析标签 */}
      {activeTab === "docling" && (
        <div className="docling-section">
          <DoclingParser />
        </div>
      )}

      {/* 会话详情弹窗 */}
      {showDetail && selectedSession && (
        <div className="modal-overlay" onClick={() => setShowDetail(false)}>
          <div className="modal session-detail-modal session-observability-modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3>会话详情</h3>
              <button className="close-btn" onClick={() => setShowDetail(false)}>×</button>
            </div>
            <div className="session-info session-meta-grid">
              {[
                ["会话ID", selectedSession.id],
                ["用户ID", selectedSession.user_id],
                ["标题", selectedSession.title],
                ["创建时间", formatTime(selectedSession.created_at)],
                ["更新时间", formatTime(selectedSession.updated_at)],
              ].map(([label, value]) => renderSessionMetaItem(label, value))}
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
                      <h4>会话记忆与主控判定</h4>
                      <div className="human-readable-card-grid flow-summary-grid">
                        <div className="human-readable-card">
                          <div className="human-readable-card-title">会话目标</div>
                          {renderExpandableText(selectedTurn.memory_before?.conversation_goal, `goal-${selectedTurn.turn_id}`, 180)}
                        </div>
                        <div className="human-readable-card">
                          <div className="human-readable-card-title">已确认事实</div>
                          {renderListLines(selectedTurn.memory_before?.confirmed_facts, `facts-${selectedTurn.turn_id}`)}
                        </div>
                        <div className="human-readable-card">
                          <div className="human-readable-card-title">已回答要点</div>
                          {renderListLines(selectedTurn.memory_before?.answered_points, `answered-${selectedTurn.turn_id}`)}
                        </div>
                        <div className="human-readable-card">
                          <div className="human-readable-card-title">未闭合问题</div>
                          {renderListLines(selectedTurn.memory_before?.open_questions, `open-${selectedTurn.turn_id}`)}
                        </div>
                        <div className="human-readable-card">
                          <div className="human-readable-card-title">响应模式</div>
                          <div className="human-readable-line">
                            {selectedTurn.orchestrator_decision?.response_mode_label
                              || (selectedTurn.orchestrator_decision?.response_mode === "direct_answer" ? "能直接回答" : "")
                              || (selectedTurn.orchestrator_decision?.response_mode === "retrieval_required" ? "需要检索" : "")
                              || selectedTurn.orchestrator_decision?.response_mode
                              || "-"}
                          </div>
                          <div className="human-readable-hint">
                            原因：{selectedTurn.orchestrator_decision?.direct_reason || selectedTurn.orchestrator_decision?.retrieval_reason || selectedTurn.orchestrator_decision?.memory_route_reason || "-"}
                          </div>
                        </div>
                        <div className="human-readable-card">
                          <div className="human-readable-card-title">回答模式</div>
                          <div className="human-readable-line">{selectedTurn.orchestrator_decision?.answer_mode || "-"}</div>
                          <div className="human-readable-hint">
                            来源：{selectedTurn.orchestrator_decision?.answer_source || "-"}
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
                                      <div className="flow-event-actor">{event.actor || laneName}</div>
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
              {renderDetailField("输入内容", selectedFlowEvent.detail?.["输入内容"], `detail-input-${selectedFlowEvent.event_id}`)}
              {renderDetailField("输出内容", selectedFlowEvent.detail?.["输出内容"], `detail-output-${selectedFlowEvent.event_id}`)}
              {renderDetailField("输出承接方", selectedFlowEvent.detail?.["输出承接方"], `detail-handoff-${selectedFlowEvent.event_id}`)}
              {renderDetailField("最终返回", selectedFlowEvent.detail?.["最终返回"], `detail-return-${selectedFlowEvent.event_id}`)}
              {renderDetailField("状态", selectedFlowEvent.detail?.["状态"], `detail-status-${selectedFlowEvent.event_id}`)}
              {renderDetailField("补充说明", selectedFlowEvent.detail?.["补充说明"], `detail-note-${selectedFlowEvent.event_id}`)}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
