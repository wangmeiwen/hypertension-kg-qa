import { useMemo, useState, useEffect, useCallback } from "react";
import { streamProcessWithSession, getSession, createSession, adminLogin, adminLogout, checkAdminStatus, uploadChatFile, rollbackToQuestion, cancelQaJob } from "./api";
import GraphView from "./GraphView";
import SessionSidebar from "./SessionSidebar";
import AdminDashboard from "./AdminDashboard";

function createUserId() {
  const cacheKey = "hypertensionkg_user_id";
  const cached = localStorage.getItem(cacheKey);
  if (cached) return cached;
  const next = `user_${Math.random().toString(36).slice(2, 10)}`;
  localStorage.setItem(cacheKey, next);
  return next;
}

const INLINE_TOKEN_REGEX = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\((?:https?:\/\/|mailto:)[^)]+\))/g;

const isMarkdownTableSeparator = (line) => {
  const trimmed = String(line || "").trim();
  if (!trimmed || !trimmed.includes("|")) return false;
  const cleaned = trimmed.replace(/^\|/, "").replace(/\|$/, "").trim();
  if (!cleaned) return false;
  const columns = cleaned.split("|").map((part) => part.trim());
  if (!columns.length) return false;
  return columns.every((part) => /^:?-{3,}:?$/.test(part));
};

const parseMarkdownTableRow = (line) => {
  const cleaned = String(line || "").trim().replace(/^\|/, "").replace(/\|$/, "");
  return cleaned.split("|").map((cell) => cell.trim());
};

const renderInlineMarkdown = (text, keyPrefix) => {
  const safeText = String(text || "");
  if (!safeText) return "";

  return safeText.split(INLINE_TOKEN_REGEX).map((part, index) => {
    if (!part) return null;

    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      return <strong key={`${keyPrefix}-strong-${index}`}>{part.slice(2, -2)}</strong>;
    }

    if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
      return <code key={`${keyPrefix}-code-${index}`}>{part.slice(1, -1)}</code>;
    }

    const linkMatch = part.match(/^\[([^\]]+)\]\(((?:https?:\/\/|mailto:)[^)]+)\)$/);
    if (linkMatch) {
      const [, label, href] = linkMatch;
      return (
        <a key={`${keyPrefix}-link-${index}`} href={href} target="_blank" rel="noreferrer">
          {label}
        </a>
      );
    }

    return <span key={`${keyPrefix}-text-${index}`}>{part}</span>;
  });
};

const renderMarkdownContent = (content) => {
  const text = String(content || "").replace(/\r\n/g, "\n");
  if (!text.trim()) return "处理中...";

  const lines = text.split("\n");
  const blocks = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    if (!line.trim()) {
      i += 1;
      continue;
    }

    const fenceMatch = line.match(/^```\s*([^`]*)\s*$/);
    if (fenceMatch) {
      const lang = (fenceMatch[1] || "").trim();
      i += 1;
      const codeLines = [];
      while (i < lines.length && !/^```\s*$/.test(lines[i])) {
        codeLines.push(lines[i]);
        i += 1;
      }
      if (i < lines.length && /^```\s*$/.test(lines[i])) {
        i += 1;
      }
      blocks.push(
        <pre key={`code-${blocks.length}`} className="md-code-block">
          {lang ? <div className="md-code-lang">{lang}</div> : null}
          <code>{codeLines.join("\n")}</code>
        </pre>
      );
      continue;
    }

    const headingMatch = line.match(/^(#{1,6})\s+(.+)$/);
    if (headingMatch) {
      const level = headingMatch[1].length;
      const headingText = headingMatch[2].trim();
      const headingClass = `md-h${level}`;
      blocks.push(
        <div key={`h-${blocks.length}`} className={headingClass}>
          {renderInlineMarkdown(headingText, `h-${blocks.length}`)}
        </div>
      );
      i += 1;
      continue;
    }

    if (/^>\s?/.test(line)) {
      const quoteLines = [];
      while (i < lines.length && /^>\s?/.test(lines[i])) {
        quoteLines.push(lines[i].replace(/^>\s?/, ""));
        i += 1;
      }
      blocks.push(
        <blockquote key={`quote-${blocks.length}`} className="md-quote">
          {renderInlineMarkdown(quoteLines.join(" "), `quote-${blocks.length}`)}
        </blockquote>
      );
      continue;
    }

    if (/^[-*+]\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^[-*+]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^[-*+]\s+/, "").trim());
        i += 1;
      }
      blocks.push(
        <ul key={`ul-${blocks.length}`} className="md-list">
          {items.map((item, idx) => (
            <li key={`ul-item-${idx}`}>{renderInlineMarkdown(item, `ul-${blocks.length}-${idx}`)}</li>
          ))}
        </ul>
      );
      continue;
    }

    if (/^\d+\.\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\d+\.\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\d+\.\s+/, "").trim());
        i += 1;
      }
      blocks.push(
        <ol key={`ol-${blocks.length}`} className="md-list md-ordered-list">
          {items.map((item, idx) => (
            <li key={`ol-item-${idx}`}>{renderInlineMarkdown(item, `ol-${blocks.length}-${idx}`)}</li>
          ))}
        </ol>
      );
      continue;
    }

    if (line.includes("|") && i + 1 < lines.length && isMarkdownTableSeparator(lines[i + 1])) {
      const headerCells = parseMarkdownTableRow(line);
      i += 2;

      const bodyRows = [];
      while (i < lines.length && lines[i].trim() && lines[i].includes("|")) {
        bodyRows.push(parseMarkdownTableRow(lines[i]));
        i += 1;
      }

      blocks.push(
        <div key={`table-${blocks.length}`} className="md-table-wrap">
          <table className="md-table">
            <thead>
              <tr>
                {headerCells.map((cell, idx) => (
                  <th key={`th-${idx}`}>{renderInlineMarkdown(cell, `th-${blocks.length}-${idx}`)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {bodyRows.map((row, rowIdx) => (
                <tr key={`tr-${rowIdx}`}>
                  {headerCells.map((_, colIdx) => (
                    <td key={`td-${rowIdx}-${colIdx}`}>
                      {renderInlineMarkdown(row[colIdx] || "", `td-${blocks.length}-${rowIdx}-${colIdx}`)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
      continue;
    }

    const paragraphLines = [];
    while (
      i < lines.length
      && lines[i].trim()
      && !/^```\s*([^`]*)\s*$/.test(lines[i])
      && !/^(#{1,6})\s+/.test(lines[i])
      && !/^>\s?/.test(lines[i])
      && !/^[-*+]\s+/.test(lines[i])
      && !/^\d+\.\s+/.test(lines[i])
    ) {
      paragraphLines.push(lines[i].trim());
      i += 1;
    }

    const paragraphText = paragraphLines.join(" ");
    blocks.push(
      <p key={`p-${blocks.length}`} className="md-paragraph">
        {renderInlineMarkdown(paragraphText, `p-${blocks.length}`)}
      </p>
    );
  }

  return blocks;
};

const getLatestToolStatus = (thinkContent) => {
  const lines = String(thinkContent || "")
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.startsWith("当前调用工具:"));
  if (!lines.length) return "";
  return lines[lines.length - 1];
};

// ==================== 登录弹窗组件 ====================
function LoginModal({ onClose, onLogin }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!username.trim() || !password) {
      setError("请输入用户名和密码");
      return;
    }
    setLoading(true);
    setError("");
    try {
      await adminLogin(username.trim(), password);
      onLogin();
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal login-modal" onClick={(e) => e.stopPropagation()}>
        <h3>管理员登录</h3>
        <p className="login-hint">默认账号: admin / admin123</p>
        {error && <div className="login-error">{error}</div>}
        <form onSubmit={handleSubmit}>
          <div className="form-group">
            <label>用户名</label>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="admin"
              required
            />
          </div>
          <div className="form-group">
            <label>密码</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="admin123"
              required
            />
          </div>
          <div className="modal-actions">
            <button type="button" onClick={onClose}>取消</button>
            <button type="submit" className="primary" disabled={loading}>
              {loading ? "登录中..." : "登录"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// ==================== 主应用组件 ====================
export default function App() {
  const userId = useMemo(() => createUserId(), []);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [copiedMessageKey, setCopiedMessageKey] = useState("");
  const [loading, setLoading] = useState(false);
  const [activeTab, setActiveTab] = useState("chat");
  const [currentSessionId, setCurrentSessionId] = useState(null);
  const [isInitializing, setIsInitializing] = useState(true);
  const [showLogin, setShowLogin] = useState(false);
  const [isLoggedIn, setIsLoggedIn] = useState(false);
  const [showAdminPanel, setShowAdminPanel] = useState(false);
  const [selectedFile, setSelectedFile] = useState(null);
  const [filePreview, setFilePreview] = useState(null);
  const [currentJobId, setCurrentJobId] = useState("");
  const [stoppingJob, setStoppingJob] = useState(false);

  // 检查登录状态
  useEffect(() => {
    let active = true;
    checkAdminStatus()
      .then((loggedIn) => {
        if (active) {
          setIsLoggedIn(loggedIn);
        }
      })
      .catch(() => {
        if (active) {
          setIsLoggedIn(false);
        }
      });
    return () => {
      active = false;
    };
  }, []);

  // 从 localStorage 恢复当前会话ID
  useEffect(() => {
    const savedSessionId = localStorage.getItem(`current_session_${userId}`);
    if (savedSessionId) {
      setCurrentSessionId(savedSessionId);
      loadSessionMessages(savedSessionId);
    } else {
      setIsInitializing(false);
    }
  }, [userId]);

  // 保存当前会话ID到 localStorage
  useEffect(() => {
    if (currentSessionId) {
      localStorage.setItem(`current_session_${userId}`, currentSessionId);
    } else {
      localStorage.removeItem(`current_session_${userId}`);
    }
  }, [currentSessionId, userId]);

  const loadSessionMessages = useCallback(async (sessionId) => {
    try {
      const session = await getSession(sessionId, userId);
      if (session && session.messages) {
        const formattedMessages = session.messages.map((msg) => ({
          id: msg.id,
          role: msg.role,
          content: msg.content,
          thinkContent: msg.think_content || ""
        }));
        setMessages(formattedMessages);
      }
    } catch (e) {
      console.error("加载会话消息失败:", e);
    } finally {
      setIsInitializing(false);
    }
  }, [userId]);

  const handleSelectSession = useCallback((session) => {
    if (session) {
      setCurrentSessionId(session.id);
      loadSessionMessages(session.id);
    } else {
      setCurrentSessionId(null);
      setMessages([]);
    }
  }, [loadSessionMessages]);

  const handleCreateSession = useCallback((session) => {
    setCurrentSessionId(session.id);
    setMessages([]);
  }, []);

  const handleNewChat = useCallback(async () => {
    try {
      const session = await createSession(userId, "新会话");
      setCurrentSessionId(session.id);
      setMessages([]);
    } catch (e) {
      console.error("创建新会话失败:", e);
    }
  }, [userId]);

  const handleLogin = () => {
    setIsLoggedIn(true);
    setShowLogin(false);
    setShowAdminPanel(true);
  };

  const handleLogout = async () => {
    await adminLogout();
    setIsLoggedIn(false);
    setShowAdminPanel(false);
  };

  const handleEnterAdmin = () => {
    if (isLoggedIn) {
      setShowAdminPanel(true);
    } else {
      setShowLogin(true);
    }
  };

  const handleBackToMain = () => {
    setShowAdminPanel(false);
  };

  const handleFileSelect = (e) => {
    const file = e.target.files?.[0];
    if (file) {
      const lowerName = String(file.name || "").toLowerCase();
      const ext = lowerName.includes(".") ? `.${lowerName.split(".").pop()}` : "";
      const allowedExt = new Set([".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".md", ".doc", ".docx", ".pdf", ".ppt", ".txt"]);
      if (!allowedExt.has(ext)) {
        alert("仅支持 md/doc/docx/pdf/ppt/txt 或图片文件");
        return;
      }
      if (file.size > 20 * 1024 * 1024) {
        alert("文件大小不能超过 20MB");
        return;
      }
      setSelectedFile(file);
      if (file.type.startsWith("image/")) {
        const reader = new FileReader();
        reader.onload = (event) => {
          setFilePreview(event.target?.result || null);
        };
        reader.readAsDataURL(file);
      } else {
        setFilePreview(null);
      }
    }
  };

  const handleRemoveFile = () => {
    setSelectedFile(null);
    setFilePreview(null);
  };

  const sendQuestion = async ({ question, imageData = null, fileName = "", displayContent = "", image = null }) => {
    const normalizedQuestion = String(question || "").trim();
    const normalizedDisplay = String(displayContent || normalizedQuestion || "").trim();
    if (!normalizedDisplay || loading) return;

    setLoading(true);
    setCurrentJobId("");
    setStoppingJob(false);
    const userMessage = { role: "user", content: normalizedDisplay, image };
    const assistantMessage = { role: "assistant", content: "", thinkContent: "" };
    setMessages((prev) => [...prev, userMessage, assistantMessage]);

    let thinkContent = "";
    let replyContent = "";
    let doneStatus = "done";
    try {
      for await (const chunk of streamProcessWithSession(normalizedQuestion, userId, currentSessionId, imageData, fileName)) {
        if (chunk?.type === "job") {
          setCurrentJobId(String(chunk.job_id || ""));
          continue;
        }

        if (chunk?.type === "msg") {
          thinkContent += chunk.msg || "";
        } else if (chunk?.type === "reply") {
          replyContent += chunk.msg || "";
        } else if (chunk?.type === "done") {
          doneStatus = String(chunk.status || "done");
          if (!replyContent) {
            replyContent = (chunk.msg || "").trim();
          }
        }
        setMessages((prev) => {
          const next = [...prev];
          const last = next[next.length - 1];
          if (last?.role === "assistant") {
            next[next.length - 1] = {
              ...last,
              content: replyContent || last.content,
              thinkContent,
            };
          }
          return next;
        });
      }

      if (doneStatus === "cancelled" || doneStatus === "timeout") {
        await loadSessionMessages(currentSessionId);
      }
    } catch (err) {
      setMessages((prev) => {
        const next = [...prev];
        const last = next[next.length - 1];
        if (last?.role === "assistant") {
          next[next.length - 1] = {
            ...last,
            content: `请求异常: ${String(err)}`,
          };
        }
        return next;
      });
    } finally {
      setLoading(false);
      setStoppingJob(false);
      setCurrentJobId("");
    }
  };

  const handleStopCurrentTask = async () => {
    if (!loading || !currentJobId || !currentSessionId || stoppingJob) return;
    setStoppingJob(true);
    try {
      await cancelQaJob(currentJobId, userId, currentSessionId);
    } catch (err) {
      alert(`终止失败: ${String(err?.message || err)}`);
      setStoppingJob(false);
    }
  };

  const handleCopyMessage = async (content, key) => {
    const text = String(content || "").trim();
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopiedMessageKey(key);
      setTimeout(() => {
        setCopiedMessageKey("");
      }, 1500);
    } catch {
      alert("复制失败，请检查浏览器权限");
    }
  };

  const handleResendMessage = async (content) => {
    if (loading) return;
    const text = String(content || "").replace(/\n\[(图片|文件):[^\]]+\]\s*$/u, "").trim();
    if (!text) return;
    setInput(text);
    await sendQuestion({ question: text, displayContent: text });
  };

  const handleRollbackQuestion = async (message) => {
    if (loading || !currentSessionId) return;
    const messageId = Number(message?.id || 0);
    if (!messageId) {
      alert("该问题尚未持久化，暂不可回撤");
      return;
    }
    try {
      const result = await rollbackToQuestion(currentSessionId, userId, messageId);
      const rolledQuestion = String(result?.question || "")
        .replace(/^\[图片\]\s*/u, "")
        .trim();
      setInput(rolledQuestion);
      await loadSessionMessages(currentSessionId);
    } catch (err) {
      alert(`回撤失败: ${String(err?.message || err)}`);
    }
  };

  const send = async () => {
    const question = input.trim();
    if ((!question && !selectedFile) || loading) return;
    setInput("");

    let imageData = null;
    let fileName = "";
    if (selectedFile) {
      try {
        const result = await uploadChatFile(selectedFile);
        fileName = selectedFile.name;
        if (result?.is_image) {
          imageData = result.base64;
        }
      } catch (err) {
        setMessages((prev) => [...prev, { role: "assistant", content: `文件上传失败: ${err.message}` }]);
        setLoading(false);
        setSelectedFile(null);
        setFilePreview(null);
        return;
      }
    }

    const isImageFile = Boolean(selectedFile && selectedFile.type.startsWith("image/"));
    const displayContent = selectedFile
      ? `${question}\n[${isImageFile ? "图片" : "文件"}: ${selectedFile.name}]`
      : question;
    const selectedFilePreview = filePreview;

    setSelectedFile(null);
    setFilePreview(null);

    await sendQuestion({
      question,
      imageData,
      fileName,
      displayContent,
      image: isImageFile ? selectedFilePreview : null,
    });
  };

  if (isInitializing) {
    return (
      <div className="page loading">
        <div className="loading-spinner">加载中...</div>
      </div>
    );
  }

  return (
    <div className="page with-sidebar">
      <SessionSidebar
        userId={userId}
        currentSessionId={currentSessionId}
        onSelectSession={handleSelectSession}
        onCreateSession={handleCreateSession}
      />
      <div className="main-content">
        <header className="header">
          <h1>HypertensionKG Agentic RAG</h1>
          <span className="sub">高血压知识图谱 + RAG + Agent 自主检索</span>
          <div className="header-actions">
            {currentSessionId && (
              <button className="new-chat-btn" onClick={handleNewChat}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <line x1="12" y1="5" x2="12" y2="19" />
                  <line x1="5" y1="12" x2="19" y2="12" />
                </svg>
                新对话
              </button>
            )}
            {isLoggedIn ? (
              <>
                <button className="admin-entry-btn" onClick={handleEnterAdmin}>
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
                    <polyline points="9 22 9 12 15 12 15 22" />
                  </svg>
                  后台管理
                </button>
                <button className="admin-entry-btn" onClick={handleLogout}>
                  退出登录
                </button>
              </>
            ) : (
              <button className="admin-entry-btn" onClick={() => setShowLogin(true)}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
                  <circle cx="12" cy="7" r="4" />
                </svg>
                管理员登录
              </button>
            )}
          </div>
        </header>

        {showLogin && (
          <LoginModal onClose={() => setShowLogin(false)} onLogin={handleLogin} />
        )}

        {showAdminPanel && (
          <div className="admin-panel-overlay">
            <div className="admin-panel">
              <div className="admin-panel-header">
                <button className="back-btn" onClick={handleBackToMain}>
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <path d="M19 12H5M12 19l-7-7 7-7" />
                  </svg>
                  返回首页
                </button>
              </div>
              <AdminDashboard onLogout={handleLogout} />
            </div>
          </div>
        )}

        <div className="tabs">
          <button
            className={activeTab === "chat" ? "tab active" : "tab"}
            type="button"
            onClick={() => setActiveTab("chat")}
          >
            智能问答
          </button>
          <button
            className={activeTab === "graph" ? "tab active" : "tab"}
            type="button"
            onClick={() => setActiveTab("graph")}
          >
            图谱可视化
          </button>
        </div>
        {activeTab === "chat" ? (
          <>
            <main className="chat">
              {messages.length === 0 ? (
                <div className="welcome">
                  <h2>欢迎使用高血压知识问答系统</h2>
                  <p>您可以询问关于高血压的任何问题，例如：</p>
                  <div className="examples">
                    <button onClick={() => setInput("高血压的常见症状有哪些？")}>
                      高血压的常见症状有哪些？
                    </button>
                    <button onClick={() => setInput("高血压患者应该如何控制饮食？")}>
                      高血压患者应该如何控制饮食？
                    </button>
                    <button onClick={() => setInput("常用的降压药物有哪些？")}>
                      常用的降压药物有哪些？
                    </button>
                  </div>
                </div>
              ) : (
                messages.map((m, i) => (
                  <div key={`${m.role}-${i}`} className={`msg ${m.role}`}>
                    <div className="role">{m.role === "user" ? "你" : "助手"}</div>
                    {(() => {
                      const toolStatus = getLatestToolStatus(m.thinkContent);
                      if (m.role !== "assistant" || !toolStatus || m.content) {
                        return null;
                      }
                      return <div className="live-tool-status">{toolStatus}</div>;
                    })()}
                    {m.image && (
                      <div className="msg-image-container">
                        <img src={m.image} alt="用户上传的图片" className="msg-image" />
                      </div>
                    )}
                    <div className={m.role === "assistant" ? "content assistant-markdown" : "content"}>
                      {m.role === "assistant"
                        ? renderMarkdownContent(m.content || "处理中...")
                        : (m.content || "")}
                    </div>
                    {m.role === "assistant" && m.thinkContent ? (
                      <details className="think">
                        <summary>思考过程</summary>
                        <pre>{m.thinkContent}</pre>
                      </details>
                    ) : null}
                    <div className="msg-actions">
                      {m.role === "assistant" ? (
                        <button
                          type="button"
                          className="msg-action-btn"
                          onClick={() => handleCopyMessage(m.content, `${m.role}-${i}`)}
                          disabled={!m.content}
                        >
                          {copiedMessageKey === `${m.role}-${i}` ? "已复制" : "复制"}
                        </button>
                      ) : null}
                      {m.role === "user" ? (
                        <>
                          <button
                            type="button"
                            className="msg-action-btn"
                            onClick={() => handleResendMessage(m.content)}
                            disabled={loading}
                          >
                            重新发送
                          </button>
                          <button
                            type="button"
                            className="msg-action-btn danger"
                            onClick={() => handleRollbackQuestion(m)}
                            disabled={loading || !currentSessionId || !m.id}
                          >
                            回撤到此问题
                          </button>
                        </>
                      ) : null}
                    </div>
                  </div>
                ))
              )}
            </main>
            <footer className="inputBar">
              {filePreview && (
                <div className="image-preview-container">
                  <img src={filePreview} alt="预览" className="image-preview" />
                  <button className="remove-image-btn" onClick={handleRemoveFile} type="button">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <line x1="18" y1="6" x2="6" y2="18" />
                      <line x1="6" y1="6" x2="18" y2="18" />
                    </svg>
                  </button>
                </div>
              )}
              {selectedFile && !filePreview ? (
                <div className="file-chip">
                  <span>{selectedFile.name}</span>
                  <button className="remove-image-btn" onClick={handleRemoveFile} type="button" aria-label="移除文件">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <line x1="18" y1="6" x2="6" y2="18" />
                      <line x1="6" y1="6" x2="18" y2="18" />
                    </svg>
                  </button>
                </div>
              ) : null}
              <div className="input-row">
                <label className="image-upload-btn" title="上传文件（图片、md、doc、docx、pdf、ppt、txt）">
                  <input
                    type="file"
                    accept="image/*,.md,.doc,.docx,.pdf,.ppt,.txt"
                    onChange={handleFileSelect}
                    style={{ display: "none" }}
                  />
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
                  </svg>
                </label>
                <textarea
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      if (loading) {
                        return;
                      }
                      e.preventDefault();
                      send();
                    }
                  }}
                  placeholder={selectedFile ? "描述文件内容或提问..." : "请输入高血压相关问题..."}
                />
                <button onClick={send} disabled={loading || (!input.trim() && !selectedFile)}>
                  {loading ? "发送中..." : "发送"}
                </button>
                {loading ? (
                  <button type="button" className="msg-action-btn danger" onClick={handleStopCurrentTask} disabled={!currentJobId || stoppingJob}>
                    {stoppingJob ? "终止中..." : "终止"}
                  </button>
                ) : null}
              </div>
            </footer>
          </>
        ) : (
          <GraphView />
        )}
      </div>
    </div>
  );
}
