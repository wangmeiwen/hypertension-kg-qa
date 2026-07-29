import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import styles from "./AdminPages.module.css";
import { getBaseUrl } from "../../shared/api/http";
import {
  getAdminPerfBaseline,
  getAdminQueueStatus,
  getAdminRuntimeFlags,
} from "../../shared/api/runtimeClient";
import { formatDateTime } from "../../shared/lib/format";
import { queryKeys } from "../../shared/queries/queryKeys";
import { Button, Card, PageHeader } from "../../shared/ui/primitives";
import AdminSubnav from "../../widgets/admin/AdminSubnav";

// 设置页集中展示前端语言、后端地址、运行时开关、队列和性能基线。
export default function AdminSettingsPage() {
  const { t, i18n } = useTranslation();

  const runtimeFlagsQuery = useQuery({
    queryKey: queryKeys.admin.runtimeFlags,
    queryFn: getAdminRuntimeFlags,
  });
  const perfBaselineQuery = useQuery({
    queryKey: queryKeys.admin.perfBaseline(1),
    queryFn: () => getAdminPerfBaseline(1),
  });
  const queueStatusQuery = useQuery({
    queryKey: queryKeys.admin.queueStatus,
    queryFn: getAdminQueueStatus,
  });

  return (
    <div className={styles.layout}>
      <PageHeader
        eyebrow={t("admin.eyebrow")}
        title={t("admin.settingsTitle")}
        description={t("admin.description")}
      />
      <AdminSubnav />

      <div className={styles.twoColumn}>
        <Card title={t("shell.language")}>
          <div className="inlineMeta">
            <Button
              tone={i18n.language === "zh-CN" ? "primary" : "secondary"}
              onClick={() => void i18n.changeLanguage("zh-CN")}
            >
              {t("shell.chinese")}
            </Button>
            <Button
              tone={i18n.language === "en-US" ? "primary" : "secondary"}
              onClick={() => void i18n.changeLanguage("en-US")}
            >
              {t("shell.english")}
            </Button>
          </div>
        </Card>
        <Card title={t("admin.backendUrl")}>
          <div className={styles.mono}>{getBaseUrl()}</div>
        </Card>
      </div>

      <div className={styles.twoColumn}>
        <Card title={t("admin.runtimeFlags")}>
          <div className="stack">
            {/* 当前只读展示 runtime flags，修改能力由后端管理接口决定。 */}
            <div className="muted">
              {t("admin.updatedAt")}: {formatDateTime(runtimeFlagsQuery.data?.updated_at)}
            </div>
            <div className={styles.mono}>
              {JSON.stringify(runtimeFlagsQuery.data?.flags || {}, null, 2)}
            </div>
          </div>
        </Card>
        <Card title={t("admin.queueStatus")}>
          <div className={styles.mono}>{JSON.stringify(queueStatusQuery.data || {}, null, 2)}</div>
        </Card>
      </div>

      <Card title={t("admin.perfBaseline")}>
        <div className={styles.mono}>{JSON.stringify(perfBaselineQuery.data || {}, null, 2)}</div>
      </Card>
    </div>
  );
}
