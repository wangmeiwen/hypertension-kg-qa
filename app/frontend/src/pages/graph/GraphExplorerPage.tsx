import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import styles from "./GraphExplorerPage.module.css";
import KnowledgeGraphCanvas, { DEFAULT_GRAPH_LIMIT, GRAPH_LIMIT_OPTIONS } from "../../entities/graph/ui/KnowledgeGraphCanvas";
import { fetchGraph, getEntityDetail } from "../../shared/api/graphClient";
import { queryKeys } from "../../shared/queries/queryKeys";
import { Badge, Button, Card, InlineNotice, Input, Modal, PageHeader, Select } from "../../shared/ui/primitives";

function renderPropertyList(properties: Record<string, unknown> | undefined) {
  const entries = Object.entries(properties || {});
  if (!entries.length) {
    return <div className="muted">-</div>;
  }
  return (
    <div className={styles.propertyList}>
      {entries.map(([key, value]) => (
        <div key={key} className={styles.propertyRow}>
          <strong>{key}</strong>
          <span>{typeof value === "string" ? value : JSON.stringify(value)}</span>
        </div>
      ))}
    </div>
  );
}

function renderRelationshipList(
  items: Array<{
    rel_id?: string | number;
    rel_type: string;
    rel_props?: Record<string, unknown>;
    source_name?: string;
    target_name?: string;
  }>,
  t: (key: string) => string,
) {
  if (!items.length) return <div className="muted">{t("graph.noRelationships")}</div>;
  return (
    <div className={styles.relationshipList}>
      {items.map((item, index) => (
        <div key={`${item.rel_id || index}`} className={styles.relationshipCard}>
          <div className={styles.propertyRow}>
            <strong>{t("graph.relationshipType")}</strong>
            <span>{item.rel_type}</span>
          </div>
          <div className={styles.propertyRow}>
            <strong>{t("graph.relatedNode")}</strong>
            <span>{item.source_name || item.target_name || "-"}</span>
          </div>
          <div>
            <strong>{t("graph.relationshipProperties")}</strong>
            {renderPropertyList(item.rel_props)}
          </div>
        </div>
      ))}
    </div>
  );
}

export default function GraphExplorerPage() {
  const { t } = useTranslation();
  const [keyword, setKeyword] = useState("");
  const [submittedKeyword, setSubmittedKeyword] = useState("");
  const [limit, setLimit] = useState(DEFAULT_GRAPH_LIMIT);
  const [selectedEntityId, setSelectedEntityId] = useState<string | number | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState("");

  const graphQuery = useQuery({
    queryKey: queryKeys.graph.visualization(submittedKeyword, limit),
    queryFn: () => fetchGraph(submittedKeyword, limit),
  });

  const entityQuery = useQuery({
    queryKey: queryKeys.graph.entity(selectedEntityId),
    queryFn: () => getEntityDetail(selectedEntityId as string | number),
    enabled: selectedEntityId != null,
  });

  const graph = graphQuery.data || { nodes: [], links: [] };

  return (
    <div className={styles.layout}>
      <PageHeader
        eyebrow={t("graph.eyebrow")}
        title={t("graph.title")}
        description={t("graph.description")}
        actions={<Badge>{t("graph.nodesLoaded", { nodes: graph.nodes.length, links: graph.links.length })}</Badge>}
      />

      {graphQuery.error ? <InlineNotice tone="error">{graphQuery.error instanceof Error ? graphQuery.error.message : String(graphQuery.error)}</InlineNotice> : null}

      <Card>
        <div className={styles.toolbar}>
          <Input
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
            placeholder={t("graph.keywordPlaceholder")}
          />
          <Select value={limit} onChange={(event) => setLimit(Number(event.target.value))}>
            {GRAPH_LIMIT_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </Select>
          <Button onClick={() => setSubmittedKeyword(keyword.trim())}>{t("common.search")}</Button>
          <Button tone="secondary" onClick={() => void graphQuery.refetch()}>
            {t("common.refresh")}
          </Button>
        </div>
      </Card>

      <Card className={styles.canvasCard}>
        <KnowledgeGraphCanvas
          graph={graph}
          selectedNodeId={selectedNodeId}
          onNodeSelect={(node) => setSelectedNodeId(node ? String(node.id) : "")}
          onOpenDetail={(entityId) => setSelectedEntityId(entityId)}
          emptyText={t("graph.noGraph")}
        />
      </Card>

      <Modal open={selectedEntityId != null} onClose={() => setSelectedEntityId(null)}>
        {entityQuery.data ? (
          <div className={styles.entityMeta}>
            <h2 style={{ margin: 0 }}>{t("graph.entityDetail")}</h2>
            <Badge>{entityQuery.data.entity.labels.join(", ") || t("graph.entityLabelFallback")}</Badge>
            <div className={styles.propertyRow}>
              <strong>{t("graph.entityId")}</strong>
              <span>{String(entityQuery.data.entity.id)}</span>
            </div>
            <div className={styles.propertyRow}>
              <strong>{t("graph.entityInfo")}</strong>
              <span>{entityQuery.data.entity.name}</span>
            </div>
            <div>
              <strong>{t("graph.entityProperties")}</strong>
              {renderPropertyList(entityQuery.data.entity.properties)}
            </div>
            <div className={styles.detailGrid}>
              <div>
                <strong>{t("graph.entityIncoming")}</strong>
                {renderRelationshipList(entityQuery.data.relationships.incoming, t)}
              </div>
              <div>
                <strong>{t("graph.entityOutgoing")}</strong>
                {renderRelationshipList(entityQuery.data.relationships.outgoing, t)}
              </div>
            </div>
          </div>
        ) : (
          <div>{t("common.loading")}</div>
        )}
      </Modal>
    </div>
  );
}
