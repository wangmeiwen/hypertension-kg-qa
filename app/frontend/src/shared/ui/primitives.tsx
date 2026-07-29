import { useVirtualizer } from "@tanstack/react-virtual";
import { useRef, type ComponentPropsWithoutRef, type CSSProperties, type ReactNode } from "react";

import styles from "./primitives.module.css";

type ButtonTone = "primary" | "secondary" | "danger" | "ghost";

// 基础按钮组件统一 tone 样式，并透传原生 button 属性。
export function Button({
  tone = "primary",
  children,
  className = "",
  ...props
}: ComponentPropsWithoutRef<"button"> & { tone?: ButtonTone }) {
  const toneClass =
    tone === "secondary"
      ? styles.secondary
      : tone === "danger"
        ? styles.danger
        : tone === "ghost"
          ? styles.ghost
          : "";
  return (
    <button {...props} className={`${styles.button} ${toneClass} ${className}`.trim()}>
      {children}
    </button>
  );
}

export function Input(props: ComponentPropsWithoutRef<"input">) {
  return <input {...props} className={`${styles.input} ${props.className || ""}`.trim()} />;
}

export function Textarea(props: ComponentPropsWithoutRef<"textarea">) {
  return <textarea {...props} className={`${styles.textarea} ${props.className || ""}`.trim()} />;
}

export function Select(props: ComponentPropsWithoutRef<"select">) {
  return <select {...props} className={`${styles.select} ${props.className || ""}`.trim()} />;
}

export function Card({
  title,
  action,
  children,
  className = "",
}: {
  title?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  // Card 支持可选标题区和右侧 action，正文始终包在 cardBody 中。
  return (
    <section className={`${styles.card} ${className}`.trim()}>
      {title || action ? (
        <div className={styles.cardHeader}>
          <div>{title}</div>
          <div>{action}</div>
        </div>
      ) : null}
      <div className={styles.cardBody}>{children}</div>
    </section>
  );
}

export function Badge({
  tone = "default",
  children,
}: {
  tone?: "default" | "danger" | "success";
  children: ReactNode;
}) {
  const toneClass =
    tone === "danger" ? styles.badgeDanger : tone === "success" ? styles.badgeSuccess : "";
  return <span className={`${styles.badge} ${toneClass}`.trim()}>{children}</span>;
}

export function InlineNotice({
  tone = "info",
  title,
  children,
}: {
  tone?: "info" | "error" | "success";
  title?: ReactNode;
  children: ReactNode;
}) {
  const toneClass =
    tone === "error"
      ? styles.noticeError
      : tone === "success"
        ? styles.noticeSuccess
        : styles.noticeInfo;
  return (
    <div className={`${styles.notice} ${toneClass}`.trim()}>
      {title ? <div className={styles.noticeTitle}>{title}</div> : null}
      <div>{children}</div>
    </div>
  );
}

export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow: ReactNode;
  title: ReactNode;
  description: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className={styles.pageHeader}>
      <div className={styles.pageHeaderMeta}>
        <div className={styles.eyebrow}>{eyebrow}</div>
        <h1 className={styles.pageTitle}>{title}</h1>
        <p className={styles.pageDescription}>{description}</p>
      </div>
      {actions ? <div>{actions}</div> : null}
    </div>
  );
}

export function Modal({
  open,
  onClose,
  children,
}: {
  open: boolean;
  onClose: () => void;
  children: ReactNode;
}) {
  // 点击遮罩关闭弹窗，点击面板内部阻止冒泡以保留表单交互。
  if (!open) return null;
  return (
    <div className={styles.modalOverlay} onClick={onClose} role="presentation">
      <div
        className={styles.modalPanel}
        role="dialog"
        aria-modal="true"
        onClick={(event) => event.stopPropagation()}
      >
        {children}
      </div>
    </div>
  );
}

export function EmptyState({ children }: { children: ReactNode }) {
  return <div className={styles.emptyState}>{children}</div>;
}

export function Skeleton({ children = "Loading..." }: { children?: ReactNode }) {
  return <div className={styles.skeleton}>{children}</div>;
}

export function SkeletonBlock({ lines = 3, title = false }: { lines?: number; title?: boolean }) {
  return (
    <div className={styles.skeletonBlock} aria-hidden="true">
      {title ? <div className={`${styles.skeletonLine} ${styles.skeletonTitle}`} /> : null}
      {Array.from({ length: lines }).map((_, index) => (
        <div key={`skeleton-line-${index}`} className={styles.skeletonLine} />
      ))}
    </div>
  );
}

export function SkeletonList({ rows = 5 }: { rows?: number }) {
  return (
    <div className={styles.skeletonList} aria-hidden="true">
      {Array.from({ length: rows }).map((_, index) => (
        <SkeletonBlock key={`skeleton-row-${index}`} lines={2} title={index === 0} />
      ))}
    </div>
  );
}

export function SkeletonTable({ rows = 6, columns = 4 }: { rows?: number; columns?: number }) {
  return (
    <div className={styles.skeletonTable} aria-hidden="true">
      {Array.from({ length: rows }).map((_, rowIndex) => (
        <div
          key={`skeleton-table-row-${rowIndex}`}
          className={styles.skeletonTableRow}
          style={{ "--skeleton-columns": columns } as CSSProperties}
        >
          {Array.from({ length: columns }).map((__, columnIndex) => (
            <div
              key={`skeleton-table-cell-${rowIndex}-${columnIndex}`}
              className={styles.skeletonLine}
            />
          ))}
        </div>
      ))}
    </div>
  );
}

export function PageSkeleton({ title = true }: { title?: boolean }) {
  // 页面级骨架屏提供 status 语义，供路由加载状态和测试定位。
  return (
    <div className={styles.pageSkeleton} role="status" aria-label="页面加载中">
      <SkeletonBlock lines={2} title={title} />
      <div className={styles.pageSkeletonGrid}>
        <SkeletonList rows={4} />
        <SkeletonBlock lines={8} title />
      </div>
    </div>
  );
}

export function ErrorState({
  title,
  description,
  actions,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className={styles.errorState} role="alert">
      <div>
        <h2>{title}</h2>
        {description ? <p>{description}</p> : null}
      </div>
      {actions ? <div className={styles.errorActions}>{actions}</div> : null}
    </div>
  );
}

export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  cancelLabel,
  onConfirm,
  onClose,
}: {
  open: boolean;
  title: ReactNode;
  description?: ReactNode;
  confirmLabel: ReactNode;
  cancelLabel: ReactNode;
  onConfirm: () => void;
  onClose: () => void;
}) {
  return (
    <Modal open={open} onClose={onClose}>
      <div className={styles.confirmDialog}>
        <div>
          <h2>{title}</h2>
          {description ? <p>{description}</p> : null}
        </div>
        <div className={styles.confirmActions}>
          <Button tone="secondary" onClick={onClose}>
            {cancelLabel}
          </Button>
          <Button tone="danger" onClick={onConfirm}>
            {confirmLabel}
          </Button>
        </div>
      </div>
    </Modal>
  );
}

export function Toolbar({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`${styles.toolbar} ${className}`.trim()}>{children}</div>;
}

export function VirtualList<T>({
  items,
  renderItem,
  getKey,
  estimateSize = 72,
  threshold = 100,
  maxHeight = 560,
  overscan = 6,
  className = "",
  itemClassName = "",
}: {
  items: T[];
  renderItem: (item: T, index: number) => ReactNode;
  getKey: (item: T, index: number) => string | number;
  estimateSize?: number;
  threshold?: number;
  maxHeight?: number | string;
  overscan?: number;
  className?: string;
  itemClassName?: string;
}) {
  // 小列表直接渲染，超过阈值后启用虚拟滚动降低 DOM 节点数量。
  const parentRef = useRef<HTMLDivElement | null>(null);
  const virtualizer = useVirtualizer({
    count: items.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => estimateSize,
    overscan,
  });

  if (items.length <= threshold) {
    return (
      <div className={`${styles.scrollViewport} ${className}`.trim()} style={{ maxHeight }}>
        {items.map((item, index) => (
          <div key={getKey(item, index)} className={itemClassName}>
            {renderItem(item, index)}
          </div>
        ))}
      </div>
    );
  }

  return (
    <div
      ref={parentRef}
      className={`${styles.virtualViewport} ${className}`.trim()}
      style={{ maxHeight }}
      data-testid="virtual-list"
    >
      <div className={styles.virtualSpacer} style={{ height: virtualizer.getTotalSize() }}>
        {virtualizer.getVirtualItems().map((virtualItem) => {
          const item = items[virtualItem.index];
          return (
            <div
              key={getKey(item, virtualItem.index)}
              ref={virtualizer.measureElement}
              data-index={virtualItem.index}
              className={`${styles.virtualItem} ${itemClassName}`.trim()}
              style={{ transform: `translateY(${virtualItem.start}px)` }}
            >
              {renderItem(item, virtualItem.index)}
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function DataTable({ columns, rows }: { columns: ReactNode[]; rows: ReactNode[][] }) {
  // 大表格使用 VirtualList 渲染行，小表格保持原生 table 语义。
  if (rows.length > 100) {
    return (
      <div className={styles.tableWrap} role="table">
        <div className={styles.virtualTableHeader} role="row">
          {columns.map((column, index) => (
            <div key={`header-${index}`} role="columnheader">
              {column}
            </div>
          ))}
        </div>
        <VirtualList
          items={rows}
          estimateSize={58}
          threshold={100}
          maxHeight={640}
          getKey={(_, index) => `row-${index}`}
          renderItem={(row, rowIndex) => (
            <div className={styles.virtualTableRow} role="row">
              {row.map((cell, cellIndex) => (
                <div key={`cell-${rowIndex}-${cellIndex}`} role="cell">
                  {cell}
                </div>
              ))}
            </div>
          )}
        />
      </div>
    );
  }

  return (
    <div className={styles.tableWrap}>
      <table className={styles.table}>
        <thead>
          <tr>
            {columns.map((column, index) => (
              <th key={`header-${index}`}>{column}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, rowIndex) => (
            <tr key={`row-${rowIndex}`}>
              {row.map((cell, cellIndex) => (
                <td key={`cell-${rowIndex}-${cellIndex}`}>{cell}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
