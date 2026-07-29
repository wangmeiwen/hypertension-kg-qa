import { NavLink } from "react-router-dom";

const NAV_ITEMS = [
  {
    to: "/chat",
    label: "问答",
    description: "智能问答工作台",
  },
  {
    to: "/graph",
    label: "图谱",
    description: "知识图谱浏览",
  },
  {
    to: "/admin",
    label: "管理",
    description: "运维管理后台",
  },
];

interface WorkspaceNavProps {
  collapsed: boolean;
  onToggleCollapsed: () => void;
}

export default function WorkspaceNav({ collapsed, onToggleCollapsed }: WorkspaceNavProps) {
  return (
    <nav className={`workspace-nav${collapsed ? " collapsed" : ""}`} aria-label="工作区导航">
      <button
        type="button"
        className="workspace-nav-toggle"
        onClick={onToggleCollapsed}
        aria-label={collapsed ? "展开工作区导航" : "收起工作区导航"}
        title={collapsed ? "展开" : "收起"}
      >
        <span
          className={`workspace-nav-toggle-icon${collapsed ? " collapsed" : ""}`}
          aria-hidden="true"
        >
          ‹
        </span>
      </button>

      <div className="workspace-brand">
        <div className="workspace-brand-mark">HK</div>
        <div className="workspace-brand-copy">
          <div className="workspace-brand-title">HypertensionKG</div>
          <div className="workspace-brand-subtitle">高血压智能工作台</div>
        </div>
      </div>

      <div className="workspace-nav-links">
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) => `workspace-nav-link${isActive ? " active" : ""}`}
            title={collapsed ? item.label : undefined}
          >
            <span className="workspace-nav-label">{item.label}</span>
            <span className="workspace-nav-description">{item.description}</span>
          </NavLink>
        ))}
      </div>
    </nav>
  );
}
