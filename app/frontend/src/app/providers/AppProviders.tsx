import { QueryClientProvider } from "@tanstack/react-query";
import { useEffect, type ReactNode } from "react";
import { I18nextProvider } from "react-i18next";

import i18n from "../../shared/lib/i18n";
import { reportFrontendError } from "../../shared/lib/observability";
import { queryClient } from "../../shared/queries/queryClient";

export default function AppProviders({ children }: { children: ReactNode }) {
  useEffect(() => {
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
