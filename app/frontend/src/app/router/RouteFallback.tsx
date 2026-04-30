import styles from "./RouteFallback.module.css";
import { PageSkeleton } from "../../shared/ui/primitives";

export default function RouteFallback() {
  return (
    <div className={styles.fallback}>
      <PageSkeleton />
    </div>
  );
}
