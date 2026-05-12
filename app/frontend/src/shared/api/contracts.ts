import { z } from "zod";

// 后端后台任务状态枚举，兼容图谱构建、文档嵌入等异步任务。
export const taskStatusSchema = z.enum(["queued", "running", "succeeded", "failed", "cancelled", "paused"]);

// 标准 API envelope，同时 passthrough 保留后端扩展字段。
export const apiEnvelopeSchema = z
  .object({
    ok: z.boolean().optional(),
    data: z.unknown().optional(),
    error: z.string().optional(),
    token: z.string().optional(),
    logged_in: z.boolean().optional(),
    total: z.number().optional(),
  })
  .passthrough();

export const apiErrorSchema = z.object({
  code: z.string(),
  message: z.string(),
  details: z.record(z.string(), z.unknown()).optional(),
  status: z.number().optional(),
});

export const propertyRecordSchema = z.record(z.string(), z.unknown());

export const readinessReportSchema = z
  .object({
    level: z.enum(["ready", "degraded", "unavailable"]).optional(),
    checked_at: z.string().optional(),
    critical_not_ready: z.array(z.string()).optional(),
    optional_issues: z.array(z.string()).optional(),
  })
  .passthrough();

export const sessionRecordSchema = z
  .object({
    id: z.string(),
    user_id: z.string(),
    title: z.string(),
    message_count: z.number().default(0),
    created_at: z.string(),
    updated_at: z.string(),
  })
  .passthrough();

export const persistedMessageSchema = z
  .object({
    id: z.number().optional(),
    role: z.enum(["user", "assistant"]),
    content: z.string(),
    think_content: z.string().optional(),
    created_at: z.string().optional(),
  })
  .passthrough();

export const sessionDetailSchema = sessionRecordSchema.extend({
  messages: z.array(persistedMessageSchema).default([]),
});

export const uploadFileResultSchema = z
  .object({
    is_image: z.boolean().optional(),
    base64: z.string().optional(),
    file_id: z.string().optional(),
    aliyun_file_id: z.string().optional(),
    provider: z.string().optional(),
    original_name: z.string().optional(),
    filename: z.string().optional(),
    url: z.string().optional(),
  })
  .passthrough();

export const rollbackResultSchema = z
  .object({
    question: z.string().optional(),
  })
  .passthrough();

export const streamEventSchema = z.discriminatedUnion("type", [
  // SSE/流式问答事件按 type 字段区分任务、会话、消息和完成状态。
  z.object({ type: z.literal("job"), job_id: z.string().optional() }).passthrough(),
  z
    .object({
      type: z.literal("session"),
      session_id: z.string().optional(),
      user_message_id: z.number().optional(),
    })
    .passthrough(),
  z
    .object({
      type: z.literal("persisted"),
      session_id: z.string().optional(),
      user_message_id: z.number().optional(),
      assistant_message_id: z.number().optional(),
    })
    .passthrough(),
  z.object({ type: z.literal("msg"), msg: z.string().optional(), stage: z.string().optional() }).passthrough(),
  z.object({ type: z.literal("reply"), msg: z.string().optional() }).passthrough(),
  z.object({ type: z.literal("done"), msg: z.string().optional(), status: z.string().optional() }).passthrough(),
]);

export const adminStatsSchema = z
  .object({
    total_sessions: z.number(),
    total_messages: z.number(),
    today_sessions: z.number(),
    today_messages: z.number(),
  })
  .passthrough();

export const adminSessionListSchema = z.object({
  data: z.array(sessionRecordSchema),
  total: z.number().default(0),
});

export const adminSessionMessageSchema = z
  .object({
    id: z.number().optional(),
    role: z.string(),
    content: z.string(),
    created_at: z.string().optional(),
  })
  .passthrough();

export const flowEventSchema = z
  .object({
    event_id: z.string().optional(),
    event_time: z.string().optional(),
    lane: z.string().optional(),
    stage: z.string().optional(),
    title: z.string().optional(),
    summary: z.string().optional(),
    actor: z.string().optional(),
    detail: z
      .object({
        tool_call_count: z.number().optional(),
        model_input_prompt: z.string().optional(),
        model_output_text: z.string().optional(),
        tool_call_inputs: z.string().optional(),
        tool_call_results: z.string().optional(),
        model_name: z.string().optional(),
        request_id: z.string().optional(),
      })
      .passthrough()
      .optional(),
  })
  .passthrough();

export const observabilityTurnSchema = z
  .object({
    turn_id: z.string(),
    status: z.string().optional(),
    question: z.string().optional(),
    answer_digest: z.string().optional(),
    total_tokens: z.number().optional(),
    started_at: z.string().optional(),
    finished_at: z.string().optional(),
    duration_ms: z.number().optional(),
    flow_events: z.array(flowEventSchema).default([]),
  })
  .passthrough();

export const observabilitySessionResponseSchema = z.object({
  turns: z.array(observabilityTurnSchema).default([]),
});

export const observabilitySummarySchema = z
  .object({
    total_runs: z.number(),
    avg_duration_ms: z.number(),
    total_tokens: z.number(),
    total_llm_calls: z.number(),
    total_tool_calls: z.number().optional(),
    total_attempted_tool_actions: z.number().optional(),
    total_blocked_tool_actions: z.number().optional(),
    avg_tool_success_rate: z.number().optional(),
    avg_execution_tool_success_rate: z.number().optional(),
    avg_attempted_action_success_rate: z.number().optional(),
    strategy_error_types: z.record(z.string(), z.number()).optional(),
    execution_error_types: z.record(z.string(), z.number()).optional(),
  })
  .passthrough();

export const observabilityRunSchema = z
  .object({
    session_id: z.string().optional(),
    timestamp: z.string().optional(),
    llm_calls: z.number().optional(),
    attempted_tool_actions: z.number().optional(),
    blocked_tool_actions: z.number().optional(),
    tool_calls: z.number().optional(),
    total_tokens: z.number().optional(),
  })
  .passthrough();

export const graphNodeSchema = z
  .object({
    id: z.union([z.string(), z.number()]),
    name: z.string().optional(),
    label: z.string().optional(),
    labels: z.array(z.string()).optional(),
    properties: propertyRecordSchema.optional(),
    x: z.number().optional(),
    y: z.number().optional(),
  })
  .passthrough();

export const graphLinkSchema = z
  .object({
    id: z.string().optional(),
    source: z.union([z.string(), z.number(), graphNodeSchema]),
    target: z.union([z.string(), z.number(), graphNodeSchema]),
    label: z.string().optional(),
    rel_type: z.string().optional(),
  })
  .passthrough();

export const graphVisualizationSchema = z.object({
  nodes: z.array(graphNodeSchema).default([]),
  links: z.array(graphLinkSchema).default([]),
});

export const publicGraphRelationshipSchema = z
  .object({
    rel_id: z.union([z.string(), z.number()]).optional(),
    source_id: z.union([z.string(), z.number()]).optional(),
    source_labels: z.array(z.string()).optional(),
    source_name: z.string().optional(),
    target_id: z.union([z.string(), z.number()]).optional(),
    target_labels: z.array(z.string()).optional(),
    target_name: z.string().optional(),
    rel_type: z.string(),
    rel_props: propertyRecordSchema.default({}),
  })
  .passthrough();

export const graphEntityDetailSchema = z
  .object({
    entity: z.object({
      id: z.union([z.string(), z.number()]),
      name: z.string(),
      labels: z.array(z.string()).default([]),
      properties: propertyRecordSchema.default({}),
    }),
    relationships: z.object({
      incoming: z.array(publicGraphRelationshipSchema).default([]),
      outgoing: z.array(publicGraphRelationshipSchema).default([]),
    }),
  })
  .passthrough();

export const adminGraphRelationshipSchema = z
  .object({
    rel_id: z.union([z.string(), z.number()]).optional(),
    rel_type: z.string(),
    rel_props: propertyRecordSchema.default({}),
    source_id: z.union([z.string(), z.number()]).optional(),
    source_name: z.string().optional(),
    source_labels: z.array(z.string()).optional(),
    target_id: z.union([z.string(), z.number()]).optional(),
    target_name: z.string().optional(),
    target_labels: z.array(z.string()).optional(),
  })
  .passthrough();

export const adminGraphNodeDetailSchema = z.object({
  node: graphNodeSchema,
  relationships: z.object({
    incoming: z.array(adminGraphRelationshipSchema).default([]),
    outgoing: z.array(adminGraphRelationshipSchema).default([]),
  }),
});

export const graphJobSchema = z
  .object({
    // 图谱任务 schema 覆盖构建、重建、嵌入等后台任务的通用观测字段。
    job_id: z.string(),
    job_type: z.string().optional(),
    status: taskStatusSchema.or(z.string()),
    message: z.string().optional(),
    stage: z.string().optional(),
    stage_detail: z.string().optional(),
    progress_text: z.string().optional(),
    error: z.string().optional(),
    warnings: z.array(z.object({ message: z.string(), timestamp: z.string().optional() }).passthrough()).optional(),
    trace: z.array(z.record(z.string(), z.unknown())).optional(),
    labels: z.array(z.string()).optional(),
    batch_size: z.number().optional(),
    file_hash: z.string().optional(),
    file_name: z.string().optional(),
    created_at: z.string().optional(),
    started_at: z.string().optional(),
    finished_at: z.string().optional(),
    last_heartbeat_at: z.string().optional(),
    stage_started_at: z.string().optional(),
    total_chars: z.number().optional(),
    processed_chars: z.number().optional(),
    total_chunks: z.number().optional(),
    completed_chunks: z.number().optional(),
    result: z.record(z.string(), z.unknown()).optional(),
  })
  .passthrough();

export const graphNodesResponseSchema = z.object({
  data: z.array(graphNodeSchema),
  total: z.number().default(0),
});

export const graphLabelListSchema = z
  .object({
    data: z.array(z.string()).default([]),
  })
  .passthrough();

export const cypherResultSchema = z.array(propertyRecordSchema);

export const runtimeFlagsSchema = z
  .object({
    flags: propertyRecordSchema.default({}),
    updated_at: z.string().optional(),
  })
  .passthrough();

export const perfBaselineSchema = z
  .object({
    window_days: z.number().optional(),
    api_latency: propertyRecordSchema.optional(),
    event_loop_lag_ms: z.unknown().optional(),
    queue: z.unknown().optional(),
    runtime_flags: propertyRecordSchema.optional(),
    active_threads: z.number().optional(),
    generated_at: z.string().optional(),
  })
  .passthrough();

export const queueStatusSchema = z.record(z.string(), z.unknown());

export const documentStatsSchema = z.record(z.string(), z.unknown());

export type ApiEnvelope = z.infer<typeof apiEnvelopeSchema>;
export type ApiError = z.infer<typeof apiErrorSchema>;
export type TaskStatus = z.infer<typeof taskStatusSchema>;
export type ReadinessReport = z.infer<typeof readinessReportSchema>;
export type SessionRecord = z.infer<typeof sessionRecordSchema>;
export type SessionDetail = z.infer<typeof sessionDetailSchema>;
export type PersistedMessage = z.infer<typeof persistedMessageSchema>;
export type UploadFileResult = z.infer<typeof uploadFileResultSchema>;
export type RollbackResult = z.infer<typeof rollbackResultSchema>;
export type StreamEvent = z.infer<typeof streamEventSchema>;
export type AdminStats = z.infer<typeof adminStatsSchema>;
export type AdminSessionMessage = z.infer<typeof adminSessionMessageSchema>;
export type FlowEvent = z.infer<typeof flowEventSchema>;
export type ObservabilityTurn = z.infer<typeof observabilityTurnSchema>;
export type ObservabilityRun = z.infer<typeof observabilityRunSchema>;
export type ObservabilitySummary = z.infer<typeof observabilitySummarySchema>;
export type GraphNode = z.infer<typeof graphNodeSchema>;
export type GraphLink = z.infer<typeof graphLinkSchema>;
export type GraphVisualization = z.infer<typeof graphVisualizationSchema>;
export type GraphJob = z.infer<typeof graphJobSchema>;
export type GraphEntityDetail = z.infer<typeof graphEntityDetailSchema>;
export type AdminGraphNodeDetail = z.infer<typeof adminGraphNodeDetailSchema>;
export type AdminGraphRelationship = z.infer<typeof adminGraphRelationshipSchema>;
export type RuntimeFlags = z.infer<typeof runtimeFlagsSchema>;
export type PerfBaseline = z.infer<typeof perfBaselineSchema>;
