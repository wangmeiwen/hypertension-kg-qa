import { useState, useEffect, useCallback } from "react";
import { listSessions, deleteSession, createSession } from "./api";

export default function SessionSidebar({
  userId,
  currentSessionId,
  onSelectSession,
  onCreateSession,
  onSessionChange
}) {
  const [sessions, setSessions] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [editingId, setEditingId] = useState(null);
  const [editTitle, setEditTitle] = useState("");
  const [isCollapsed, setIsCollapsed] = useState(false);

  const loadSessions = useCallback(async () => {
    if (!userId) return;
    setLoading(true);
    setError("");
    try {
      const data = await listSessions(userId);
      setSessions(data);
    } catch (e) {
      setError("加载会话失败");
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, [userId]);

  useEffect(() => {
    loadSessions();
  }, [loadSessions]);

  const handleCreateSession = async () => {
    try {
      const session = await createSession(userId, "新会话");
      setSessions((prev) => [session, ...prev]);
      onCreateSession?.(session);
    } catch (e) {
      setError("创建会话失败");
      console.error(e);
    }
  };

  const handleDeleteSession = async (e, sessionId) => {
    e.stopPropagation();
    if (!confirm("确定要删除这个会话吗？")) return;
    try {
      await deleteSession(sessionId, userId);
      setSessions((prev) => prev.filter((s) => s.id !== sessionId));
      if (currentSessionId === sessionId) {
        onSelectSession?.(null);
      }
      onSessionChange?.();
    } catch (e) {
      setError("删除会话失败");
      console.error(e);
    }
  };

  const formatTime = (timestamp) => {
    const date = new Date(timestamp);
    const now = new Date();
    const diff = now - date;
    
    // 小于1小时显示"x分钟前"
    if (diff < 60 * 60 * 1000) {
      const minutes = Math.floor(diff / (60 * 1000));
      return minutes < 1 ? "刚刚" : `${minutes}分钟前`;
    }
    // 小于24小时显示"x小时前"
    if (diff < 24 * 60 * 60 * 1000) {
      const hours = Math.floor(diff / (60 * 60 * 1000));
      return `${hours}小时前`;
    }
    // 否则显示日期
    return date.toLocaleDateString("zh-CN", {
      month: "short",
      day: "numeric"
    });
  };

  const truncateTitle = (title, maxLength = 20) => {
    if (!title) return "新会话";
    return title.length > maxLength ? title.slice(0, maxLength) + "..." : title;
  };

  if (isCollapsed) {
    return (
      <div className="session-sidebar collapsed">
        <button
          className="expand-btn"
          onClick={() => setIsCollapsed(false)}
          title="展开会话列表"
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M9 18l6-6-6-6" />
          </svg>
        </button>
      </div>
    );
  }

  return (
    <div className="session-sidebar">
      <div className="sidebar-header">
        <h3>会话历史</h3>
        <div className="header-actions">
          <button
            className="new-session-btn"
            onClick={handleCreateSession}
            title="新建会话"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="12" y1="5" x2="12" y2="19" />
              <line x1="5" y1="12" x2="19" y2="12" />
            </svg>
            新建
          </button>
          <button
            className="collapse-btn"
            onClick={() => setIsCollapsed(true)}
            title="收起"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M15 18l-6-6 6-6" />
            </svg>
          </button>
        </div>
      </div>

      {error && <div className="sidebar-error">{error}</div>}

      <div className="sessions-list">
        {loading && sessions.length === 0 ? (
          <div className="loading-state">加载中...</div>
        ) : sessions.length === 0 ? (
          <div className="empty-state">
            <p>暂无会话</p>
            <button onClick={handleCreateSession}>开始新对话</button>
          </div>
        ) : (
          sessions.map((session) => (
            <div
              key={session.id}
              className={`session-item ${currentSessionId === session.id ? "active" : ""}`}
              onClick={() => onSelectSession?.(session)}
            >
              <div className="session-icon">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
                </svg>
              </div>
              <div className="session-info">
                <div className="session-title">
                  {truncateTitle(session.title)}
                </div>
                <div className="session-time">
                  {formatTime(session.updated_at)}
                </div>
              </div>
              <button
                className="delete-btn"
                onClick={(e) => handleDeleteSession(e, session.id)}
                title="删除会话"
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <line x1="18" y1="6" x2="6" y2="18" />
                  <line x1="6" y1="6" x2="18" y2="18" />
                </svg>
              </button>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
