import { useTranslation } from "react-i18next";
import { NavLink } from "react-router-dom";

import styles from "./AdminSubnav.module.css";

// 管理后台二级导航，和路由配置保持同一组后台功能入口。
export default function AdminSubnav() {
  const { t } = useTranslation();

  const items = [
    { to: "/admin/sessions", label: t("admin.sessions") },
    { to: "/admin/observability", label: t("admin.observability") },
    { to: "/admin/graph", label: t("admin.graph") },
    { to: "/admin/documents", label: t("admin.documents") },
    { to: "/admin/settings", label: t("admin.settings") },
  ];

  return (
    <nav className={styles.nav}>
      {items.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          className={({ isActive }) => `${styles.link} ${isActive ? styles.active : ""}`.trim()}
        >
          {item.label}
        </NavLink>
      ))}
    </nav>
  );
}
