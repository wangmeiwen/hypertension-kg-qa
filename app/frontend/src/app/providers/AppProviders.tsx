import { QueryClientProvider } from "@tanstack/react-query";
import { useEffect, type ReactNode } from "react";
import { I18nextProvider } from "react-i18next";

import i18n from "../../shared/lib/i18n";
import { reportFrontendError } from "../../shared/lib/observability";
import { queryClient } from "../../shared/queries/queryClient";

// 汇总应用级 Provider，并统一挂载浏览器全局错误上报监听。
export default function AppProviders({ children }: { children: ReactNode }) {
  useEffect(() => {
    // 捕获 React 边界外的脚本错误和 Promise 未处理拒绝。
    const onError = (event: ErrorEvent) => {
      reportFrontendError(event.error || event.message, "window_error");
    };
    const onRejection = (event: PromiseRejectionEvent) => {
      reportFrontendError(event.reason, "promise_rejection");
    };
    window.addEventListener("error", onError);
    window.addEventListener("unhandledrejection", onRejection);
    return () => {
      window.removeEventListener("error", onError);
      window.removeEventListener("unhandledrejection", onRejection);
    };
  }, []);

  return (
    <I18nextProvider i18n={i18n}>
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    </I18nextProvider>
  );
}
