import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Navigate, Outlet, useLocation } from "react-router-dom";

import { useSessionStore } from "../../entities/session/model/useSessionStore";
import { useUserAuthStore } from "../../features/user_auth/model/useUserAuthStore";
import { subscribeUserTokenChanges } from "../../shared/api/http";
import { getCurrentUser, hasUserToken } from "../../shared/api/userAuthClient";
import { Skeleton } from "../../shared/ui/primitives";

export default function UserGuard() {
  const location = useLocation();
  const setUser = useUserAuthStore((state) => state.setUser);
  const clearUser = useUserAuthStore((state) => state.clearUser);
  const setUserId = useSessionStore((state) => state.setUserId);
  const [tokenPresent, setTokenPresent] = useState(hasUserToken);
  const authQuery = useQuery({
    queryKey: ["user-auth", "me"],
    queryFn: getCurrentUser,
    enabled: tokenPresent,
    retry: false,
  });

  useEffect(() => subscribeUserTokenChanges(() => setTokenPresent(hasUserToken())), []);

  useEffect(() => {
    if (authQuery.data) {
      setUser(authQuery.data);
      setUserId(authQuery.data.id);
    }
  }, [authQuery.data, setUser, setUserId]);

  useEffect(() => {
    if (!tokenPresent || authQuery.isError) {
      clearUser();
      setUserId("");
    }
  }, [tokenPresent, authQuery.isError, clearUser, setUserId]);

  if (!tokenPresent || authQuery.isError) {
    return <Navigate to="/login" state={{ from: location.pathname }} replace />;
  }
  if (authQuery.isPending || !authQuery.data) return <Skeleton>正在确认登录状态...</Skeleton>;
  return <Outlet />;
}
