import { type ZodType } from "zod";

import { apiEnvelopeSchema, type ApiError } from "./contracts";

const BASE_URL = import.meta.env.VITE_BACKEND_URL || "http://127.0.0.1:8000";
const ADMIN_TOKEN_KEY = "admin_token";

export function getBaseUrl(): string {
  return BASE_URL;
}

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

function buildError(message: string, status?: number, code = "request_error"): ApiError {
  return { code, message, status };
}

async function parseResponse(response: Response): Promise<unknown> {
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
  const envelope = apiEnvelopeSchema.safeParse(payload);
  if (envelope.success && envelope.data.error) {
    return envelope.data.error;
  }
  if (payload && typeof payload === "object" && "error" in payload && typeof payload.error === "string") {
    return payload.error;
  }
  return fallback;
}

export async function fetchJson<T>(
  path: string,
  schema: ZodType<T>,
  init: RequestInit = {},
  options: { auth?: boolean; fallbackMessage?: string } = {}
): Promise<T> {
  const headers = new Headers(init.headers);
  if (options.auth) {
    const token = getAdminToken();
    if (token) {
      headers.set("Authorization", `Bearer ${token}`);
    }
  }

  const response = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers,
  });

  const payload = await parseResponse(response);
  const message = extractMessage(payload, options.fallbackMessage || `Request failed: ${response.status}`);
  if (response.status === 401) {
    clearAdminToken();
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
  options: { auth?: boolean; fallbackMessage?: string } = {}
): Promise<T> {
  const headers = new Headers(init.headers);
  if (options.auth) {
    const token = getAdminToken();
    if (token) {
      headers.set("Authorization", `Bearer ${token}`);
    }
  }

  const response = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers,
  });

  const payload = await parseResponse(response);
  const message = extractMessage(payload, options.fallbackMessage || `Request failed: ${response.status}`);
  if (response.status === 401) {
    clearAdminToken();
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
  onErrorMessage = "Streaming request failed"
): AsyncGenerator<string, void, undefined> {
  const response = await fetch(`${BASE_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (!response.ok || !response.body) {
    throw buildError(`${onErrorMessage}: ${response.status}`, response.status);
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
