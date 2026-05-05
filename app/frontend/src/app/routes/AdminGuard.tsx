import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { Outlet } from "react-router-dom";

import { useAuthStore } from "../../features/auth/model/useAuthStore";
import LoginDialog from "../../features/auth/ui/LoginDialog";
import { checkAdminStatus } from "../../shared/api/authClient";
import { queryKeys } from "../../shared/queries/queryKeys";
import { Button, Card, InlineNotice, Skeleton } from "../../shared/ui/primitives";

// 管理员路由守卫负责同步服务端登录态，并在未授权时展示登录入口。
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
    // 服务端状态是最终来源，查询结果变化时同步本地 store。
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
