import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import styles from "./AdminGraphWorkspace.module.css";
import type { AdminGraphRelationship, GraphJob, GraphNode } from "../../../shared/api/contracts";
import {
  createGraphNode,
  createGraphRelationship,
  deleteGraphNode,
  deleteGraphRelationship,
  executeCypherQuery,
  getGraphEmbedStatus,
  getGraphLabels,
  getGraphNode,
  getGraphNodes,
  getGraphRelationshipTypes,
  getRecentGraphJobs,
  submitGraphEmbedMilvus,
  submitGraphExtractImport,
  updateGraphNode,
} from "../../../shared/api/graphClient";
import { formatDateTime, truncateText } from "../../../shared/lib/format";
import { reportFrontendError } from "../../../shared/lib/observability";
import { queryKeys } from "../../../shared/queries/queryKeys";
import { Badge, Button, Card, DataTable, InlineNotice, Input, Modal, Select, Textarea, VirtualList } from "../../../shared/ui/primitives";

const LIMIT_OPTIONS = [20, 50, 80, 100, 150];

function parseProperties(text: string): Record<string, unknown> {
  const trimmed = text.trim();
  if (!trimmed) return {};
  const value = JSON.parse(trimmed) as unknown;
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("属性必须是 JSON 对象。");
  }
  return value as Record<string, unknown>;
}

function stringifyProperties(value: Record<string, unknown> | undefined): string {
  return JSON.stringify(value || {}, null, 2);
}

function getNodeName(node: GraphNode): string {
  const props = node.properties || {};
  return String(node.name || props.name || props.title || node.id);
}

function renderPropertyRows(value: Record<string, unknown> | undefined): ReactNode {
  const entries = Object.entries(value || {});
  if (!entries.length) return <div className="muted">-</div>;
  return (
    <div className={styles.properties}>
      {entries.map(([key, propertyValue]) => (
        <div key={key} className={styles.propertyRow}>
          <strong>{key}</strong>
          <span>{typeof propertyValue === "string" ? propertyValue : JSON.stringify(propertyValue)}</span>
        </div>
      ))}
    </div>
  );
}

function renderRelationshipRows(relationships: AdminGraphRelationship[], onDelete: (relId: string | number) => void): ReactNode {
  if (!relationships.length) return <div className="muted">-</div>;
  return (
    <div className={styles.relationshipList}>
      {relationships.map((relationship, index) => {
        const relId = relationship.rel_id;
        const neighborName = relationship.source_name || relationship.target_name || "-";
        return (
          <div key={`${relId || index}`} className={styles.relationshipItem}>
            <div className={styles.relationshipMeta}>
              <strong>{relationship.rel_type}</strong>
              <span className="muted">{neighborName}</span>
            </div>
            <div className={styles.relationshipActions}>
              <div className={styles.mono}>{JSON.stringify(relationship.rel_props || {}, null, 2)}</div>
              {relId != null ? (
                <Button tone="ghost" onClick={() => onDelete(relId)}>
                  删除关系
                </Button>
              ) : null}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function getStatusTone(status: string): "default" | "danger" | "success" {
  if (status === "failed" || status === "cancelled") return "danger";
  if (status === "succeeded") return "success";
  return "default";
}

export default function AdminGraphWorkspace() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();

  const [label, setLabel] = useState("");
  const [keyword, setKeyword] = useState("");
  const [limit, setLimit] = useState(50);
  const [page, setPage] = useState(1);
  const [selectedNodeId, setSelectedNodeId] = useState<string | number | null>(null);
  const [nodeModal, setNodeModal] = useState<{ mode: "create" | "edit"; open: boolean }>({ mode: "create", open: false });
  const [nodeLabel, setNodeLabel] = useState("");
  const [nodePropertiesText, setNodePropertiesText] = useState('{\n  "name": ""\n}');
  const [relationshipOpen, setRelationshipOpen] = useState(false);
  const [sourceNodeId, setSourceNodeId] = useState("");
  const [targetNodeId, setTargetNodeId] = useState("");
  const [relationshipType, setRelationshipType] = useState("");
  const [relationshipPropertiesText, setRelationshipPropertiesText] = useState("{}");
  const [cypherOpen, setCypherOpen] = useState(false);
  const [cypher, setCypher] = useState("MATCH (n) RETURN n LIMIT 20");
  const [cypherRows, setCypherRows] = useState<Record<string, unknown>[]>([]);
  const [selectedImportFile, setSelectedImportFile] = useState<File | null>(null);
  const [embedLabels, setEmbedLabels] = useState("");
  const [embedBatchSize, setEmbedBatchSize] = useState("100");
  const [feedback, setFeedback] = useState("");

  const labelsQuery = useQuery({ queryKey: queryKeys.graph.labels, queryFn: getGraphLabels });
  const relationTypesQuery = useQuery({ queryKey: queryKeys.graph.relationTypes, queryFn: getGraphRelationshipTypes });
  const nodesQuery = useQuery({
    queryKey: queryKeys.graph.nodes(label, keyword, page, limit),
    queryFn: () => getGraphNodes({ label, keyword, limit, offset: (page - 1) * limit }),
  });
  const nodeDetailQuery = useQuery({
    queryKey: queryKeys.graph.node(selectedNodeId),
    queryFn: () => getGraphNode(selectedNodeId as string | number),
    enabled: selectedNodeId != null,
  });
  const jobsQuery = useQuery({
    queryKey: queryKeys.graph.jobs,
    queryFn: () => getRecentGraphJobs(10),
    refetchInterval: (query) =>
      (query.state.data as GraphJob[] | undefined)?.some((job) => job.status === "queued" || job.status === "running") ? 2500 : false,
  });
  const embedStatusQuery = useQuery({
    queryKey: queryKeys.graph.embedStatus,
    queryFn: getGraphEmbedStatus,
  });

  const createNodeMutation = useMutation({
    mutationFn: () => createGraphNode(nodeLabel.trim(), parseProperties(nodePropertiesText)),
    onSuccess: async (node) => {
      setFeedback(t("admin.graphWorkspace.createSuccess"));
      setNodeModal({ mode: "create", open: false });
      setSelectedNodeId(node.id);
      await queryClient.invalidateQueries({ queryKey: queryKeys.graph.nodes(label, keyword, page, limit) });
    },
    onError: (error) => {
      reportFrontendError(error, "graph_create_node");
      setFeedback(error instanceof Error ? error.message : String(error));
    },
  });

  const updateNodeMutation = useMutation({
    mutationFn: () => updateGraphNode(selectedNodeId as string | number, parseProperties(nodePropertiesText)),
    onSuccess: async () => {
      setFeedback(t("admin.graphWorkspace.updateSuccess"));
      setNodeModal({ mode: "edit", open: false });
      await queryClient.invalidateQueries({ queryKey: queryKeys.graph.nodes(label, keyword, page, limit) });
      await queryClient.invalidateQueries({ queryKey: queryKeys.graph.node(selectedNodeId) });
    },
    onError: (error) => {
      reportFrontendError(error, "graph_update_node");
      setFeedback(error instanceof Error ? error.message : String(error));
    },
  });

  const deleteNodeMutation = useMutation({
    mutationFn: (nodeId: string | number) => deleteGraphNode(nodeId),
    onSuccess: async () => {
      setFeedback(t("admin.graphWorkspace.deleteSuccess"));
      setSelectedNodeId(null);
      await queryClient.invalidateQueries({ queryKey: queryKeys.graph.nodes(label, keyword, page, limit) });
    },
    onError: (error) => {
      reportFrontendError(error, "graph_delete_node");
      setFeedback(error instanceof Error ? error.message : String(error));
    },
  });

  const createRelationshipMutation = useMutation({
    mutationFn: () =>
      createGraphRelationship(
        Number(sourceNodeId),
        Number(targetNodeId),
        relationshipType.trim(),
        parseProperties(relationshipPropertiesText),
      ),
    onSuccess: async () => {
      setFeedback(t("admin.graphWorkspace.relationshipSuccess"));
      setRelationshipOpen(false);
      await queryClient.invalidateQueries({ queryKey: queryKeys.graph.node(selectedNodeId) });
    },
    onError: (error) => {
      reportFrontendError(error, "graph_create_relationship");
      setFeedback(error instanceof Error ? error.message : String(error));
    },
  });

  const deleteRelationshipMutation = useMutation({
    mutationFn: (relId: string | number) => deleteGraphRelationship(relId),
    onSuccess: async () => {
      setFeedback(t("admin.graphWorkspace.deleteRelationshipSuccess"));
      await queryClient.invalidateQueries({ queryKey: queryKeys.graph.node(selectedNodeId) });
    },
    onError: (error) => {
      reportFrontendError(error, "graph_delete_relationship");
      setFeedback(error instanceof Error ? error.message : String(error));
    },
  });

  const cypherMutation = useMutation({
    mutationFn: () => executeCypherQuery(cypher),
    onSuccess: (rows) => setCypherRows(rows),
    onError: (error) => {
      reportFrontendError(error, "graph_cypher");
      setFeedback(error instanceof Error ? error.message : String(error));
    },
  });

  const importMutation = useMutation({
    mutationFn: () => submitGraphExtractImport(selectedImportFile as File),
    onSuccess: async () => {
      setFeedback("图谱导入任务已提交。");
      setSelectedImportFile(null);
      await queryClient.invalidateQueries({ queryKey: queryKeys.graph.jobs });
    },
    onError: (error) => {
      reportFrontendError(error, "graph_import_job");
      setFeedback(error instanceof Error ? error.message : String(error));
    },
  });

  const embedMutation = useMutation({
    mutationFn: () =>
      submitGraphEmbedMilvus(
        embedLabels
          .split(",")
          .map((item) => item.trim())
          .filter(Boolean),
        Number(embedBatchSize) || 100,
      ),
    onSuccess: async () => {
      setFeedback("图谱向量化任务已提交。");
      await queryClient.invalidateQueries({ queryKey: queryKeys.graph.jobs });
      await queryClient.invalidateQueries({ queryKey: queryKeys.graph.embedStatus });
    },
    onError: (error) => {
      reportFrontendError(error, "graph_embed_job");
      setFeedback(error instanceof Error ? error.message : String(error));
    },
  });

  const nodeRows =
    nodesQuery.data?.data.map((node) => [
      String(node.id),
      truncateText(getNodeName(node), 30),
      Array.isArray(node.labels) ? node.labels.join(", ") : "-",
      <div key={`actions-${node.id}`} className={styles.inlineActions}>
        <Button tone="secondary" onClick={() => setSelectedNodeId(node.id)}>
          {t("common.inspect")}
        </Button>
        <Button
          tone="ghost"
          onClick={() => {
            setSelectedNodeId(node.id);
            setNodeLabel((node.labels || [node.label || ""]).join(","));
            setNodePropertiesText(stringifyProperties(node.properties));
            setNodeModal({ mode: "edit", open: true });
          }}
        >
          {t("admin.graphActions.editNode")}
        </Button>
        <Button
          tone="danger"
          onClick={() => {
            if (window.confirm(t("admin.graphWorkspace.confirmDeleteNode"))) {
              void deleteNodeMutation.mutateAsync(node.id);
            }
          }}
        >
          {t("admin.graphActions.deleteNode")}
        </Button>
      </div>,
    ]) || [];

  const selectedNode = nodeDetailQuery.data?.node;
  const totalPages = Math.max(1, Math.ceil((nodesQuery.data?.total || 0) / limit));
  const embedStatusText = useMemo(() => JSON.stringify(embedStatusQuery.data || {}, null, 2), [embedStatusQuery.data]);
  const jobs = jobsQuery.data || [];
  const renderJob = (job: GraphJob) => (
    <div className={styles.jobCard}>
      <div className={styles.jobHeader}>
        <strong>{job.job_type || job.job_id}</strong>
        <Badge tone={getStatusTone(String(job.status))}>{String(job.status)}</Badge>
      </div>
      <div className="muted">{job.progress_text || job.message || "-"}</div>
      <div className={styles.jobMeta}>
        <span>{formatDateTime(job.created_at)}</span>
        <span>{job.stage || "-"}</span>
      </div>
    </div>
  );

  return (
    <div className={styles.workspace}>
      {feedback ? <InlineNotice tone="info">{feedback}</InlineNotice> : null}

      <div className={styles.grid}>
        <div className={styles.primaryColumn}>
          <Card
            title={t("admin.graphWorkspace.nodeList")}
            action={
              <div className={styles.inlineActions}>
                <Button
                  onClick={() => {
                    setNodeLabel("");
                    setNodePropertiesText('{\n  "name": ""\n}');
                    setNodeModal({ mode: "create", open: true });
                  }}
                >
                  {t("admin.graphActions.createNode")}
                </Button>
                <Button tone="secondary" onClick={() => setRelationshipOpen(true)}>
                  {t("admin.graphActions.createRelationship")}
                </Button>
                <Button tone="secondary" onClick={() => setCypherOpen(true)}>
                  {t("admin.graphActions.runCypher")}
                </Button>
              </div>
            }
          >
            <div className={styles.toolbar}>
              <Select
                value={label}
                onChange={(event) => {
                  setLabel(event.target.value);
                  setPage(1);
                }}
              >
                <option value="">{t("admin.graphWorkspace.labelFilter")}</option>
                {(labelsQuery.data || []).map((item) => (
                  <option key={item} value={item}>
                    {item}
                  </option>
                ))}
              </Select>
              <Input
                value={keyword}
                onChange={(event) => {
                  setKeyword(event.target.value);
                  setPage(1);
                }}
                placeholder={t("admin.graphWorkspace.searchPlaceholder")}
              />
              <Select
                value={limit}
                onChange={(event) => {
                  setLimit(Number(event.target.value));
                  setPage(1);
                }}
              >
                {LIMIT_OPTIONS.map((option) => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </Select>
              <Button tone="secondary" onClick={() => void nodesQuery.refetch()}>
                {t("common.refresh")}
              </Button>
            </div>
            <DataTable columns={["ID", "名称", "标签", t("admin.table.actions")]} rows={nodeRows} />
            <div className={styles.pagination}>
              <span className="muted">
                第 {page} / {totalPages} 页，共 {nodesQuery.data?.total || 0} 条
              </span>
              <div className={styles.inlineActions}>
                <Button tone="secondary" disabled={page <= 1} onClick={() => setPage((current) => Math.max(1, current - 1))}>
                  上一页
                </Button>
                <Button tone="secondary" disabled={page >= totalPages} onClick={() => setPage((current) => Math.min(totalPages, current + 1))}>
                  下一页
                </Button>
              </div>
            </div>
          </Card>
        </div>

        <div className={styles.secondaryColumn}>
          <Card title={t("admin.graphWorkspace.nodeDetail")}>
            {selectedNode ? (
              <div className={styles.detailStack}>
                <div className={styles.propertyRow}>
                  <strong>ID</strong>
                  <span>{String(selectedNode.id)}</span>
                </div>
                <div className={styles.propertyRow}>
                  <strong>名称</strong>
                  <span>{getNodeName(selectedNode)}</span>
                </div>
                <div className={styles.propertyRow}>
                  <strong>标签</strong>
                  <span>{(selectedNode.labels || []).join(", ") || "-"}</span>
                </div>
                <div>
                  <strong>{t("graph.entityProperties")}</strong>
                  {renderPropertyRows(selectedNode.properties)}
                </div>
                <div>
                  <strong>{t("graph.entityIncoming")}</strong>
                  {renderRelationshipRows(nodeDetailQuery.data?.relationships.incoming || [], (relId) => {
                    if (window.confirm(t("admin.graphWorkspace.confirmDeleteRelationship"))) {
                      void deleteRelationshipMutation.mutateAsync(relId);
                    }
                  })}
                </div>
                <div>
                  <strong>{t("graph.entityOutgoing")}</strong>
                  {renderRelationshipRows(nodeDetailQuery.data?.relationships.outgoing || [], (relId) => {
                    if (window.confirm(t("admin.graphWorkspace.confirmDeleteRelationship"))) {
                      void deleteRelationshipMutation.mutateAsync(relId);
                    }
                  })}
                </div>
              </div>
            ) : (
              <div className="muted">{t("admin.graphWorkspace.noNodeSelected")}</div>
            )}
          </Card>

          <Card title={t("admin.graphWorkspace.importFile")}>
            <div className={styles.detailStack}>
              <Input type="file" onChange={(event) => setSelectedImportFile(event.target.files?.[0] || null)} />
              {selectedImportFile ? <div className="muted">{selectedImportFile.name}</div> : null}
              <Button onClick={() => selectedImportFile && void importMutation.mutateAsync()} disabled={!selectedImportFile}>
                {t("admin.graphActions.importGraph")}
              </Button>
            </div>
          </Card>

          <Card title={t("admin.graphActions.embedGraph")}>
            <div className={styles.detailStack}>
              <Input value={embedLabels} onChange={(event) => setEmbedLabels(event.target.value)} placeholder="Disease,Drug" />
              <Input value={embedBatchSize} onChange={(event) => setEmbedBatchSize(event.target.value)} />
              <Button onClick={() => void embedMutation.mutateAsync()}>{t("admin.graphActions.embedGraph")}</Button>
              <div className={styles.mono}>{embedStatusText}</div>
            </div>
          </Card>

          <Card title={t("admin.graphWorkspace.jobs")}>
            <div>
              {jobs.length ? (
                <VirtualList
                  items={jobs}
                  className={styles.jobsList}
                  estimateSize={112}
                  threshold={100}
                  maxHeight={560}
                  getKey={(job) => job.job_id}
                  renderItem={renderJob}
                />
              ) : (
                <div className="muted">{t("admin.graphWorkspace.noJobs")}</div>
              )}
            </div>
          </Card>
        </div>
      </div>

      <Modal open={nodeModal.open} onClose={() => setNodeModal((current) => ({ ...current, open: false }))}>
        <div className={styles.modalStack}>
          <h2>{t(nodeModal.mode === "create" ? "admin.graphActions.createNode" : "admin.graphActions.editNode")}</h2>
          {nodeModal.mode === "create" ? (
            <Input value={nodeLabel} onChange={(event) => setNodeLabel(event.target.value)} placeholder={t("admin.graphWorkspace.nodeLabel")} />
          ) : null}
          <Textarea value={nodePropertiesText} onChange={(event) => setNodePropertiesText(event.target.value)} />
          <div className="muted">{t("admin.graphWorkspace.jsonHint")}</div>
          <div className={styles.inlineActions}>
            <Button tone="secondary" onClick={() => setNodeModal((current) => ({ ...current, open: false }))}>
              {t("common.cancel")}
            </Button>
            <Button onClick={() => void (nodeModal.mode === "create" ? createNodeMutation.mutateAsync() : updateNodeMutation.mutateAsync())}>
              {t("common.save")}
            </Button>
          </div>
        </div>
      </Modal>

      <Modal open={relationshipOpen} onClose={() => setRelationshipOpen(false)}>
        <div className={styles.modalStack}>
          <h2>{t("admin.graphActions.createRelationship")}</h2>
          <Input value={sourceNodeId} onChange={(event) => setSourceNodeId(event.target.value)} placeholder={t("admin.graphWorkspace.sourceNodeId")} />
          <Input value={targetNodeId} onChange={(event) => setTargetNodeId(event.target.value)} placeholder={t("admin.graphWorkspace.targetNodeId")} />
          <Input
            value={relationshipType}
            onChange={(event) => setRelationshipType(event.target.value)}
            placeholder={t("admin.graphWorkspace.relationshipType")}
            list="relationship-types"
          />
          <datalist id="relationship-types">
            {(relationTypesQuery.data || []).map((item) => (
              <option key={item} value={item} />
            ))}
          </datalist>
          <Textarea value={relationshipPropertiesText} onChange={(event) => setRelationshipPropertiesText(event.target.value)} />
          <div className={styles.inlineActions}>
            <Button tone="secondary" onClick={() => setRelationshipOpen(false)}>
              {t("common.cancel")}
            </Button>
            <Button onClick={() => void createRelationshipMutation.mutateAsync()}>{t("common.save")}</Button>
          </div>
        </div>
      </Modal>

      <Modal open={cypherOpen} onClose={() => setCypherOpen(false)}>
        <div className={styles.modalStack}>
          <h2>{t("admin.graphActions.runCypher")}</h2>
          <Textarea value={cypher} onChange={(event) => setCypher(event.target.value)} />
          <div className={styles.inlineActions}>
            <Button tone="secondary" onClick={() => setCypherOpen(false)}>
              {t("common.cancel")}
            </Button>
            <Button onClick={() => void cypherMutation.mutateAsync()}>{t("common.submit")}</Button>
          </div>
          <Card title={t("admin.graphWorkspace.cypherResult")}>
            <div className={styles.mono}>{JSON.stringify(cypherRows, null, 2)}</div>
          </Card>
        </div>
      </Modal>
    </div>
  );
}
