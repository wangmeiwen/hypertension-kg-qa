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
  const queue = window.__HYK_TELEMETRY__ || [];
  queue.push({ name, payload });
  window.__HYK_TELEMETRY__ = queue;
}

export function reportFrontendError(error: unknown, context: string): void {
  const message = error instanceof Error ? error.message : String(error);
  trackEvent("frontend_error", { context, message });
  console.error(`[${context}]`, error);
}
