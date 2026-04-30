import { useTranslation } from "react-i18next";

import styles from "./AdminPages.module.css";
import AdminGraphWorkspace from "../../features/admin-graph/ui/AdminGraphWorkspace";
import { PageHeader } from "../../shared/ui/primitives";
import AdminSubnav from "../../widgets/admin/AdminSubnav";

export default function AdminGraphPage() {
  const { t } = useTranslation();

  return (
    <div className={styles.layout}>
      <PageHeader eyebrow={t("admin.eyebrow")} title={t("admin.graph")} description={t("admin.description")} />
      <AdminSubnav />
      <AdminGraphWorkspace />
    </div>
  );
}
