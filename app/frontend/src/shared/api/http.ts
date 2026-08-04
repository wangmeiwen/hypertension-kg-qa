import { type ZodType } from "zod";

import { apiEnvelopeSchema, type ApiError } from "./contracts";

const BASE_URL = import.meta.env.VITE_BACKEND_URL || "http://127.0.0.1:8000";
const ADMIN_TOKEN_KEY = "admin_token";
const USER_TOKEN_KEY = "user_token";

export type AuthKind = "admin" | "user";

// 暴露后端基础地址，供诊断界面或测试断言复用。
export function getBaseUrl(): string {
  return BASE_URL;
}

// 管理员 token 存在浏览器本地存储中，供需要 auth 的请求注入 Authorization。
export function getAdminToken(): string {
  return window.localStorage.getItem(ADMIN_TOKEN_KEY) || "";
}

export function setAdminToken(token: string): void {
  if (token) {
    window.localStorage.setItem(ADMIN_TOKEN_KEY, token);
  }
}

export function clearAdminToken(): void {
  window.localStorage.removeItem(ADMIN_TOKEN_KEY);
}

export function getUserToken(): string {
  return window.localStorage.getItem(USER_TOKEN_KEY) || "";
}

export function setUserToken(token: string): void {
  if (token) {
    window.localStorage.setItem(USER_TOKEN_KEY, token);
  }
}

export function clearUserToken(): void {
  window.localStorage.removeItem(USER_TOKEN_KEY);
}

function getToken(auth: AuthKind): string {
  return auth === "admin" ? getAdminToken() : getUserToken();
}

function clearToken(auth: AuthKind): void {
  if (auth === "admin") {
    clearAdminToken();
    return;
  }
  clearUserToken();
}

function buildError(message: string, status?: number, code = "request_error"): ApiError {
  return { code, message, status };
}

async function parseResponse(response: Response): Promise<unknown> {
  // 后端部分接口可能返回空 body 或非 JSON 错误文本，这里统一转为可解析对象。
  const text = await response.text();
  if (!text) {
    return {};
  }
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return { error: text };
  }
}

function extractMessage(payload: unknown, fallback: string): string {
  // 优先读取标准 envelope.error，其次兼容裸 error 字段。
  const envelope = apiEnvelopeSchema.safeParse(payload);
  if (envelope.success && envelope.data.error) {
    return envelope.data.error;
  }
  if (
    payload &&
    typeof payload === "object" &&
    "error" in payload &&
    typeof payload.error === "string"
  ) {
    return payload.error;
  }
  return fallback;
}

export async function fetchJson<T>(
  path: string,
  schema: ZodType<T>,
  init: RequestInit = {},
  options: { auth?: AuthKind; fallbackMessage?: string } = {},
): Promise<T> {
  // fetchJson 解析标准 envelope.data；非 envelope 响应则直接按 schema 校验原始 payload。
  const headers = new Headers(init.headers);
  if (options.auth) {
    const token = getToken(options.auth);
    if (token) {
      headers.set("Authorization", `Bearer ${token}`);
    }
  }

  const response = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers,
  });

  const payload = await parseResponse(response);
  const message = extractMessage(
    payload,
    options.fallbackMessage || `Request failed: ${response.status}`,
  );
  if (response.status === 401) {
    if (options.auth) clearToken(options.auth);
    throw buildError(message, response.status, "unauthorized");
  }
  if (!response.ok) {
    throw buildError(message, response.status);
  }

  const envelope = apiEnvelopeSchema.safeParse(payload);
  if (envelope.success && envelope.data.ok === false) {
    throw buildError(message, response.status, "api_error");
  }
  const value =
    envelope.success && Object.prototype.hasOwnProperty.call(envelope.data, "data")
      ? envelope.data.data
      : payload;

  const parsed = schema.safeParse(value);
  if (!parsed.success) {
    throw buildError(parsed.error.message, response.status, "schema_error");
  }
  return parsed.data;
}

export async function fetchJsonEnvelope<T>(
  path: string,
  schema: ZodType<T>,
  init: RequestInit = {},
  options: { auth?: AuthKind; fallbackMessage?: string } = {},
): Promise<T> {
  // fetchJsonEnvelope 保留完整响应 envelope，用于需要读取 ok/token/total 等顶层字段的接口。
  const headers = new Headers(init.headers);
  if (options.auth) {
    const token = getToken(options.auth);
    if (token) {
      headers.set("Authorization", `Bearer ${token}`);
    }
  }

  const response = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers,
  });

  const payload = await parseResponse(response);
  const message = extractMessage(
    payload,
    options.fallbackMessage || `Request failed: ${response.status}`,
  );
  if (response.status === 401) {
    if (options.auth) clearToken(options.auth);
    throw buildError(message, response.status, "unauthorized");
  }
  if (!response.ok) {
    throw buildError(message, response.status);
  }

  const envelope = apiEnvelopeSchema.safeParse(payload);
  if (envelope.success && envelope.data.ok === false) {
    throw buildError(message, response.status, "api_error");
  }

  const parsed = schema.safeParse(payload);
  if (!parsed.success) {
    throw buildError(parsed.error.message, response.status, "schema_error");
  }
  return parsed.data;
}

export async function* fetchStream(
  path: string,
  payload: Record<string, unknown>,
  onErrorMessage = "Streaming request failed",
  expectedSchemaVersion?: string,
  auth?: AuthKind,
): AsyncGenerator<string, void, undefined> {
  // 后端流式接口按换行分隔事件；生成器逐行产出完整事件文本。
  const headers = new Headers({ "Content-Type": "application/json" });
  if (auth) {
    const token = getToken(auth);
    if (token) headers.set("Authorization", `Bearer ${token}`);
  }
  const response = await fetch(`${BASE_URL}${path}`, {
    method: "POST",
    headers,
    body: JSON.stringify(payload),
  });

  if (!response.ok || !response.body) {
    if (response.status === 401 && auth) clearToken(auth);
    throw buildError(`${onErrorMessage}: ${response.status}`, response.status);
  }
  if (
    expectedSchemaVersion &&
    response.headers.get("X-Stream-Schema-Version") !== expectedSchemaVersion
  ) {
    throw buildError(
      `${onErrorMessage}: stream schema version mismatch`,
      response.status,
      "schema_version_error",
    );
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let index = buffer.indexOf("\n");
    while (index !== -1) {
      const line = buffer.slice(0, index).trim();
      buffer = buffer.slice(index + 1);
      if (line) {
        yield line;
      }
      index = buffer.indexOf("\n");
    }
  }

  const tail = buffer.trim();
  if (tail) {
    yield tail;
  }
}
