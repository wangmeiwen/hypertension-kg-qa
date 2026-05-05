import { useTranslation } from "react-i18next";

import styles from "./AdminPages.module.css";
import AdminDocumentsWorkspace from "../../features/admin-documents/ui/AdminDocumentsWorkspace";
import { PageHeader } from "../../shared/ui/primitives";
import AdminSubnav from "../../widgets/admin/AdminSubnav";

// 文档向量化后台页面，组合页头、后台子导航和文档任务工作台。
export default function AdminDocumentsPage() {
  const { t } = useTranslation();

  return (
    <div className={styles.layout}>
      <PageHeader
        eyebrow={t("admin.eyebrow")}
        title={t("admin.documentsTitle")}
        description={t("admin.documentsDescription")}
      />
      <AdminSubnav />
      <AdminDocumentsWorkspace />
    </div>
  );
}
