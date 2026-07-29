import { useEffect, useState } from "react";
import { Outlet } from "react-router-dom";

import WorkspaceNav from "./WorkspaceNav";

const WORKSPACE_NAV_COLLAPSED_KEY = "workspace_nav_collapsed";

export default function AppShell() {
  const [navCollapsed, setNavCollapsed] = useState(false);

  useEffect(() => {
    const saved = window.localStorage.getItem(WORKSPACE_NAV_COLLAPSED_KEY);
    if (saved === "true") {
      setNavCollapsed(true);
    }
  }, []);

  useEffect(() => {
    window.localStorage.setItem(WORKSPACE_NAV_COLLAPSED_KEY, String(navCollapsed));
  }, [navCollapsed]);

  return (
    <div className={`workspace-shell${navCollapsed ? " nav-collapsed" : ""}`}>
      <WorkspaceNav
        collapsed={navCollapsed}
        onToggleCollapsed={() => setNavCollapsed((prev) => !prev)}
      />
      <main className="workspace-content">
        <Outlet />
      </main>
    </div>
  );
}
