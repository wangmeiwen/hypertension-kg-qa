import { Component, type ErrorInfo, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import styles from "./AppErrorBoundary.module.css";
import { reportFrontendError } from "../../shared/lib/observability";
import { Button, ErrorState } from "../../shared/ui/primitives";

type AppErrorBoundaryState = {
  error: Error | null;
};

function AppErrorFallback({ error, onReset }: { error: Error; onReset: () => void }) {
  const { t } = useTranslation();

  return (
    <main className={styles.boundary}>
      <div className={styles.panel}>
        <ErrorState
          title={t("common.errorBoundary.title")}
          description={error.message || t("common.errorBoundary.description")}
          actions={
            <>
              <Button onClick={() => window.location.reload()}>{t("common.errorBoundary.refreshPage")}</Button>
              <Button tone="secondary" onClick={onReset}>
                {t("common.retry")}
              </Button>
              <Link to="/chat">
                <Button tone="ghost">{t("common.errorBoundary.backToChat")}</Button>
              </Link>
            </>
          }
        />
      </div>
    </main>
  );
}

export default class AppErrorBoundary extends Component<{ children: ReactNode }, AppErrorBoundaryState> {
  state: AppErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): AppErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    reportFrontendError(
      new Error(`${error.message}\n${info.componentStack || ""}`),
      "app_error_boundary",
    );
  }

  render() {
    if (this.state.error) {
      return <AppErrorFallback error={this.state.error} onReset={() => this.setState({ error: null })} />;
    }

    return this.props.children;
  }
}
