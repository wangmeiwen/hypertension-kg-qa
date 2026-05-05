// 共享 JSON 与 API envelope 类型，供旧接口类型和少量泛型工具复用。
export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonObject | JsonValue[];
export interface JsonObject {
  [key: string]: JsonValue | undefined;
}

export type UnknownRecord = Record<string, unknown>;

export interface ApiEnvelope<T> {
  ok?: boolean;
  data?: T;
  error?: string;
  token?: string;
  logged_in?: boolean;
  total?: number;
  [key: string]: unknown;
}
