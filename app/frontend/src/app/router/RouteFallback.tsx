import styles from "./RouteFallback.module.css";
import { PageSkeleton } from "../../shared/ui/primitives";

// 懒加载页面切换期间展示统一骨架屏，避免页面级 loading 文案分散。
export default function RouteFallback() {
  return (
    <div className={styles.fallback}>
      <PageSkeleton />
    </div>
  );
}
