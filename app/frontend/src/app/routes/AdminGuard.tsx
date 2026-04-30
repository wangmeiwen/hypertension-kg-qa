import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { Outlet } from "react-router-dom";

import { useAuthStore } from "../../features/auth/model/useAuthStore";
import LoginDialog from "../../features/auth/ui/LoginDialog";
import { checkAdminStatus } from "../../shared/api/authClient";
import { queryKeys } from "../../shared/queries/queryKeys";
import { Button, Card, InlineNotice, Skeleton } from "../../shared/ui/primitives";

export default function AdminGuard() {
  const { t } = useTranslation();
  const isAuthenticated = useAuthStore((state) => state.isAdminAuthenticated);
  const setAuthenticated = useAuthStore((state) => state.setAuthenticated);
  const openLoginDialog = useAuthStore((state) => state.openLoginDialog);
  const isLoginDialogOpen = useAuthStore((state) => state.isLoginDialogOpen);
  const closeLoginDialog = useAuthStore((state) => state.closeLoginDialog);

  const authQuery = useQuery({
    queryKey: queryKeys.auth.status,
    queryFn: checkAdminStatus,
  });

  useEffect(() => {
    if (typeof authQuery.data === "boolean" && authQuery.data !== isAuthenticated) {
      setAuthenticated(authQuery.data);
    }
  }, [authQuery.data, isAuthenticated, setAuthenticated]);

  if (authQuery.isLoading) {
    return <Skeleton>{t("common.loading")}</Skeleton>;
  }

  if (!authQuery.data && !isAuthenticated) {
    return (
      <>
        <Card title={t("common.unauthorized")}>
          <InlineNotice tone="info" title={t("common.unauthorized")}>
            {t("admin.description")}
          </InlineNotice>
          <div style={{ marginTop: 16 }}>
            <Button onClick={openLoginDialog}>{t("admin.login")}</Button>
          </div>
        </Card>
        <LoginDialog open={isLoginDialogOpen} onClose={closeLoginDialog} />
      </>
    );
  }

  return (
    <>
      <Outlet />
      <LoginDialog open={isLoginDialogOpen} onClose={closeLoginDialog} />
    </>
  );
}
