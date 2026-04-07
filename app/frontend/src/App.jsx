import { useMemo, useState, useEffect, useCallback, useRef } from "react";
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
const MEDICAL_DISCLAIMER_TEXT = "以上内容仅供参考，不构成诊断或治疗建议，不能替代专业医生意见；如有不适或紧急情况，请及时就医。";

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

const getDisclaimerBlockClass = (text) => (
  String(text || "").trim() === MEDICAL_DISCLAIMER_TEXT ? " medical-disclaimer-block" : ""
);

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
      const quoteText = quoteLines.join(" ");
      blocks.push(
        <blockquote key={`quote-${blocks.length}`} className="md-quote">
          {renderInlineMarkdown(quoteText, `quote-${blocks.length}`)}
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
      <p key={`p-${blocks.length}`} className={`md-paragraph${getDisclaimerBlockClass(paragraphText)}`}>
        {renderInlineMarkdown(paragraphText, `p-${blocks.length}`)}
      </p>
    );
  }

  return blocks;
};

const THINK_STATUS_META = [
  { stage: "memory", label: "正在加载记忆", keywords: ["正在加载记忆", "memory", "prepare_session_turn", "build_runtime_context", "ensure_memory_current", "resolve_query"] },
  { stage: "orchestrator", label: "主agent思考中", keywords: ["主agent思考中", "主代理", "orchestrator", "task_submitted", "task_running", "解析输入", "路由", "任务拆解", "门控"] },
  { stage: "subagent", label: "子agent正在检索", keywords: ["子agent正在检索", "subagent", "子代理", "检索", "kg", "rag", "search", "retrieval", "调度选中任务", "补检索"] },
  { stage: "answer", label: "答案生成中", keywords: ["答案生成中", "finalized", "orchestrator_done", "task_completed", "终答", "最终回答", "answer"] },
];

const THINK_LABELS = THINK_STATUS_META.map((item) => item.label);

const normalizeThinkStatus = (value, stage = "") => {
  const safeStage = String(stage || "").trim().toLowerCase();
  const byStage = THINK_STATUS_META.find((item) => item.stage === safeStage);
  if (byStage) return byStage.label;

  const text = String(value || "").trim();
  if (!text) return "";
  if (THINK_LABELS.includes(text)) return text;

  const lowered = text.toLowerCase();
  const matched = THINK_STATUS_META.find((item) => item.keywords.some((keyword) => lowered.includes(String(keyword).toLowerCase())));
  return matched?.label || "";
};

const appendThinkStatus = (statuses, value, stage = "") => {
  const label = normalizeThinkStatus(value, stage);
  if (!label || statuses.includes(label)) return statuses;
  return [...statuses, label];
};

const formatThinkStatuses = (statuses) => statuses.filter(Boolean).join("\n");

const getSimplifiedThinkStatuses = (thinkContent) => {
  const text = String(thinkContent || "").trim();
  if (!text) return [];

  const rows = text
    .split(/\n+/)
    .map((line) => line.trim())
    .filter(Boolean);
  const candidates = rows.length ? rows : [text];

  let statuses = [];
  candidates.forEach((row) => {
    const direct = normalizeThinkStatus(row);
    if (direct) {
      statuses = appendThinkStatus(statuses, direct);
    }
  });

  THINK_STATUS_META.forEach((item) => {
    if (item.keywords.some((keyword) => text.toLowerCase().includes(String(keyword).toLowerCase()))) {
      statuses = appendThinkStatus(statuses, item.label);
    }
  });
  return statuses;
};

const getLatestThinkStatus = (thinkContent) => {
  const statuses = getSimplifiedThinkStatuses(thinkContent);
  if (!statuses.length) return "";
  return statuses[statuses.length - 1];
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
  const chatScrollRef = useRef(null);

  const scrollChatToBottom = useCallback((behavior = "smooth") => {
    const chatNode = chatScrollRef.current;
    if (!chatNode) return;
    window.requestAnimationFrame(() => {
      chatNode.scrollTo({
        top: chatNode.scrollHeight,
        behavior,
      });
    });
  }, []);

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

  useEffect(() => {
    if (activeTab !== "chat") return;
    scrollChatToBottom(loading ? "auto" : "smooth");
  }, [activeTab, loading, messages, scrollChatToBottom]);

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
    const turnClientId = `pending-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const userClientId = `${turnClientId}-user`;
    const assistantClientId = `${turnClientId}-assistant`;
    const userMessage = { clientId: userClientId, role: "user", content: normalizedDisplay, image };
    let thinkStatuses = appendThinkStatus([], "正在加载记忆", "memory");
    let thinkContent = formatThinkStatuses(thinkStatuses);
    const assistantMessage = { clientId: assistantClientId, role: "assistant", content: "", thinkContent };
    setMessages((prev) => [...prev, userMessage, assistantMessage]);

    let replyContent = "";
    let doneStatus = "done";
    let streamSessionId = currentSessionId;
    const addThinkStatus = (value, stage = "") => {
      const nextStatuses = appendThinkStatus(thinkStatuses, value, stage);
      if (nextStatuses.length !== thinkStatuses.length) {
        thinkStatuses = nextStatuses;
        thinkContent = formatThinkStatuses(thinkStatuses);
        setMessages((prev) => prev.map((msg) => (
          msg.clientId === assistantClientId ? { ...msg, thinkContent } : msg
        )));
      }
    };
    const hydratePendingMessageIds = ({ sessionId = "", userMessageId = 0, assistantMessageId = 0 }) => {
      const normalizedSessionId = String(sessionId || "").trim();
      if (normalizedSessionId) {
        streamSessionId = normalizedSessionId;
        setCurrentSessionId(normalizedSessionId);
      }
      const normalizedUserMessageId = Number(userMessageId || 0);
      const normalizedAssistantMessageId = Number(assistantMessageId || 0);
      setMessages((prev) => prev.map((msg) => {
        if (msg.clientId === userClientId && normalizedUserMessageId > 0) {
          return { ...msg, id: normalizedUserMessageId };
        }
        if (msg.clientId === assistantClientId && normalizedAssistantMessageId > 0) {
          return { ...msg, id: normalizedAssistantMessageId };
        }
        return msg;
      }));
    };
    try {
      for await (const chunk of streamProcessWithSession(normalizedQuestion, userId, currentSessionId, imageData, fileName)) {
        if (chunk?.type === "job") {
          setCurrentJobId(String(chunk.job_id || ""));
          addThinkStatus("主agent思考中", "orchestrator");
          continue;
        }

        if (chunk?.type === "session") {
          hydratePendingMessageIds({
            sessionId: chunk.session_id,
            userMessageId: chunk.user_message_id,
          });
          addThinkStatus("主agent思考中", "orchestrator");
          continue;
        }

        if (chunk?.type === "persisted") {
          hydratePendingMessageIds({
            sessionId: chunk.session_id,
            userMessageId: chunk.user_message_id,
            assistantMessageId: chunk.assistant_message_id,
          });
          continue;
        }

        if (chunk?.type === "msg") {
          addThinkStatus(chunk.msg || "", chunk.stage || "");
        } else if (chunk?.type === "reply") {
          addThinkStatus("答案生成中", "answer");
          replyContent += chunk.msg || "";
        } else if (chunk?.type === "done") {
          addThinkStatus("答案生成中", "answer");
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

      if ((doneStatus === "cancelled" || doneStatus === "timeout") && streamSessionId) {
        await loadSessionMessages(streamSessionId);
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
          <div className="brand-copy">
            <h1>HypertensionKG Agentic RAG</h1>
            <span className="sub">高血压知识图谱智能问答系统</span>
          </div>
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
          <div className="chat-stage">
            <main className="chat" ref={chatScrollRef}>
              {messages.length === 0 ? (
                <div className="welcome">
                  <div className="welcome-kicker">医学知识问答</div>
                  <h2>高血压知识图谱问答助手</h2>
                  <p>用清晰、可追溯的方式了解高血压症状、风险、用药和生活方式建议。</p>
                  <p className="welcome-note">如出现胸痛、呼吸困难、意识异常或血压持续显著升高，请及时联系医生或急诊。</p>
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
                    {(() => {
                      const latestThinkStatus = m.role === "assistant" && !m.content
                        ? getLatestThinkStatus(m.thinkContent)
                        : "";
                      return (
                        <div className="role">
                          <span>{m.role === "user" ? "你" : "助手"}</span>
                          {m.role === "assistant" && !m.content ? (
                            <span className="processing-stage-badge">{latestThinkStatus || "处理中..."}</span>
                          ) : null}
                        </div>
                      );
                    })()}
                    {m.image && (
                      <div className="msg-image-container">
                        <img src={m.image} alt="用户上传的图片" className="msg-image" />
                      </div>
                    )}
                    <div className={m.role === "assistant" ? "content assistant-markdown" : "content"}>
                      {m.role === "assistant"
                        ? (m.content ? renderMarkdownContent(m.content) : null)
                        : (m.content || "")}
                    </div>
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
              <div className="composer-header">
                <div>
                  <p>请描述症状、血压数值、用药情况或想了解的高血压问题。</p>
                </div>
              </div>
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
                  aria-label="高血压相关问题"
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
                <button type="button" className="send-btn" onClick={send} disabled={loading || (!input.trim() && !selectedFile)}>
                  {loading ? "发送中..." : "发送"}
                </button>
                {loading ? (
                  <button type="button" className="msg-action-btn danger" onClick={handleStopCurrentTask} disabled={!currentJobId || stoppingJob}>
                    {stoppingJob ? "终止中..." : "终止"}
                  </button>
                ) : null}
              </div>
              <div className="medical-disclaimer">
                系统回答用于健康知识参考，不能替代医生诊断；急症或明显不适请及时就医。
              </div>
            </footer>
          </div>
        ) : (
          <GraphView />
        )}
      </div>
    </div>
  );
}
