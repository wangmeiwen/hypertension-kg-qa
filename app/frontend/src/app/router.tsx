import { Suspense, lazy } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import AppErrorBoundary from "./error/AppErrorBoundary";
import AppLayout from "./layout/AppLayout";
import RouteFallback from "./router/RouteFallback";
import AdminGuard from "./routes/AdminGuard";
import UserGuard from "./routes/UserGuard";

const ChatWorkspacePage = lazy(() => import("../pages/chat/ChatWorkspacePage"));
const GraphExplorerPage = lazy(() => import("../pages/graph/GraphExplorerPage"));
const AdminSessionsPage = lazy(() => import("../pages/admin/AdminSessionsPage"));
const AdminObservabilityPage = lazy(() => import("../pages/admin/AdminObservabilityPage"));
const AdminGraphPage = lazy(() => import("../pages/admin/AdminGraphPage"));
const AdminDocumentsPage = lazy(() => import("../pages/admin/AdminDocumentsPage"));
const AdminSettingsPage = lazy(() => import("../pages/admin/AdminSettingsPage"));
const UserAuthPage = lazy(() => import("../pages/auth/UserAuthPage"));

// 统一声明应用路由树，并通过懒加载拆分前台、图谱和管理员页面代码。
export default function AppRouter() {
  return (
    <AppErrorBoundary>
      <Suspense fallback={<RouteFallback />}>
        <Routes>
          <Route path="/login" element={<UserAuthPage mode="login" />} />
          <Route path="/register" element={<UserAuthPage mode="register" />} />
          <Route path="/" element={<AppLayout />}>
            <Route index element={<Navigate to="/chat" replace />} />
            <Route element={<UserGuard />}>
              <Route path="chat" element={<ChatWorkspacePage />} />
            </Route>
            <Route path="graph" element={<GraphExplorerPage />} />
            <Route path="admin" element={<AdminGuard />}>
              <Route index element={<Navigate to="/admin/sessions" replace />} />
              <Route path="sessions" element={<AdminSessionsPage />} />
              <Route path="observability" element={<AdminObservabilityPage />} />
              <Route path="graph" element={<AdminGraphPage />} />
              <Route path="documents" element={<AdminDocumentsPage />} />
              <Route path="settings" element={<AdminSettingsPage />} />
            </Route>
            <Route path="*" element={<Navigate to="/chat" replace />} />
          </Route>
        </Routes>
      </Suspense>
    </AppErrorBoundary>
  );
}
