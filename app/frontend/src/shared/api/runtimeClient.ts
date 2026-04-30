import { perfBaselineSchema, queueStatusSchema, runtimeFlagsSchema, type PerfBaseline, type RuntimeFlags } from "./contracts";
import { fetchJson } from "./http";

export async function getAdminRuntimeFlags(): Promise<RuntimeFlags> {
  return fetchJson("/admin/runtime/flags", runtimeFlagsSchema, {}, { auth: true, fallbackMessage: "加载运行时开关失败" });
}

export async function getAdminPerfBaseline(days = 1): Promise<PerfBaseline> {
  return fetchJson(`/admin/perf/baseline?days=${days}`, perfBaselineSchema, {}, { auth: true, fallbackMessage: "加载性能基线失败" });
}

export async function getAdminQueueStatus(): Promise<Record<string, unknown>> {
  return fetchJson("/admin/qa/queue/status", queueStatusSchema, {}, { auth: true, fallbackMessage: "加载任务队列状态失败" });
}
