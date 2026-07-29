import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import styles from "./AdminPages.module.css";
import {
  clearAdminSessionMessages,
  deleteAdminSession,
  getAdminSessionMessages,
  getAdminSessions,
  getAdminStats,
  getReadinessStatus,
} from "../../shared/api/adminClient";
import { formatDateTime, truncateText } from "../../shared/lib/format";
import { reportFrontendError } from "../../shared/lib/observability";
import { queryKeys } from "../../shared/queries/queryKeys";
import {
  Badge,
  Button,
  Card,
  ConfirmDialog,
  DataTable,
  Modal,
  PageHeader,
} from "../../shared/ui/primitives";
import AdminSubnav from "../../widgets/admin/AdminSubnav";

// 管理后台会话页展示系统概览、会话列表、消息详情和危险操作入口。
export default function AdminSessionsPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [pendingDeleteSessionId, setPendingDeleteSessionId] = useState<string | null>(null);

  const statsQuery = useQuery({ queryKey: queryKeys.admin.stats, queryFn: getAdminStats });
  const readinessQuery = useQuery({
    queryKey: queryKeys.admin.readiness,
    queryFn: getReadinessStatus,
  });
  const sessionsQuery = useQuery({
    queryKey: queryKeys.admin.sessions(100, 0),
    queryFn: () => getAdminSessions(100, 0),
  });
  const sessionDetailQuery = useQuery({
    queryKey: queryKeys.admin.sessionMessages(selectedSessionId),
    queryFn: () => getAdminSessionMessages(selectedSessionId as string),
    enabled: Boolean(selectedSessionId),
  });

  const deleteMutation = useMutation({
    mutationFn: (sessionId: string) => deleteAdminSession(sessionId),
    onSuccess: async () => {
      setSelectedSessionId(null);
      setPendingDeleteSessionId(null);
      await queryClient.invalidateQueries({ queryKey: queryKeys.admin.sessions(100, 0) });
      await queryClient.invalidateQueries({ queryKey: queryKeys.admin.stats });
    },
    onError: (error) => reportFrontendError(error, "admin_delete_session"),
  });

  const clearMutation = useMutation({
    mutationFn: (sessionId: string) => clearAdminSessionMessages(sessionId),
    onSuccess: async (_, sessionId) => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.admin.sessionMessages(sessionId) });
      await queryClient.invalidateQueries({ queryKey: queryKeys.admin.sessions(100, 0) });
    },
    onError: (error) => reportFrontendError(error, "admin_clear_session"),
  });

  const stats = statsQuery.data;
  const readiness = readinessQuery.data;
  const rows =
    // DataTable 行由会话数据派生，操作列打开详情或进入删除确认。
    sessionsQuery.data?.data.map((session) => [
      session.id,
      truncateText(session.title, 32),
      String(session.message_count),
      formatDateTime(session.updated_at),
      <div key={`actions-${session.id}`} className={styles.rowActions}>
        <Button
          tone="secondary"
          data-testid={`admin-open-session-${session.id}`}
          onClick={() => setSelectedSessionId(session.id)}
        >
          {t("admin.sessionMessages")}
        </Button>
        <Button
          tone="danger"
          data-testid={`admin-delete-session-${session.id}`}
          onClick={() => setPendingDeleteSessionId(session.id)}
        >
          {t("common.delete")}
        </Button>
      </div>,
    ]) || [];

  return (
    <div className={styles.layout}>
      <PageHeader
        eyebrow={t("admin.eyebrow")}
        title={t("admin.title")}
        description={t("admin.description")}
      />
      <AdminSubnav />

      <div className={styles.statsGrid}>
        <Card title={t("admin.stats.totalSessions")}>
          <strong>{stats?.total_sessions ?? 0}</strong>
        </Card>
        <Card title={t("admin.stats.totalMessages")}>
          <strong>{stats?.total_messages ?? 0}</strong>
        </Card>
        <Card title={t("admin.stats.todaySessions")}>
          <strong>{stats?.today_sessions ?? 0}</strong>
        </Card>
        <Card title={t("admin.stats.todayMessages")}>
          <strong>{stats?.today_messages ?? 0}</strong>
        </Card>
      </div>

      <Card
        title={t("admin.readiness")}
        action={<Badge>{readiness?.level || t("common.loading")}</Badge>}
      >
        <div className="stack">
          <div>{formatDateTime(readiness?.checked_at)}</div>
          <div className="muted">{readiness?.critical_not_ready?.join(" / ") || "-"}</div>
        </div>
      </Card>

      <Card title={t("admin.sessions")}>
        <DataTable
          columns={[
            t("admin.table.id"),
            t("admin.table.title"),
            t("admin.table.messageCount"),
            t("admin.table.updatedAt"),
            t("admin.table.actions"),
          ]}
          rows={rows}
        />
      </Card>

      <Modal open={Boolean(selectedSessionId)} onClose={() => setSelectedSessionId(null)}>
        {/* 消息详情按选中会话懒加载，避免列表页一次性拉取全部消息。 */}
        <div className={styles.modalSection}>
          <h2 style={{ margin: 0 }}>
            {sessionDetailQuery.data?.session.title || t("admin.sessionMessages")}
          </h2>
          <div className="inlineMeta">
            <Button
              tone="danger"
              onClick={() =>
                selectedSessionId && void deleteMutation.mutateAsync(selectedSessionId)
              }
            >
              {t("admin.deleteSession")}
            </Button>
            <Button
              tone="secondary"
              onClick={() => selectedSessionId && void clearMutation.mutateAsync(selectedSessionId)}
            >
              {t("admin.clearMessages")}
            </Button>
          </div>
          <div className="stack">
            {(sessionDetailQuery.data?.messages || []).map((message, index) => (
              <Card
                key={`${message.id || index}`}
                title={`${message.role} #${message.id || index + 1}`}
              >
                <div className="stack">
                  <div className="muted">{formatDateTime(message.created_at)}</div>
                  <div>{message.content}</div>
                </div>
              </Card>
            ))}
          </div>
        </div>
      </Modal>

      {/* 删除会话使用共享确认框，避免误删不可恢复数据。 */}
      <ConfirmDialog
        open={Boolean(pendingDeleteSessionId)}
        title={t("admin.deleteSession")}
        description={t("admin.deleteSessionConfirm")}
        confirmLabel={t("common.delete")}
        cancelLabel={t("common.cancel")}
        onClose={() => setPendingDeleteSessionId(null)}
        onConfirm={() =>
          pendingDeleteSessionId && void deleteMutation.mutateAsync(pendingDeleteSessionId)
        }
      />
    </div>
  );
}
