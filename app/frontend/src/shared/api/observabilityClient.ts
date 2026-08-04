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

// Agent 观测汇总用于后台总览页展示耗时、token、工具调用等聚合指标。
export async function getAdminAgentObservabilitySummary(
  limit = 200,
): Promise<ObservabilitySummary> {
  return fetchJson(
    `/admin/agent/observability-summary?limit=${limit}`,
    observabilitySummarySchema,
    {},
    { auth: "admin", fallbackMessage: "加载 Agent 观测汇总失败" },
  );
}

export async function getAdminAgentObservabilityRuns(limit = 50): Promise<ObservabilityRun[]> {
  return fetchJson(
    `/admin/agent/observability-runs?limit=${limit}`,
    z.array(observabilityRunSchema),
    {},
    { auth: "admin", fallbackMessage: "加载 Agent 运行记录失败" },
  );
}

export async function getAdminSessionObservability(
  sessionId: string,
): Promise<ObservabilityTurn[]> {
  // 会话观测详情只向页面暴露 turns 数组，隐藏后端响应 envelope 结构。
  const result = await fetchJson(
    `/admin/sessions/${sessionId}/observability`,
    observabilitySessionResponseSchema,
    {},
    { auth: "admin", fallbackMessage: "加载会话观测详情失败" },
  );
  return result.turns;
}
