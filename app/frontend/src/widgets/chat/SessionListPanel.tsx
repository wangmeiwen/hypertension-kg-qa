import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import styles from "./SessionListPanel.module.css";
import { useSessionStore } from "../../entities/session/model/useSessionStore";
import { createSession, deleteSession, listSessions, updateSession } from "../../shared/api/sessionClient";
import { formatRelativeTime, truncateText } from "../../shared/lib/format";
import { reportFrontendError, trackEvent } from "../../shared/lib/observability";
import { queryKeys } from "../../shared/queries/queryKeys";
import { Button, Card, EmptyState, Input, Skeleton, VirtualList } from "../../shared/ui/primitives";

export default function SessionListPanel() {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const userId = useSessionStore((state) => state.userId);
  const currentSessionId = useSessionStore((state) => state.currentSessionId);
  const setCurrentSessionId = useSessionStore((state) => state.setCurrentSessionId);
  const setMessages = useSessionStore((state) => state.setMessages);

  const [editingId, setEditingId] = useState<string | null>(null);
  const [draftTitle, setDraftTitle] = useState("");
  const [error, setError] = useState("");

  const sessionsQuery = useQuery({
    queryKey: queryKeys.sessions.list(userId),
    queryFn: () => listSessions(userId),
  });

  const createMutation = useMutation({
    mutationFn: () => createSession(userId),
    onSuccess: async (session) => {
      trackEvent("chat_new_session", { sessionId: session.id });
      setCurrentSessionId(session.id);
      setMessages([]);
      await queryClient.invalidateQueries({ queryKey: queryKeys.sessions.list(userId) });
    },
    onError: (mutationError) => {
      reportFrontendError(mutationError, "chat_create_session");
      setError(mutationError instanceof Error ? mutationError.message : String(mutationError));
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ sessionId, title }: { sessionId: string; title: string }) => updateSession(sessionId, userId, title),
    onSuccess: async () => {
      setEditingId(null);
      setDraftTitle("");
      await queryClient.invalidateQueries({ queryKey: queryKeys.sessions.list(userId) });
    },
    onError: (mutationError) => {
      reportFrontendError(mutationError, "chat_rename_session");
      setError(mutationError instanceof Error ? mutationError.message : String(mutationError));
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (sessionId: string) => deleteSession(sessionId, userId),
    onSuccess: async (_, sessionId) => {
      if (sessionId === currentSessionId) {
        setCurrentSessionId(null);
        setMessages([]);
      }
      await queryClient.invalidateQueries({ queryKey: queryKeys.sessions.list(userId) });
    },
    onError: (mutationError) => {
      reportFrontendError(mutationError, "chat_delete_session");
      setError(mutationError instanceof Error ? mutationError.message : String(mutationError));
    },
  });

  async function handleSave(sessionId: string) {
    const nextTitle = draftTitle.trim();
    if (!nextTitle) {
      setEditingId(null);
      return;
    }
    await updateMutation.mutateAsync({ sessionId, title: nextTitle });
  }

  const sessions = sessionsQuery.data || [];
  const renderSession = (session: (typeof sessions)[number]) => (
    <div
      className={`${styles.item} ${currentSessionId === session.id ? styles.active : ""}`.trim()}
      onClick={() => {
        setCurrentSessionId(session.id);
        setMessages([]);
      }}
      role="button"
      tabIndex={0}
    >
      <div className={styles.top}>
        <div>
          {editingId === session.id ? (
            <Input
              data-testid={`session-rename-input-${session.id}`}
              value={draftTitle}
              onChange={(event) => setDraftTitle(event.target.value)}
              onBlur={() => void handleSave(session.id)}
              onClick={(event) => event.stopPropagation()}
              autoFocus
            />
          ) : (
            <div className={styles.title}>{truncateText(session.title || t("chat.newSession"), 20)}</div>
          )}
          <div className={styles.meta}>
            {formatRelativeTime(session.updated_at || session.created_at, i18n.language)}
          </div>
        </div>
        <div className={styles.meta}>{session.message_count}</div>
      </div>
      <div className={styles.actions}>
        <Button
          tone="secondary"
          data-testid={`session-rename-button-${session.id}`}
          onClick={(event) => {
            event.stopPropagation();
            setEditingId(session.id);
            setDraftTitle(session.title || "");
          }}
        >
          {t("common.rename")}
        </Button>
        <Button
          tone="danger"
          data-testid={`session-delete-button-${session.id}`}
          onClick={(event) => {
            event.stopPropagation();
            if (window.confirm(t("chat.deleteConfirm"))) {
              void deleteMutation.mutateAsync(session.id);
            }
          }}
        >
          {t("common.delete")}
        </Button>
      </div>
    </div>
  );

  return (
    <Card
      title={
        <div className={styles.header}>
          <div>
            <h2 style={{ margin: 0 }}>{t("chat.newSession")}</h2>
            <div className="muted">{t("chat.askHint")}</div>
          </div>
          <Button onClick={() => createMutation.mutate()}>{t("chat.newSession")}</Button>
        </div>
      }
      className={styles.panel}
    >
      {error ? <div className="dangerText">{error}</div> : null}
      {sessionsQuery.isLoading ? <Skeleton>{t("common.loading")}</Skeleton> : null}
      {!sessionsQuery.isLoading && sessions.length === 0 ? <EmptyState>{t("chat.noMessages")}</EmptyState> : null}
      <VirtualList
        items={sessions}
        className={styles.list}
        estimateSize={132}
        threshold={100}
        maxHeight={620}
        getKey={(session) => session.id}
        renderItem={renderSession}
      />
    </Card>
  );
}
