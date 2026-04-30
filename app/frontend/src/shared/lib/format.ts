export function formatDateTime(value?: string, locale = "zh-CN"): string {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(locale, { hour12: false });
}

export function formatRelativeTime(value?: string, locale = "zh-CN"): string {
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
  return value.length > maxLength ? `${value.slice(0, maxLength)}...` : value;
}

export function formatDurationMs(value: number | undefined): string {
  if (!value || Number.isNaN(value)) return "-";
  if (value < 1000) return `${Math.round(value)} ms`;
  return `${(value / 1000).toFixed(2)} s`;
}
