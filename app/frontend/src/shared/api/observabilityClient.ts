import { z } from "zod";

import {
  observabilityRunSchema,
  observabilitySessionResponseSchema,
  observabilitySummarySchema,
  type ObservabilityRun,
  type ObservabilitySummary,
  type ObservabilityTurn,
} from "./contracts";
import { fetchJson } from "./http";

export async function getAdminAgentObservabilitySummary(limit = 200): Promise<ObservabilitySummary> {
  return fetchJson(`/admin/agent/observability-summary?limit=${limit}`, observabilitySummarySchema, {}, { auth: true, fallbackMessage: "加载 Agent 观测汇总失败" });
}

export async function getAdminAgentObservabilityRuns(limit = 50): Promise<ObservabilityRun[]> {
  return fetchJson(`/admin/agent/observability-runs?limit=${limit}`, z.array(observabilityRunSchema), {}, { auth: true, fallbackMessage: "加载 Agent 运行记录失败" });
}

export async function getAdminSessionObservability(sessionId: string): Promise<ObservabilityTurn[]> {
  const result = await fetchJson(
    `/admin/sessions/${sessionId}/observability`,
    observabilitySessionResponseSchema,
    {},
    { auth: true, fallbackMessage: "加载会话观测详情失败" },
  );
  return result.turns;
}
