import { memo, useMemo } from "react";
import { useTranslation } from "react-i18next";

import styles from "./MessageThread.module.css";
import type { UiMessage } from "../../entities/session/model/useSessionStore";
import { renderMarkdownContent } from "../../features/chat/markdown_renderer";
import { Button, EmptyState, VirtualList } from "../../shared/ui/primitives";

type MessageThreadProps = {
  messages: UiMessage[];
  loading: boolean;
  onCopy: (message: UiMessage, index: number) => void;
  onRollback: (message: UiMessage) => void;
  onResend: (message: UiMessage) => void;
};

type MessageBubbleProps = {
  message: UiMessage;
  index: number;
  isLatest: boolean;
  loading: boolean;
  onCopy: (message: UiMessage, index: number) => void;
  onRollback: (message: UiMessage) => void;
  onResend: (message: UiMessage) => void;
};

const MessageBubble = memo(({ message, index, isLatest, loading, onCopy, onRollback, onResend }: MessageBubbleProps) => {
  const { t } = useTranslation();
  const renderedContent = useMemo(() => renderMarkdownContent(message.content), [message.content]);
  const showThinkStatus = Boolean(loading && isLatest && message.role === "assistant" && message.thinkContent);

  return (
    // assistant 消息使用 Markdown 渲染，user 消息保留同一渲染路径以支持附件文本。
    <article className={`${styles.bubble} ${message.role === "assistant" ? styles.assistant : styles.user}`.trim()}>
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
      {showThinkStatus ? <div className={`${styles.think} muted`}>{message.thinkContent}</div> : null}
      <div className="messageMarkdown">{renderedContent}</div>
    </article>
  );
});

MessageBubble.displayName = "MessageBubble";

// 消息线程负责展示用户/助手消息，并暴露复制、回滚、重新发送等动作入口。
const MessageThread = ({
  messages,
  loading,
  onCopy,
  onRollback,
  onResend,
}: MessageThreadProps) => {
  const { t } = useTranslation();

  if (!messages.length && !loading) {
    return <EmptyState>{t("chat.noMessages")}</EmptyState>;
  }

  const renderMessage = (message: UiMessage, index: number) => (
    <MessageBubble
      message={message}
      index={index}
      isLatest={index === messages.length - 1}
      loading={loading}
      onCopy={onCopy}
      onRollback={onRollback}
      onResend={onResend}
    />
  );

  return (
    <VirtualList
      items={messages}
      className={styles.thread}
      estimateSize={178}
      threshold={10000}
      maxHeight="none"
      getKey={(message, index) => message.id || message.clientId || `${message.role}-${index}`}
      renderItem={renderMessage}
    />
  );
};

export default MessageThread;
