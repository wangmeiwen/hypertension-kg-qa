import { useTranslation } from "react-i18next";

import styles from "./MessageThread.module.css";
import type { UiMessage } from "../../entities/session/model/useSessionStore";
import { renderMarkdownContent } from "../../features/chat/markdown_renderer";
import { Button, EmptyState, VirtualList } from "../../shared/ui/primitives";

export default function MessageThread({
  messages,
  loading,
  onCopy,
  onRollback,
  onResend,
}: {
  messages: UiMessage[];
  loading: boolean;
  onCopy: (message: UiMessage, index: number) => void;
  onRollback: (message: UiMessage) => void;
  onResend: (message: UiMessage) => void;
}) {
  const { t } = useTranslation();

  if (!messages.length && !loading) {
    return <EmptyState>{t("chat.noMessages")}</EmptyState>;
  }

  const renderMessage = (message: UiMessage, index: number) => (
    <article
      className={`${styles.bubble} ${message.role === "assistant" ? styles.assistant : styles.user}`.trim()}
    >
      <div className={styles.header}>
        <div className={styles.role}>{message.role === "assistant" ? t("chat.assistant") : t("chat.user")}</div>
        <div className={styles.actions}>
          <Button tone="secondary" onClick={() => onCopy(message, index)}>
            {t("chat.copy")}
          </Button>
          {message.role === "user" ? (
            <Button tone="ghost" onClick={() => onResend(message)}>
              {t("chat.resend")}
            </Button>
          ) : null}
          {message.role === "user" && message.id ? (
            <Button tone="ghost" onClick={() => onRollback(message)}>
              {t("chat.rollback")}
            </Button>
          ) : null}
        </div>
      </div>
      {message.image ? (
        <div className={styles.imagePreview}>
          <img src={message.image} alt="" />
        </div>
      ) : null}
      {message.thinkContent ? <div className={`${styles.think} muted`}>{message.thinkContent}</div> : null}
      <div className="messageMarkdown">{renderMarkdownContent(message.content)}</div>
    </article>
  );

  return (
    <VirtualList
      items={messages}
      className={styles.thread}
      estimateSize={178}
      threshold={80}
      maxHeight={680}
      getKey={(message, index) => message.id || message.clientId || `${message.role}-${index}`}
      renderItem={renderMessage}
    />
  );
}
