import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState, type ChangeEvent } from "react";
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
import { Badge, Card, InlineNotice, PageHeader } from "../../shared/ui/primitives";
import MessageComposer from "../../widgets/chat/MessageComposer";
import MessageThread from "../../widgets/chat/MessageThread";
import SessionListPanel from "../../widgets/chat/SessionListPanel";

function formatSessionMessages(session: Awaited<ReturnType<typeof getSession>> | undefined): UiMessage[] {
  return (session?.messages || []).map((message) => ({
    id: message.id,
    role: message.role,
    content: message.content,
    thinkContent: message.think_content || "",
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
    if (!scrollRef.current) return;
    scrollRef.current.scrollTo({ top: scrollRef.current.scrollHeight, behavior: loading ? "auto" : "smooth" });
  }, [loading, messages]);

  const chatStatus = useMemo(() => {
    if (loading) return t("common.statusRunning");
    if (currentSessionId) return t("chat.sessionReady");
    return t("chat.askHint");
  }, [currentSessionId, loading, t]);

  async function ensureSession(): Promise<string> {
    if (currentSessionId) return currentSessionId;
    const session = await createSession(userId, t("chat.newSession"));
    setCurrentSessionId(session.id);
    await queryClient.invalidateQueries({ queryKey: queryKeys.sessions.list(userId) });
    return session.id;
  }

  function resolveThinkLabel(raw: string, stage = ""): string {
    const lowered = `${raw} ${stage}`.toLowerCase();
    if (lowered.includes("memory")) return t("chat.thinkStatus.memory");
    if (lowered.includes("orchestrator") || lowered.includes("planner")) return t("chat.thinkStatus.orchestration");
    if (lowered.includes("retrieval") || lowered.includes("subagent") || lowered.includes("kg") || lowered.includes("rag")) {
      return t("chat.thinkStatus.retrieval");
    }
    return t("chat.thinkStatus.answer");
  }

  async function handleSend(overrideQuestion?: string): Promise<void> {
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

    try {
      let imageBase64: string | null = filePreview;
      let fileName = selectedFile?.name || "";
      if (selectedFile && !filePreview) {
        const uploaded = await uploadChatFile(selectedFile);
        imageBase64 = uploaded.base64 || null;
        fileName = selectedFile.name;
      }

      streamSessionId = await ensureSession();
      setDraft("");
      clearAttachment();
      trackEvent("chat_send", { sessionId: streamSessionId });

      for await (const event of streamProcessWithSession(normalizedQuestion, userId, streamSessionId, imageBase64, fileName)) {
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
          thinkStatuses = appendUnique(thinkStatuses, resolveThinkLabel(event.msg || "", event.stage));
          thinkContent = thinkStatuses.join("\n");
        }
        if (event.type === "reply" && event.msg) {
          replyContent += event.msg;
        }
        updateMessages((previous) => {
          const next = [...previous];
          const last = next[next.length - 1];
          if (last?.clientId === assistantClientId) {
            next[next.length - 1] = {
              ...last,
              id: persistedIds.assistantMessageId || last.id,
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
      }
    } catch (sendError) {
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
        await queryClient.invalidateQueries({ queryKey: queryKeys.sessions.detail(userId, streamSessionId) });
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
    const file = event.target.files?.[0];
    if (!file) return;
    if (file.size > 20 * 1024 * 1024) {
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
    if (!message.id || !currentSessionId) return;
    try {
      const result = await rollbackToQuestion(currentSessionId, userId, message.id);
      if (result.question) {
        setDraft(result.question);
      }
      await queryClient.invalidateQueries({ queryKey: queryKeys.sessions.detail(userId, currentSessionId) });
      trackEvent("chat_rollback", { sessionId: currentSessionId, messageId: message.id });
    } catch (rollbackError) {
      reportFrontendError(rollbackError, "chat_rollback");
      setFeedback(rollbackError instanceof Error ? rollbackError.message : String(rollbackError));
    }
  }

  function handleCopy(message: UiMessage, index: number): void {
    void navigator.clipboard.writeText(message.content || "");
    setCopiedMessageKey(`${message.id || message.clientId || index}`);
  }

  function handleResend(message: UiMessage): void {
    setDraft(message.content);
    setFeedback(t("chat.resendSuccess"));
  }

  return (
    <div className={styles.layout}>
      <PageHeader
        eyebrow={t("chat.eyebrow")}
        title={t("chat.title")}
        description={t("chat.description")}
        actions={<Badge>{chatStatus}</Badge>}
      />

      {feedback ? <InlineNotice tone="info">{feedback}</InlineNotice> : null}

      <div className={styles.body}>
        <SessionListPanel />
        <div className="stack">
          <Card className={styles.messageCard}>
            <div ref={scrollRef}>
              <MessageThread
                messages={messages}
                loading={loading}
                onCopy={handleCopy}
                onRollback={handleRollback}
                onResend={handleResend}
              />
            </div>
          </Card>
          <Card title={<div className={styles.status}><Badge>{chatStatus}</Badge></div>}>
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
            <div className={styles.disclaimer}>{t("chat.disclaimer")}</div>
          </Card>
        </div>
      </div>
    </div>
  );
}
