import { useState } from "react";
import { useTranslation } from "react-i18next";
import { NavLink, Outlet, useLocation } from "react-router-dom";

import styles from "./AppLayout.module.css";
import { Button } from "../../shared/ui/primitives";

const NAV_COLLAPSE_KEY = "workspace_nav_collapsed";

// 从 localStorage 恢复左侧导航折叠状态，保持用户跨刷新偏好。
function getInitialCollapsed(): boolean {
  return window.localStorage.getItem(NAV_COLLAPSE_KEY) === "true";
}

// 工作台主布局承载侧边导航、语言切换和当前路由页面出口。
export default function AppLayout() {
  const { t, i18n } = useTranslation();
  const location = useLocation();
  const [collapsed, setCollapsed] = useState(getInitialCollapsed);
  const isChatRoute = location.pathname === "/chat";

  function toggleCollapsed() {
    // 折叠状态同时写入本地存储，避免刷新后导航宽度跳变。
    const next = !collapsed;
    setCollapsed(next);
    window.localStorage.setItem(NAV_COLLAPSE_KEY, String(next));
  }

  return (
    <div className={`${styles.shell} ${collapsed ? styles.collapsed : ""}`.trim()}>
      <aside className={styles.nav}>
        <Button tone="secondary" className={styles.collapseButton} onClick={toggleCollapsed}>
          {collapsed ? ">" : "<"}
        </Button>

        <div className={styles.brand}>
          <div className={styles.mark}>HK</div>
          <div className={styles.brandMeta}>
            <div className={styles.brandTitle}>{t("appName")}</div>
            <div className={styles.brandSubtitle}>{t("appSubtitle")}</div>
          </div>
        </div>

        <nav className={styles.links}>
          {[
            { to: "/chat", label: t("shell.navChat"), description: t("shell.navChatDesc") },
            { to: "/graph", label: t("shell.navGraph"), description: t("shell.navGraphDesc") },
            { to: "/admin", label: t("shell.navAdmin"), description: t("shell.navAdminDesc") },
          ].map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) => `${styles.link} ${isActive ? styles.active : ""}`.trim()}
            >
              <span className={styles.linkLabel}>{item.label}</span>
              <span className={styles.linkDescription}>{item.description}</span>
            </NavLink>
          ))}
        </nav>
      </aside>

      <main className={`${styles.content} ${isChatRoute ? styles.chatContent : ""}`.trim()}>
        {!isChatRoute ? <div className={styles.topbar}>
          <div className={styles.languageSwitch}>
            <Button tone={i18n.language === "zh-CN" ? "primary" : "secondary"} onClick={() => void i18n.changeLanguage("zh-CN")}>
              {t("shell.chinese")}
            </Button>
            <Button tone={i18n.language === "en-US" ? "primary" : "secondary"} onClick={() => void i18n.changeLanguage("en-US")}>
              {t("shell.english")}
            </Button>
          </div>
        </div> : null}
        <Outlet />
      </main>
    </div>
  );
}
