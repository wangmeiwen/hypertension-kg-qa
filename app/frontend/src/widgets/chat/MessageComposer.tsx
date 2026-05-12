import { useEffect, useRef, type ChangeEvent } from "react";
import { useTranslation } from "react-i18next";

import styles from "./MessageComposer.module.css";
import { Button, Input } from "../../shared/ui/primitives";

const MAX_DRAFT_LENGTH = 500;

// 聊天输入区是受控组件，所有草稿、附件和发送/停止动作由页面层管理。
export default function MessageComposer({
  draft,
  loading,
  stopping,
  selectedFile,
  filePreview,
  onDraftChange,
  onFileChange,
  onClearFile,
  onSend,
  onStop,
}: {
  draft: string;
  loading: boolean;
  stopping: boolean;
  selectedFile: File | null;
  filePreview: string | null;
  onDraftChange: (value: string) => void;
  onFileChange: (event: ChangeEvent<HTMLInputElement>) => void;
  onClearFile: () => void;
  onSend: () => void;
  onStop: () => void;
}) {
  const { t } = useTranslation();
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  const resizeTextarea = (textarea: HTMLTextAreaElement | null) => {
    if (!textarea) return;
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, 168)}px`;
  };

  useEffect(() => {
    resizeTextarea(textareaRef.current);
  }, [draft]);

  return (
    <div className={styles.composer}>
      <label className={styles.inputShell}>
        <span className={styles.inputLabel}>{t("chat.composerLabel")}</span>
        <textarea
          ref={textareaRef}
          className={styles.textarea}
          value={draft}
          rows={1}
          maxLength={MAX_DRAFT_LENGTH}
          placeholder={t("chat.composerPlaceholder")}
          onChange={(event) => {
            resizeTextarea(event.target);
            onDraftChange(event.target.value.slice(0, MAX_DRAFT_LENGTH));
          }}
        />
      </label>

      <div className={styles.controlRow}>
        <Input className={styles.fileInput} type="file" onChange={onFileChange} aria-label={t("chat.upload")} />
        <div className={styles.actions}>
          <Button onClick={onSend} disabled={loading}>
            {t("chat.send")}
          </Button>
          {loading ? (
            // 流式回答进行中才展示停止按钮，避免空闲状态误触取消。
            <Button tone="danger" onClick={onStop} disabled={stopping}>
              {t("chat.stop")}
            </Button>
          ) : null}
        </div>
      </div>

      {selectedFile ? (
        // 文件预览由上层生成；这里仅根据 filePreview 判断展示图片还是普通附件。
        <div className={styles.attachment}>
          <div className={styles.attachmentMeta}>
            <strong>{filePreview ? t("chat.attachmentImage") : t("chat.attachmentFile")}</strong>
            <span>{selectedFile.name}</span>
            <span className="muted">{Math.max(1, Math.round(selectedFile.size / 1024))} KB</span>
          </div>
          {filePreview ? (
            <div className={styles.attachmentPreview}>
              <img src={filePreview} alt="" />
            </div>
          ) : null}
          <Button tone="ghost" onClick={onClearFile}>
            {t("chat.removeAttachment")}
          </Button>
        </div>
      ) : null}
    </div>
  );
}
