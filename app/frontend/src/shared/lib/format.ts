// 将后端 ISO 时间格式化为当前语言环境下的日期时间文本。
export function formatDateTime(value?: string, locale = "zh-CN"): string {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(locale, { hour12: false });
}

export function formatRelativeTime(value?: string, locale = "zh-CN"): string {
  // 近期时间展示相对时间，超过一天后退回本地日期。
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const diff = Date.now() - date.getTime();
  const minute = 60_000;
  const hour = 60 * minute;
  const day = 24 * hour;

  if (diff < hour) {
    const minutes = Math.max(1, Math.floor(diff / minute));
    return locale.startsWith("zh") ? `${minutes} 分钟前` : `${minutes} min ago`;
  }
  if (diff < day) {
    const hours = Math.max(1, Math.floor(diff / hour));
    return locale.startsWith("zh") ? `${hours} 小时前` : `${hours}h ago`;
  }
  return date.toLocaleDateString(locale);
}

export function truncateText(value: string, maxLength = 80): string {
  // 用于表格/卡片摘要场景，避免长文本撑开布局。
  return value.length > maxLength ? `${value.slice(0, maxLength)}...` : value;
}

export function formatDurationMs(value: number | undefined): string {
  if (!value || Number.isNaN(value)) return "-";
  if (value < 1000) return `${Math.round(value)} ms`;
  return `${(value / 1000).toFixed(2)} s`;
}
