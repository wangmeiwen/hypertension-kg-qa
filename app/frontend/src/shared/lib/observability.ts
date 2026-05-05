type TelemetryEvent = {
  name: string;
  payload?: Record<string, unknown>;
};

declare global {
  interface Window {
    __HYK_TELEMETRY__?: TelemetryEvent[];
  }
}

export function trackEvent(name: string, payload: Record<string, unknown> = {}): void {
  // 当前埋点先写入浏览器内存队列，便于测试和后续统一上报器接管。
  const queue = window.__HYK_TELEMETRY__ || [];
  queue.push({ name, payload });
  window.__HYK_TELEMETRY__ = queue;
}

export function reportFrontendError(error: unknown, context: string): void {
  // 错误上报同时写入 telemetry 队列和控制台，方便本地调试。
  const message = error instanceof Error ? error.message : String(error);
  trackEvent("frontend_error", { context, message });
  console.error(`[${context}]`, error);
}
