import { Component, type ErrorInfo, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import styles from "./AppErrorBoundary.module.css";
import { reportFrontendError } from "../../shared/lib/observability";
import { Button, ErrorState } from "../../shared/ui/primitives";

type AppErrorBoundaryState = {
  error: Error | null;
};

// 错误边界兜底 UI，提供刷新、重试和返回问答页三个恢复入口。
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

  // React class error boundary 必须使用该静态方法把渲染错误同步到 state。
  static getDerivedStateFromError(error: Error): AppErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // 上报组件栈，便于后端 observability 页面定位前端崩溃来源。
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
