import styles from "./AdminGraphWorkspace.module.css";
import type { AdminGraphRelationship, GraphJob, GraphNode } from "../../../shared/api/contracts";
import { formatDateTime } from "../../../shared/lib/format";
import { Badge, Button } from "../../../shared/ui/primitives";

export const parseGraphProperties = (text: string): Record<string, unknown> => {
  const trimmed = text.trim();
  if (!trimmed) return {};
  const value = JSON.parse(trimmed) as unknown;
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("属性必须是 JSON 对象。");
  }
  return value as Record<string, unknown>;
};

export const stringifyGraphProperties = (value: Record<string, unknown> | undefined): string =>
  JSON.stringify(value ?? {}, null, 2);

export const getGraphNodeName = (node: GraphNode): string => {
  const properties = node.properties ?? {};
  return String(node.name ?? properties.name ?? properties.title ?? node.id);
};

export const GraphPropertyRows = ({ value }: { value: Record<string, unknown> | undefined }) => {
  const entries = Object.entries(value ?? {});
  if (!entries.length) return <div className="muted">-</div>;
  return (
    <div className={styles.properties}>
      {entries.map(([key, propertyValue]) => (
        <div key={key} className={styles.propertyRow}>
          <strong>{key}</strong>
          <span>
            {typeof propertyValue === "string" ? propertyValue : JSON.stringify(propertyValue)}
          </span>
        </div>
      ))}
    </div>
  );
};

interface GraphRelationshipRowsProps {
  relationships: AdminGraphRelationship[];
  onDelete: (relationshipId: string | number) => void;
}

export const GraphRelationshipRows = ({ relationships, onDelete }: GraphRelationshipRowsProps) => {
  if (!relationships.length) return <div className="muted">-</div>;
  return (
    <div className={styles.relationshipList}>
      {relationships.map((relationship, index) => {
        const relationshipId = relationship.rel_id;
        const neighborName = relationship.source_name ?? relationship.target_name ?? "-";
        return (
          <div key={`${relationshipId ?? index}`} className={styles.relationshipItem}>
            <div className={styles.relationshipMeta}>
              <strong>{relationship.rel_type}</strong>
              <span className="muted">{neighborName}</span>
            </div>
            <div className={styles.relationshipActions}>
              <div className={styles.mono}>
                {JSON.stringify(relationship.rel_props ?? {}, null, 2)}
              </div>
              {relationshipId != null ? (
                <Button tone="ghost" onClick={() => onDelete(relationshipId)}>
                  删除关系
                </Button>
              ) : null}
            </div>
          </div>
        );
      })}
    </div>
  );
};

const getStatusTone = (status: string): "default" | "danger" | "success" => {
  if (status === "failed" || status === "cancelled") return "danger";
  return status === "succeeded" ? "success" : "default";
};

export const GraphJobCard = ({ job }: { job: GraphJob }) => (
  <div className={styles.jobCard}>
    <div className={styles.jobHeader}>
      <strong>{job.job_type ?? job.job_id}</strong>
      <Badge tone={getStatusTone(String(job.status))}>{String(job.status)}</Badge>
    </div>
    <div className="muted">{job.progress_text ?? job.message ?? "-"}</div>
    <div className={styles.jobMeta}>
      <span>{formatDateTime(job.created_at)}</span>
      <span>{job.stage ?? "-"}</span>
    </div>
  </div>
);
