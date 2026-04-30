import { z } from "zod";

import {
  rollbackResultSchema,
  sessionDetailSchema,
  sessionRecordSchema,
  streamEventSchema,
  uploadFileResultSchema,
  type RollbackResult,
  type SessionDetail,
  type SessionRecord,
  type StreamEvent,
  type UploadFileResult,
} from "./contracts";
import { fetchJson, fetchStream } from "./http";

const sessionListSchema = z.array(sessionRecordSchema);

export async function createSession(userId: string, title = "新建会话"): Promise<SessionRecord> {
  return fetchJson(
    "/sessions",
    sessionRecordSchema,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: userId, title }),
    },
    { fallbackMessage: "创建会话失败" },
  );
}

export async function listSessions(userId: string, limit = 50): Promise<SessionRecord[]> {
  return fetchJson(`/sessions?user_id=${encodeURIComponent(userId)}&limit=${limit}`, sessionListSchema, {}, {
    fallbackMessage: "加载会话列表失败",
  });
}

export async function getSession(sessionId: string | null, userId: string): Promise<SessionDetail> {
  if (!sessionId) {
    throw new Error("缺少会话 ID");
  }
  return fetchJson(`/sessions/${sessionId}?user_id=${encodeURIComponent(userId)}`, sessionDetailSchema, {}, {
    fallbackMessage: "加载会话失败",
  });
}

export async function updateSession(sessionId: string, userId: string, title: string): Promise<void> {
  await fetchJson(
    `/sessions/${sessionId}`,
    z.record(z.string(), z.unknown()),
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: userId, title }),
    },
    { fallbackMessage: "重命名会话失败" },
  );
}

export async function deleteSession(sessionId: string, userId: string): Promise<void> {
  await fetchJson(
    `/sessions/${sessionId}?user_id=${encodeURIComponent(userId)}`,
    z.record(z.string(), z.unknown()),
    { method: "DELETE" },
    { fallbackMessage: "删除会话失败" },
  );
}

export async function rollbackToQuestion(sessionId: string, userId: string, messageId: number): Promise<RollbackResult> {
  return fetchJson(
    `/sessions/${sessionId}/rollback-to-question`,
    rollbackResultSchema,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: userId, message_id: messageId }),
    },
    { fallbackMessage: "回滚会话失败" },
  );
}

export async function cancelQaJob(jobId: string, userId: string, sessionId: string): Promise<void> {
  await fetchJson(
    `/qa/jobs/${jobId}/cancel`,
    z.record(z.string(), z.unknown()),
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: userId, session_id: sessionId }),
    },
    { fallbackMessage: "停止任务失败" },
  );
}

export async function uploadChatFile(file: File): Promise<UploadFileResult> {
  const formData = new FormData();
  formData.append("file", file);
  return fetchJson("/upload_file", uploadFileResultSchema, { method: "POST", body: formData }, { fallbackMessage: "文件上传失败" });
}

export async function* streamProcessWithSession(
  input: string,
  userId: string,
  sessionId: string | null,
  imageData: string | null = null,
  fileName = "",
): AsyncGenerator<StreamEvent, void, undefined> {
  const payload: Record<string, unknown> = {
    input,
    user_id: userId,
    session_id: sessionId,
  };
  if (imageData) payload.image = imageData;
  if (fileName) payload.file_name = fileName;

  for await (const line of fetchStream("/process_with_session", payload, "问答流式请求失败")) {
    try {
      const json = JSON.parse(line) as unknown;
      const event = streamEventSchema.parse(json);
      yield event;
    } catch {
      yield { type: "msg", msg: line };
    }
  }
}
