import type { UnknownRecord } from "./shared";

// 图谱域历史类型定义，覆盖公开图谱、节点详情和后台图谱任务。
export interface GraphNode extends UnknownRecord {
  id: string | number;
  name?: string;
  label?: string;
  labels?: string[];
  properties?: Record<string, unknown>;
  x?: number;
  y?: number;
}

export interface GraphLink extends UnknownRecord {
  id?: string;
  source: string | number | GraphNode;
  target: string | number | GraphNode;
  label?: string;
  rel_type?: string;
}

export interface GraphVisualizationData {
  nodes: GraphNode[];
  links: GraphLink[];
}

export interface EntityDetailResponse extends UnknownRecord {
  entity: {
    id: string | number;
    name: string;
    labels: string[];
    properties: Record<string, unknown>;
  };
  relationships: {
    incoming: Array<{
      source_name: string;
      rel_type: string;
      rel_props: Record<string, unknown>;
    }>;
    outgoing: Array<{
      target_name: string;
      rel_type: string;
      rel_props: Record<string, unknown>;
    }>;
  };
}

export interface GraphNodesResponse {
  data: GraphNode[];
  total: number;
}

export interface GraphJobWarning {
  message: string;
  timestamp?: string;
}

export interface GraphJobResult extends UnknownRecord {
  message?: string;
  pause_reason?: string;
  last_error?: string;
  parsed_text_length?: number;
  processed_text_length?: number;
  chunk_count?: number;
  cache_used?: boolean;
  cache_invalidated_reason?: string;
  resume_compatible?: boolean;
  total_documents?: number;
  entities_count?: number;
  relationships_count?: number;
  entity_count?: number;
  relation_count?: number;
  node_batches?: number;
  relation_batches?: number;
  parse_warning?: string;
  file_name?: string;
  parser_used?: string;
  ocr_engine?: string;
  pages?: number;
  tables?: number;
  images?: number;
  format?: string;
  parent_chunks_count?: number;
  sub_chunks_count?: number;
  milvus_row_count?: number;
  resumed_from_chunk?: number;
  chunk_limit?: number;
  parallelism?: number;
  active_lanes?: number;
  current_attempt?: number;
  max_attempts?: number;
  lane_statuses?: Array<UnknownRecord>;
  stats?: {
    total_documents?: number;
    entities_count?: number;
    relationships_count?: number;
  };
}

export interface GraphJob extends UnknownRecord {
  job_id: string;
  job_type?: string;
  status: string;
  message?: string;
  stage?: string;
  stage_detail?: string;
  progress_text?: string;
  error?: string;
  warnings?: GraphJobWarning[];
  result?: GraphJobResult;
  total_chars?: number;
  processed_chars?: number;
  total_chunks?: number;
  completed_chunks?: number;
  current_chunk_index?: number;
  current_chunk_chars?: number;
  stage_started_at?: string;
  last_heartbeat_at?: string;
  finished_at?: string;
  created_at?: string;
  chunk_limit?: number;
  batch_size?: number;
  labels?: string[];
  cache_used?: boolean;
  cache_invalidated_reason?: string;
  resume_compatible?: boolean;
  parallelism?: number;
  active_lanes?: number;
  current_attempt?: number;
  max_attempts?: number;
  lane_statuses?: Array<UnknownRecord>;
  resumed_from_chunk?: number;
  is_resumed?: boolean;
}

export type TextEmbeddingJob = GraphJob;
