import {
  perfBaselineSchema,
  queueStatusSchema,
  runtimeFlagsSchema,
  type PerfBaseline,
  type RuntimeFlags,
} from "./contracts";
import { fetchJson } from "./http";

// 运行时开关读取接口，供后台设置页展示当前 feature flags。
export async function getAdminRuntimeFlags(): Promise<RuntimeFlags> {
  return fetchJson(
    "/admin/runtime/flags",
    runtimeFlagsSchema,
    {},
    { auth: true, fallbackMessage: "加载运行时开关失败" },
  );
}

export async function getAdminPerfBaseline(days = 1): Promise<PerfBaseline> {
  // 性能基线按天数窗口查询，用于观察近期 API/队列/线程等运行指标。
  return fetchJson(
    `/admin/perf/baseline?days=${days}`,
    perfBaselineSchema,
    {},
    { auth: true, fallbackMessage: "加载性能基线失败" },
  );
}

export async function getAdminQueueStatus(): Promise<Record<string, unknown>> {
  return fetchJson(
    "/admin/qa/queue/status",
    queueStatusSchema,
    {},
    { auth: true, fallbackMessage: "加载任务队列状态失败" },
  );
}
