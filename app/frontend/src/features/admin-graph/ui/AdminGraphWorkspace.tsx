import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import styles from "./AdminGraphWorkspace.module.css";
import {
  getGraphNodeName,
  GraphJobCard,
  GraphPropertyRows,
  GraphRelationshipRows,
  parseGraphProperties,
  stringifyGraphProperties,
} from "./graph_workspace_helpers";
import {
  CypherQueryModal,
  NodeEditorModal,
  RelationshipEditorModal,
} from "./graph_workspace_modals";
import KnowledgeGraphCanvas from "../../../entities/graph/ui/KnowledgeGraphCanvas";
import type { GraphJob } from "../../../shared/api/contracts";
import {
  createGraphNode,
  createGraphRelationship,
  deleteGraphNode,
  deleteGraphRelationship,
  executeCypherQuery,
  fetchGraph,
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
import { truncateText } from "../../../shared/lib/format";
import { reportFrontendError } from "../../../shared/lib/observability";
import { queryKeys } from "../../../shared/queries/queryKeys";
import {
  Badge,
  Button,
  Card,
  DataTable,
  InlineNotice,
  Input,
  Select,
  VirtualList,
} from "../../../shared/ui/primitives";

const LIMIT_OPTIONS = [20, 50, 80, 100, 150];

export default function AdminGraphWorkspace() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();

  const [label, setLabel] = useState("");
  const [keyword, setKeyword] = useState("");
  const [limit, setLimit] = useState(50);
  const [page, setPage] = useState(1);
  const [selectedNodeId, setSelectedNodeId] = useState<string | number | null>(null);
  const [selectedGraphNodeId, setSelectedGraphNodeId] = useState("");
  const [nodeModal, setNodeModal] = useState<{ mode: "create" | "edit"; open: boolean }>({
    mode: "create",
    open: false,
  });
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
  const relationTypesQuery = useQuery({
    queryKey: queryKeys.graph.relationTypes,
    queryFn: getGraphRelationshipTypes,
  });
  const nodesQuery = useQuery({
    queryKey: queryKeys.graph.nodes(label, keyword, page, limit),
    queryFn: () => getGraphNodes({ label, keyword, limit, offset: (page - 1) * limit }),
  });
  const graphPreviewQuery = useQuery({
    queryKey: queryKeys.graph.visualization(keyword, limit),
    queryFn: () => fetchGraph(keyword, limit),
  });
  const nodeDetailQuery = useQuery({
    queryKey: queryKeys.graph.node(selectedNodeId),
    queryFn: () => getGraphNode(selectedNodeId as string | number),
    enabled: selectedNodeId != null,
  });
  const jobsQuery = useQuery({
    queryKey: queryKeys.graph.jobs,
    queryFn: () => getRecentGraphJobs(10),
    // 图谱导入/向量化任务运行中时轮询，避免后台任务完成后页面停留旧状态。
    refetchInterval: (query) =>
      (query.state.data as GraphJob[] | undefined)?.some(
        (job) => job.status === "queued" || job.status === "running",
      )
        ? 2500
        : false,
  });
  const embedStatusQuery = useQuery({
    queryKey: queryKeys.graph.embedStatus,
    queryFn: getGraphEmbedStatus,
  });

  const createNodeMutation = useMutation({
    mutationFn: () => createGraphNode(nodeLabel.trim(), parseGraphProperties(nodePropertiesText)),
    onSuccess: async (node) => {
      // 创建后选中新节点并刷新当前分页，便于继续编辑或检查关系。
      setFeedback(t("admin.graphWorkspace.createSuccess"));
      setNodeModal({ mode: "create", open: false });
      setSelectedNodeId(node.id);
      await queryClient.invalidateQueries({
        queryKey: queryKeys.graph.nodes(label, keyword, page, limit),
      });
      await queryClient.invalidateQueries({
        queryKey: queryKeys.graph.visualization(keyword, limit),
      });
    },
    onError: (error) => {
      reportFrontendError(error, "graph_create_node");
      setFeedback(error instanceof Error ? error.message : String(error));
    },
  });

  const updateNodeMutation = useMutation({
    mutationFn: () =>
      updateGraphNode(selectedNodeId as string | number, parseGraphProperties(nodePropertiesText)),
    onSuccess: async () => {
      setFeedback(t("admin.graphWorkspace.updateSuccess"));
      setNodeModal({ mode: "edit", open: false });
      await queryClient.invalidateQueries({
        queryKey: queryKeys.graph.nodes(label, keyword, page, limit),
      });
      await queryClient.invalidateQueries({ queryKey: queryKeys.graph.node(selectedNodeId) });
      await queryClient.invalidateQueries({
        queryKey: queryKeys.graph.visualization(keyword, limit),
      });
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
      setSelectedGraphNodeId("");
      await queryClient.invalidateQueries({
        queryKey: queryKeys.graph.nodes(label, keyword, page, limit),
      });
      await queryClient.invalidateQueries({
        queryKey: queryKeys.graph.visualization(keyword, limit),
      });
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
        parseGraphProperties(relationshipPropertiesText),
      ),
    onSuccess: async () => {
      setFeedback(t("admin.graphWorkspace.relationshipSuccess"));
      setRelationshipOpen(false);
      await queryClient.invalidateQueries({ queryKey: queryKeys.graph.node(selectedNodeId) });
      await queryClient.invalidateQueries({
        queryKey: queryKeys.graph.visualization(keyword, limit),
      });
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
      await queryClient.invalidateQueries({
        queryKey: queryKeys.graph.visualization(keyword, limit),
      });
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
    // DataTable 接收二维 ReactNode 数组，操作列内联绑定查看、编辑、删除。
    nodesQuery.data?.data.map((node) => [
      String(node.id),
      truncateText(getGraphNodeName(node), 30),
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
            setNodePropertiesText(stringifyGraphProperties(node.properties));
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
  // 派生分页、向量状态和任务列表展示数据，避免 JSX 中重复格式化。
  const graphPreview = graphPreviewQuery.data ?? { nodes: [], links: [] };
  const totalPages = Math.max(1, Math.ceil((nodesQuery.data?.total || 0) / limit));
  const embedStatusText = useMemo(
    () => JSON.stringify(embedStatusQuery.data || {}, null, 2),
    [embedStatusQuery.data],
  );
  const jobs = jobsQuery.data || [];

  return (
    <div className={styles.workspace}>
      {feedback ? <InlineNotice tone="info">{feedback}</InlineNotice> : null}

      <div className={styles.grid}>
        <div className={styles.primaryColumn}>
          <Card
            title={t("admin.graphWorkspace.graphPreview")}
            action={
              <div className={`${styles.inlineActions} ${styles.graphPreviewActions}`}>
                <Badge>
                  {t("graph.nodesLoaded", {
                    nodes: graphPreview.nodes.length,
                    links: graphPreview.links.length,
                  })}
                </Badge>
                <Button tone="secondary" onClick={() => void graphPreviewQuery.refetch()}>
                  {t("common.refresh")}
                </Button>
              </div>
            }
          >
            {graphPreviewQuery.error ? (
              <InlineNotice tone="error">
                {graphPreviewQuery.error instanceof Error
                  ? graphPreviewQuery.error.message
                  : String(graphPreviewQuery.error)}
              </InlineNotice>
            ) : null}
            <KnowledgeGraphCanvas
              graph={graphPreview}
              className={styles.graphPreviewCanvas}
              selectedNodeId={selectedGraphNodeId}
              onNodeSelect={(node) => {
                setSelectedGraphNodeId(node ? String(node.id) : "");
                setSelectedNodeId(node ? node.id : null);
              }}
              onOpenDetail={(entityId) => setSelectedNodeId(entityId)}
              emptyText={t("graph.noGraph")}
            />
          </Card>

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
                <Button
                  tone="secondary"
                  disabled={page <= 1}
                  onClick={() => setPage((current) => Math.max(1, current - 1))}
                >
                  上一页
                </Button>
                <Button
                  tone="secondary"
                  disabled={page >= totalPages}
                  onClick={() => setPage((current) => Math.min(totalPages, current + 1))}
                >
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
                  <span>{getGraphNodeName(selectedNode)}</span>
                </div>
                <div className={styles.propertyRow}>
                  <strong>标签</strong>
                  <span>{(selectedNode.labels || []).join(", ") || "-"}</span>
                </div>
                <div>
                  <strong>{t("graph.entityProperties")}</strong>
                  <GraphPropertyRows value={selectedNode.properties} />
                </div>
                <div>
                  <strong>{t("graph.entityIncoming")}</strong>
                  <GraphRelationshipRows
                    relationships={nodeDetailQuery.data?.relationships.incoming || []}
                    onDelete={(relId) => {
                      if (window.confirm(t("admin.graphWorkspace.confirmDeleteRelationship"))) {
                        void deleteRelationshipMutation.mutateAsync(relId);
                      }
                    }}
                  />
                </div>
                <div>
                  <strong>{t("graph.entityOutgoing")}</strong>
                  <GraphRelationshipRows
                    relationships={nodeDetailQuery.data?.relationships.outgoing || []}
                    onDelete={(relId) => {
                      if (window.confirm(t("admin.graphWorkspace.confirmDeleteRelationship"))) {
                        void deleteRelationshipMutation.mutateAsync(relId);
                      }
                    }}
                  />
                </div>
              </div>
            ) : (
              <div className="muted">{t("admin.graphWorkspace.noNodeSelected")}</div>
            )}
          </Card>

          <Card title={t("admin.graphWorkspace.importFile")}>
            <div className={styles.detailStack}>
              <Input
                type="file"
                onChange={(event) => setSelectedImportFile(event.target.files?.[0] || null)}
              />
              {selectedImportFile ? <div className="muted">{selectedImportFile.name}</div> : null}
              <Button
                onClick={() => selectedImportFile && void importMutation.mutateAsync()}
                disabled={!selectedImportFile}
              >
                {t("admin.graphActions.importGraph")}
              </Button>
            </div>
          </Card>

          <Card title={t("admin.graphActions.embedGraph")}>
            <div className={styles.detailStack}>
              <Input
                value={embedLabels}
                onChange={(event) => setEmbedLabels(event.target.value)}
                placeholder="Disease,Drug"
              />
              <Input
                value={embedBatchSize}
                onChange={(event) => setEmbedBatchSize(event.target.value)}
              />
              <Button onClick={() => void embedMutation.mutateAsync()}>
                {t("admin.graphActions.embedGraph")}
              </Button>
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
                  renderItem={(job) => <GraphJobCard job={job} />}
                />
              ) : (
                <div className="muted">{t("admin.graphWorkspace.noJobs")}</div>
              )}
            </div>
          </Card>
        </div>
      </div>

      <NodeEditorModal
        open={nodeModal.open}
        mode={nodeModal.mode}
        label={nodeLabel}
        propertiesText={nodePropertiesText}
        t={t}
        onClose={() => setNodeModal((current) => ({ ...current, open: false }))}
        onLabelChange={setNodeLabel}
        onPropertiesChange={setNodePropertiesText}
        onSubmit={() =>
          void (nodeModal.mode === "create"
            ? createNodeMutation.mutateAsync()
            : updateNodeMutation.mutateAsync())
        }
      />
      <RelationshipEditorModal
        open={relationshipOpen}
        sourceNodeId={sourceNodeId}
        targetNodeId={targetNodeId}
        relationshipType={relationshipType}
        propertiesText={relationshipPropertiesText}
        relationshipTypes={relationTypesQuery.data || []}
        t={t}
        onClose={() => setRelationshipOpen(false)}
        onSourceNodeIdChange={setSourceNodeId}
        onTargetNodeIdChange={setTargetNodeId}
        onRelationshipTypeChange={setRelationshipType}
        onPropertiesChange={setRelationshipPropertiesText}
        onSubmit={() => void createRelationshipMutation.mutateAsync()}
      />
      <CypherQueryModal
        open={cypherOpen}
        query={cypher}
        rows={cypherRows}
        t={t}
        onClose={() => setCypherOpen(false)}
        onQueryChange={setCypher}
        onSubmit={() => void cypherMutation.mutateAsync()}
      />
    </div>
  );
}
