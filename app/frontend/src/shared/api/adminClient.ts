import { z } from "zod";

import {
  adminSessionListSchema,
  adminSessionMessageSchema,
  adminStatsSchema,
  readinessReportSchema,
  sessionRecordSchema,
  type AdminSessionMessage,
  type AdminStats,
  type ReadinessReport,
  type SessionRecord,
} from "./contracts";
import { fetchJson, fetchJsonEnvelope } from "./http";

export async function getReadinessStatus(): Promise<ReadinessReport> {
  return fetchJson("/health/readiness", readinessReportSchema, {}, { fallbackMessage: "读取系统状态失败" });
}

export async function getAdminSessions(limit = 100, offset = 0): Promise<{ data: SessionRecord[]; total: number }> {
  return fetchJsonEnvelope(`/admin/sessions?limit=${limit}&offset=${offset}`, adminSessionListSchema, {}, { auth: true, fallbackMessage: "加载后台会话失败" });
}

export async function getAdminSessionMessages(sessionId: string): Promise<{
  session: SessionRecord;
  messages: AdminSessionMessage[];
}> {
  return fetchJson(
    `/admin/sessions/${sessionId}/messages`,
    z.object({
      session: sessionRecordSchema,
      messages: z.array(adminSessionMessageSchema).default([]),
    }),
    {},
    { auth: true, fallbackMessage: "加载会话消息失败" },
  );
}

export async function deleteAdminSession(sessionId: string): Promise<void> {
  await fetchJson(`/admin/sessions/${sessionId}`, z.record(z.string(), z.unknown()), { method: "DELETE" }, { auth: true, fallbackMessage: "删除会话失败" });
}

export async function clearAdminSessionMessages(sessionId: string): Promise<void> {
  await fetchJson(`/admin/sessions/${sessionId}/messages`, z.record(z.string(), z.unknown()), { method: "DELETE" }, { auth: true, fallbackMessage: "清空会话消息失败" });
}

export async function getAdminStats(): Promise<AdminStats> {
  return fetchJson("/admin/stats", adminStatsSchema, {}, { auth: true, fallbackMessage: "加载统计数据失败" });
}
