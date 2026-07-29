import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type ChangeEvent } from "react";
import { useTranslation } from "react-i18next";

import styles from "./ChatWorkspacePage.module.css";
import { useSessionStore, type UiMessage } from "../../entities/session/model/useSessionStore";
import {
  cancelQaJob,
  createSession,
  getSession,
  rollbackToQuestion,
  streamProcessWithSession,
  uploadChatFile,
} from "../../shared/api/sessionClient";
import { reportFrontendError, trackEvent } from "../../shared/lib/observability";
import { queryKeys } from "../../shared/queries/queryKeys";
import { Card, InlineNotice } from "../../shared/ui/primitives";
import MessageComposer from "../../widgets/chat/MessageComposer";
import MessageThread from "../../widgets/chat/MessageThread";
import SessionListPanel from "../../widgets/chat/SessionListPanel";

const STREAM_FLUSH_INTERVAL_MS = 80;
const MAX_STREAM_REPLY_CHARS = 60000;
const MAX_CHAT_UPLOAD_BYTES = 150 * 1024 * 1024;
const STREAM_TRUNCATED_NOTICE = "\n\n[回答内容过长，前端已停止继续追加后续片段。]";

function formatSessionMessages(
  session: Awaited<ReturnType<typeof getSession>> | undefined,
): UiMessage[] {
  // 将后端持久化消息字段转换为前端消息线程使用的 UI 模型。
  return (session?.messages || []).map((message) => ({
    id: message.id,
    role: message.role,
    content: message.content,
    thinkContent: "",
    createdAt: message.created_at,
  }));
}

function appendUnique(values: string[], next: string): string[] {
  return values.includes(next) ? values : [...values, next];
}

export default function ChatWorkspacePage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const scrollFrameRef = useRef<number | null>(null);
  const [feedback, setFeedback] = useState("");

  const userId = useSessionStore((state) => state.userId);
  const currentSessionId = useSessionStore((state) => state.currentSessionId);
  const messages = useSessionStore((state) => state.messages);
  const draft = useSessionStore((state) => state.draft);
  const loading = useSessionStore((state) => state.loading);
  const selectedFile = useSessionStore((state) => state.selectedFile);
  const filePreview = useSessionStore((state) => state.filePreview);
  const currentJobId = useSessionStore((state) => state.currentJobId);
  const stoppingJob = useSessionStore((state) => state.stoppingJob);
  const setCurrentSessionId = useSessionStore((state) => state.setCurrentSessionId);
  const setMessages = useSessionStore((state) => state.setMessages);
  const updateMessages = useSessionStore((state) => state.updateMessages);
  const appendMessages = useSessionStore((state) => state.appendMessages);
  const setDraft = useSessionStore((state) => state.setDraft);
  const setCopiedMessageKey = useSessionStore((state) => state.setCopiedMessageKey);
  const setSelectedFile = useSessionStore((state) => state.setSelectedFile);
  const setFilePreview = useSessionStore((state) => state.setFilePreview);
  const clearAttachment = useSessionStore((state) => state.clearAttachment);
  const setLoading = useSessionStore((state) => state.setLoading);
  const setCurrentJobId = useSessionStore((state) => state.setCurrentJobId);
  const setStoppingJob = useSessionStore((state) => state.setStoppingJob);

  const sessionDetailQuery = useQuery({
    queryKey: queryKeys.sessions.detail(userId, currentSessionId),
    queryFn: () => getSession(currentSessionId, userId),
    enabled: Boolean(userId && currentSessionId) && !loading,
  });

  useEffect(() => {
    if (sessionDetailQuery.data && !loading) {
      setMessages(formatSessionMessages(sessionDetailQuery.data));
    }
  }, [loading, sessionDetailQuery.data, setMessages]);

  useEffect(() => {
    const scrollElement = scrollRef.current;
    if (!scrollElement) return;
    if (scrollFrameRef.current !== null) {
      window.cancelAnimationFrame(scrollFrameRef.current);
    }
    scrollFrameRef.current = window.requestAnimationFrame(() => {
      scrollElement.scrollTo({ top: scrollElement.scrollHeight, behavior: "auto" });
      scrollFrameRef.current = null;
    });
    return () => {
      if (scrollFrameRef.current !== null) {
        window.cancelAnimationFrame(scrollFrameRef.current);
        scrollFrameRef.current = null;
      }
    };
  }, [loading, messages]);

  async function ensureSession(): Promise<string> {
    // 用户首次发送时自动创建会话，并刷新侧栏列表。
    if (currentSessionId) return currentSessionId;
    const session = await createSession(userId, t("chat.newSession"));
    setCurrentSessionId(session.id);
    await queryClient.invalidateQueries({ queryKey: queryKeys.sessions.list(userId) });
    return session.id;
  }

  function resolveThinkLabel(raw: string, stage = ""): string {
    const lowered = `${raw} ${stage}`.toLowerCase();
    if (lowered.includes("memory")) return t("chat.thinkStatus.memory");
    if (lowered.includes("orchestrator") || lowered.includes("planner"))
      return t("chat.thinkStatus.orchestration");
    if (
      lowered.includes("retrieval") ||
      lowered.includes("subagent") ||
      lowered.includes("kg") ||
      lowered.includes("rag")
    ) {
      return t("chat.thinkStatus.retrieval");
    }
    return t("chat.thinkStatus.answer");
  }

  async function handleSend(overrideQuestion?: string): Promise<void> {
    // 串起附件上传、会话准备、SSE 事件消费、临时消息更新和查询缓存刷新。
    const normalizedQuestion = (overrideQuestion ?? draft).trim();
    if (!normalizedQuestion || loading) return;

    setFeedback("");
    setLoading(true);
    setCurrentJobId("");
    setStoppingJob(false);

    const turnClientId = `pending-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const userClientId = `${turnClientId}-user`;
    const assistantClientId = `${turnClientId}-assistant`;
    const userMessage: UiMessage = {
      clientId: userClientId,
      role: "user",
      content: normalizedQuestion,
      image: filePreview,
    };

    let thinkStatuses = [t("chat.thinkStatus.memory")];
    let thinkContent = thinkStatuses.join("\n");
    appendMessages(userMessage, {
      clientId: assistantClientId,
      role: "assistant",
      content: "",
      thinkContent,
    });

    let streamSessionId = currentSessionId;
    let replyContent = "";
    let persistedIds: { userMessageId?: number; assistantMessageId?: number } = {};
    let updateTimer: number | null = null;
    let streamTruncated = false;

    const flushStreamMessage = () => {
      updateTimer = null;
      updateMessages((previous) => {
        const next = [...previous];
        const last = next[next.length - 1];
        if (last?.clientId === assistantClientId) {
          next[next.length - 1] = {
            ...last,
            id: persistedIds.assistantMessageId ?? last.id,
            content: replyContent,
            thinkContent,
          };
        }
        const beforeLast = next[next.length - 2];
        if (beforeLast?.clientId === userClientId && persistedIds.userMessageId) {
          next[next.length - 2] = { ...beforeLast, id: persistedIds.userMessageId };
        }
        return next;
      });
    };

    const scheduleStreamMessageFlush = () => {
      if (updateTimer !== null) return;
      updateTimer = window.setTimeout(flushStreamMessage, STREAM_FLUSH_INTERVAL_MS);
    };

    const flushStreamMessageNow = () => {
      if (updateTimer !== null) {
        window.clearTimeout(updateTimer);
      }
      flushStreamMessage();
    };

    try {
      let imageBase64: string | null = filePreview;
      let fileName = selectedFile?.name || "";
      let fileId = "";
      if (selectedFile) {
        const uploaded = await uploadChatFile(selectedFile);
        imageBase64 = uploaded.base64 || filePreview || null;
        fileName = uploaded.original_name || selectedFile.name;
        fileId = uploaded.file_id || uploaded.aliyun_file_id || "";
      }

      streamSessionId = await ensureSession();
      setDraft("");
      clearAttachment();
      trackEvent("chat_send", { sessionId: streamSessionId });

      for await (const event of streamProcessWithSession(
        normalizedQuestion,
        userId,
        streamSessionId,
        imageBase64,
        fileName,
        fileId,
      )) {
        // 流式事件分为任务、会话、持久化 ID、思考状态和回答增量。
        if (event.type === "job" && event.job_id) {
          setCurrentJobId(event.job_id);
        }
        if (event.type === "session" && event.session_id) {
          streamSessionId = event.session_id;
          setCurrentSessionId(event.session_id);
        }
        if (event.type === "persisted") {
          persistedIds = {
            userMessageId: event.user_message_id,
            assistantMessageId: event.assistant_message_id,
          };
        }
        if (event.type === "msg") {
          thinkStatuses = appendUnique(
            thinkStatuses,
            resolveThinkLabel(event.msg || "", event.stage),
          );
          thinkContent = thinkStatuses.join("\n");
        }
        if (event.type === "reply") {
          if (!streamTruncated) {
            const mergedReply = `${replyContent}${event.delta}`;
            if (mergedReply.length > MAX_STREAM_REPLY_CHARS) {
              replyContent = `${mergedReply.slice(0, MAX_STREAM_REPLY_CHARS)}${STREAM_TRUNCATED_NOTICE}`;
              streamTruncated = true;
            } else {
              replyContent = mergedReply;
            }
          }
        }
        if (event.type === "error") {
          throw new Error(event.message);
        }
        scheduleStreamMessageFlush();
      }
      flushStreamMessageNow();
    } catch (sendError) {
      if (updateTimer !== null) {
        window.clearTimeout(updateTimer);
        updateTimer = null;
      }
      reportFrontendError(sendError, "chat_stream");
      const message = sendError instanceof Error ? sendError.message : String(sendError);
      setFeedback(message);
      updateMessages((previous) => [
        ...previous,
        {
          role: "assistant",
          content: message,
        },
      ]);
    } finally {
      setLoading(false);
      setStoppingJob(false);
      setCurrentJobId("");
      if (streamSessionId) {
        await queryClient.invalidateQueries({ queryKey: queryKeys.sessions.list(userId) });
        await queryClient.invalidateQueries({
          queryKey: queryKeys.sessions.detail(userId, streamSessionId),
        });
      }
    }
  }

  async function handleStop(): Promise<void> {
    if (!currentJobId || !currentSessionId) return;
    try {
      setStoppingJob(true);
      await cancelQaJob(currentJobId, userId, currentSessionId);
      trackEvent("chat_stop", { sessionId: currentSessionId, jobId: currentJobId });
    } catch (stopError) {
      reportFrontendError(stopError, "chat_stop");
      setFeedback(stopError instanceof Error ? stopError.message : String(stopError));
    } finally {
      setStoppingJob(false);
    }
  }

  function handleFileChange(event: ChangeEvent<HTMLInputElement>): void {
    // 图片在前端预读为 base64 预览，普通文件延迟到发送时上传。
    const file = event.target.files?.[0];
    if (!file) return;
    if (file.size > MAX_CHAT_UPLOAD_BYTES) {
      window.alert(t("chat.fileTooLarge"));
      return;
    }
    setSelectedFile(file);
    if (file.type.startsWith("image/")) {
      const reader = new FileReader();
      reader.onload = (loadEvent) => {
        const result = loadEvent.target?.result;
        setFilePreview(typeof result === "string" ? result : null);
      };
      reader.readAsDataURL(file);
      return;
    }
    setFilePreview(null);
  }

  async function handleRollback(message: UiMessage): Promise<void> {
    // 回滚后把历史问题写回输入框，便于用户调整后重新发送。
    if (!message.id || !currentSessionId) return;
    try {
      const result = await rollbackToQuestion(currentSessionId, userId, message.id);
      if (result.question) {
        setDraft(result.question);
      }
      await queryClient.invalidateQueries({
        queryKey: queryKeys.sessions.detail(userId, currentSessionId),
      });
      trackEvent("chat_rollback", { sessionId: currentSessionId, messageId: message.id });
    } catch (rollbackError) {
      reportFrontendError(rollbackError, "chat_rollback");
      setFeedback(rollbackError instanceof Error ? rollbackError.message : String(rollbackError));
    }
  }

  function handleCopy(message: UiMessage, index: number): void {
    // 复制只操作消息正文，thinkContent 和附件不进入剪贴板。
    void navigator.clipboard.writeText(message.content || "");
    setCopiedMessageKey(`${message.id || message.clientId || index}`);
  }

  function handleResend(message: UiMessage): void {
    setDraft(message.content);
    setFeedback(t("chat.resendSuccess"));
  }

  return (
    <div className={styles.layout}>
      <h1 className={styles.title}>{t("chat.title")}</h1>

      <div className={styles.body}>
        <SessionListPanel />
        <div className={styles.chatColumn}>
          <Card className={styles.chatBox}>
            <div ref={scrollRef} className={styles.chatScroll}>
              <div className={styles.messageArea}>
                {feedback ? <InlineNotice tone="info">{feedback}</InlineNotice> : null}
                <MessageThread
                  messages={messages}
                  loading={loading}
                  onCopy={handleCopy}
                  onRollback={handleRollback}
                  onResend={handleResend}
                />
              </div>
              <div className={styles.composerDock}>
                <MessageComposer
                  draft={draft}
                  loading={loading}
                  stopping={stoppingJob}
                  selectedFile={selectedFile}
                  filePreview={filePreview}
                  onDraftChange={setDraft}
                  onFileChange={handleFileChange}
                  onClearFile={clearAttachment}
                  onSend={() => void handleSend()}
                  onStop={() => void handleStop()}
                />
              </div>
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}
