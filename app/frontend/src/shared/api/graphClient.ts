import { z } from "zod";

import {
  adminGraphNodeDetailSchema,
  cypherResultSchema,
  documentStatsSchema,
  graphJobSchema,
  graphLabelListSchema,
  graphNodeSchema,
  graphNodesResponseSchema,
  graphVisualizationSchema,
  graphEntityDetailSchema,
  type AdminGraphNodeDetail,
  type GraphEntityDetail,
  type GraphJob,
  type GraphNode,
  type GraphVisualization,
} from "./contracts";
import { fetchJson } from "./http";

export async function fetchGraph(keyword = "", limit = 120): Promise<GraphVisualization> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (keyword.trim()) params.set("keyword", keyword.trim());
  const raw = await fetchJson(
    `/graph/visualization?${params.toString()}`,
    z.object({
      nodes: graphVisualizationSchema.shape.nodes,
      links: graphVisualizationSchema.shape.links,
    }),
    {},
    { fallbackMessage: "加载图谱失败" },
  );
  return { nodes: raw.nodes, links: raw.links };
}

export async function getEntityDetail(entityId: string | number): Promise<GraphEntityDetail> {
  return fetchJson(`/graph/entity/${entityId}`, graphEntityDetailSchema, {}, { fallbackMessage: "加载实体详情失败" });
}

export async function getGraphLabels(): Promise<string[]> {
  const result = await fetchJson("/admin/graph/labels", graphLabelListSchema, {}, { auth: true, fallbackMessage: "加载标签失败" });
  return result.data;
}

export async function getGraphRelationshipTypes(): Promise<string[]> {
  const result = await fetchJson(
    "/admin/graph/relationship-types",
    graphLabelListSchema,
    {},
    { auth: true, fallbackMessage: "加载关系类型失败" },
  );
  return result.data;
}

export async function getGraphNodes(params: { label?: string; keyword?: string; limit?: number; offset?: number }) {
  const search = new URLSearchParams();
  if (params.label) search.set("label", params.label);
  if (params.keyword) search.set("keyword", params.keyword);
  search.set("limit", String(params.limit ?? 20));
  search.set("offset", String(params.offset ?? 0));
  return fetchJson(`/admin/graph/nodes?${search.toString()}`, graphNodesResponseSchema, {}, { auth: true, fallbackMessage: "加载图谱节点失败" });
}

export async function getGraphNode(nodeId: string | number): Promise<AdminGraphNodeDetail> {
  return fetchJson(`/admin/graph/nodes/${nodeId}`, adminGraphNodeDetailSchema, {}, { auth: true, fallbackMessage: "加载节点详情失败" });
}

export async function createGraphNode(label: string, properties: Record<string, unknown>): Promise<GraphNode> {
  const result = await fetchJson(
    "/admin/graph/nodes",
    graphNodeSchema,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ label, properties }),
    },
    { auth: true, fallbackMessage: "创建节点失败" },
  );
  return result;
}

export async function updateGraphNode(nodeId: string | number, properties: Record<string, unknown>): Promise<void> {
  await fetchJson(
    `/admin/graph/nodes/${nodeId}`,
    z.record(z.string(), z.unknown()),
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ properties }),
    },
    { auth: true, fallbackMessage: "更新节点失败" },
  );
}

export async function deleteGraphNode(nodeId: string | number): Promise<void> {
  await fetchJson(
    `/admin/graph/nodes/${nodeId}`,
    z.record(z.string(), z.unknown()),
    { method: "DELETE" },
    { auth: true, fallbackMessage: "删除节点失败" },
  );
}

export async function createGraphRelationship(
  sourceId: string | number,
  targetId: string | number,
  relType: string,
  properties: Record<string, unknown> = {},
): Promise<Record<string, unknown>> {
  return fetchJson(
    "/admin/graph/relationships",
    z.record(z.string(), z.unknown()),
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source_id: sourceId, target_id: targetId, rel_type: relType, properties }),
    },
    { auth: true, fallbackMessage: "创建关系失败" },
  );
}

export async function deleteGraphRelationship(relId: string | number): Promise<void> {
  await fetchJson(
    `/admin/graph/relationships/${relId}`,
    z.record(z.string(), z.unknown()),
    { method: "DELETE" },
    { auth: true, fallbackMessage: "删除关系失败" },
  );
}

export async function executeCypherQuery(query: string): Promise<Record<string, unknown>[]> {
  return fetchJson(
    "/admin/graph/execute-cypher",
    cypherResultSchema,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query }),
    },
    { auth: true, fallbackMessage: "执行 Cypher 失败" },
  );
}

export async function submitGraphExtractImport(file: File): Promise<GraphJob> {
  const formData = new FormData();
  formData.append("file", file);
  return fetchJson("/admin/graph/jobs/extract-import", graphJobSchema, { method: "POST", body: formData }, { auth: true, fallbackMessage: "提交图谱导入任务失败" });
}

export async function getGraphJobStatus(jobId: string): Promise<GraphJob> {
  return fetchJson(`/admin/graph/jobs/${jobId}`, graphJobSchema, {}, { auth: true, fallbackMessage: "加载图谱任务失败" });
}

export async function getRecentGraphJobs(limit = 10): Promise<GraphJob[]> {
  return fetchJson(`/admin/graph/jobs?limit=${limit}`, z.array(graphJobSchema), {}, { auth: true, fallbackMessage: "加载最近图谱任务失败" });
}

export async function submitGraphEmbedMilvus(labels: string[] | null = null, batchSize = 100): Promise<GraphJob> {
  const payload: Record<string, unknown> = { batch_size: batchSize };
  if (labels && labels.length > 0) payload.labels = labels;
  return fetchJson(
    "/admin/graph/jobs/embed-milvus",
    graphJobSchema,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
    { auth: true, fallbackMessage: "提交图谱向量化任务失败" },
  );
}

export async function getGraphEmbedStatus(): Promise<Record<string, unknown>> {
  return fetchJson("/admin/graph/embed-status", z.record(z.string(), z.unknown()), {}, { auth: true, fallbackMessage: "加载图谱向量化状态失败" });
}

export async function submitTextEmbeddingJob(file: File): Promise<GraphJob> {
  const formData = new FormData();
  formData.append("file", file);
  return fetchJson("/admin/text-embedding/jobs/embed", graphJobSchema, { method: "POST", body: formData }, { auth: true, fallbackMessage: "提交文档向量化任务失败" });
}

export async function getTextEmbeddingJobStatus(jobId: string): Promise<GraphJob> {
  return fetchJson(`/admin/text-embedding/jobs/${jobId}`, graphJobSchema, {}, { auth: true, fallbackMessage: "加载文档任务失败" });
}

export async function getRecentTextEmbeddingJobs(limit = 10): Promise<GraphJob[]> {
  return fetchJson(`/admin/text-embedding/jobs?limit=${limit}`, z.array(graphJobSchema), {}, { auth: true, fallbackMessage: "加载最近文档任务失败" });
}

export async function getDocumentStats(): Promise<Record<string, unknown>> {
  return fetchJson("/admin/documents/stats", documentStatsSchema, {}, { auth: true, fallbackMessage: "加载文档统计失败" });
}
