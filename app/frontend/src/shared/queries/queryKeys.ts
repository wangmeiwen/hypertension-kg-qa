export const queryKeys = {
  auth: {
    status: ["auth", "status"] as const,
  },
  sessions: {
    list: (userId: string) => ["sessions", "list", userId] as const,
    detail: (userId: string, sessionId: string | null) => ["sessions", "detail", userId, sessionId] as const,
  },
  graph: {
    visualization: (keyword: string, limit: number) => ["graph", "visualization", keyword, limit] as const,
    entity: (entityId: string | number | null) => ["graph", "entity", entityId] as const,
    labels: ["admin", "graph", "labels"] as const,
    relationTypes: ["admin", "graph", "relation-types"] as const,
    node: (nodeId: string | number | null) => ["admin", "graph", "node", nodeId] as const,
    nodes: (label: string, keyword: string, page: number, limit: number) =>
      ["admin", "graph", "nodes", label, keyword, page, limit] as const,
    jobs: ["admin", "graph", "jobs"] as const,
    job: (jobId: string | null) => ["admin", "graph", "job", jobId] as const,
    embedStatus: ["admin", "graph", "embed-status"] as const,
  },
  admin: {
    readiness: ["admin", "readiness"] as const,
    stats: ["admin", "stats"] as const,
    sessions: (limit: number, offset: number) => ["admin", "sessions", limit, offset] as const,
    sessionMessages: (sessionId: string | null) => ["admin", "session-messages", sessionId] as const,
    observabilitySummary: ["admin", "observability", "summary"] as const,
    observabilityRuns: (limit: number) => ["admin", "observability", "runs", limit] as const,
    sessionObservability: (sessionId: string | null) => ["admin", "session-observability", sessionId] as const,
    documentStats: ["admin", "document-stats"] as const,
    documentJobs: ["admin", "document-jobs"] as const,
    documentJob: (jobId: string | null) => ["admin", "document-job", jobId] as const,
    runtimeFlags: ["admin", "runtime-flags"] as const,
    perfBaseline: (days: number) => ["admin", "perf-baseline", days] as const,
    queueStatus: ["admin", "queue-status"] as const,
  },
};
