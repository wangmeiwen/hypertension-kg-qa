import type { ReactNode } from "react";

interface InlineNoticeProps {
  tone?: "info" | "danger";
  title?: string;
  children?: ReactNode;
}

export default function InlineNotice({
  tone = "info",
  title = "",
  children = null,
}: InlineNoticeProps) {
  return (
    <div className={`inline-notice ${tone}`}>
      {title ? <div className="inline-notice-title">{title}</div> : null}
      {children ? <div className="inline-notice-body">{children}</div> : null}
    </div>
  );
}
