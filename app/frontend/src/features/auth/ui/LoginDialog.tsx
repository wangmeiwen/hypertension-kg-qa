import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { loginAsAdmin } from "../../../shared/api/authClient";
import { reportFrontendError, trackEvent } from "../../../shared/lib/observability";
import { queryKeys } from "../../../shared/queries/queryKeys";
import { Button, Input, Modal } from "../../../shared/ui/primitives";
import { useAuthStore } from "../model/useAuthStore";

// 统一提取未知异常中的可展示错误信息。
function getErrorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  if (
    error &&
    typeof error === "object" &&
    "message" in error &&
    typeof error.message === "string"
  ) {
    return error.message;
  }
  return String(error);
}

export default function LoginDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const setAuthenticated = useAuthStore((state) => state.setAuthenticated);
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("admin123");
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    // 登录成功后同步本地认证状态和 React Query 缓存，再关闭弹窗。
    event.preventDefault();
    setPending(true);
    setError("");
    try {
      await loginAsAdmin(username.trim(), password);
      setAuthenticated(true);
      queryClient.setQueryData(queryKeys.auth.status, true);
      trackEvent("admin_login_success");
      await queryClient.invalidateQueries({ queryKey: queryKeys.auth.status });
      onClose();
    } catch (submitError) {
      reportFrontendError(submitError, "admin_login");
      setError(getErrorMessage(submitError));
    } finally {
      setPending(false);
    }
  }

  return (
    <Modal open={open} onClose={onClose}>
      <form className="stack" onSubmit={handleSubmit}>
        <h2 style={{ margin: 0 }}>{t("admin.login")}</h2>
        <p className="muted" style={{ margin: 0 }}>
          {t("admin.loginHint")}
        </p>
        {error ? <p className="dangerText">{error}</p> : null}
        <label className="stack">
          <span>{t("admin.username")}</span>
          <Input value={username} onChange={(event) => setUsername(event.target.value)} />
        </label>
        <label className="stack">
          <span>{t("admin.password")}</span>
          <Input
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </label>
        <div className="inlineMeta">
          <Button type="submit" disabled={pending}>
            {pending ? t("common.loading") : t("admin.login")}
          </Button>
          <Button type="button" tone="secondary" onClick={onClose}>
            {t("common.cancel")}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
