import type { ChangeEvent } from "react";
import { useTranslation } from "react-i18next";

import styles from "./MessageComposer.module.css";
import { Button, Input, Textarea } from "../../shared/ui/primitives";

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

  return (
    <div className={styles.composer}>
      <label className="stack">
        <span>{t("chat.composerLabel")}</span>
        <Textarea
          value={draft}
          placeholder={t("chat.composerPlaceholder")}
          onChange={(event) => onDraftChange(event.target.value)}
        />
      </label>

      <Input type="file" onChange={onFileChange} aria-label={t("chat.upload")} />

      {selectedFile ? (
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

      <div className={styles.actions}>
        <Button onClick={onSend} disabled={loading}>
          {t("chat.send")}
        </Button>
        {loading ? (
          <Button tone="danger" onClick={onStop} disabled={stopping}>
            {t("chat.stop")}
          </Button>
        ) : null}
      </div>
    </div>
  );
}
