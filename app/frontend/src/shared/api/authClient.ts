import { z } from "zod";

import { clearAdminToken, fetchJson, getAdminToken, setAdminToken } from "./http";

const loginSchema = z
  .object({
    token: z.string().optional(),
  })
  .passthrough();

const statusSchema = z
  .object({
    logged_in: z.boolean().default(false),
  })
  .passthrough();

// 登录成功后保存后端签发的管理员 token，后续 auth 请求由 http 层自动注入。
export async function loginAsAdmin(username: string, password: string): Promise<void> {
  const result = await fetchJson(
    "/admin/login",
    loginSchema,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    },
    { fallbackMessage: "管理员登录失败" },
  );
  if (result.token) {
    setAdminToken(result.token);
  }
}

export async function checkAdminStatus(): Promise<boolean> {
  try {
    // 认证状态校验失败时按未登录处理，并清理可能过期的本地 token。
    const result = await fetchJson(
      "/admin/status",
      statusSchema,
      {},
      { auth: "admin", fallbackMessage: "管理员状态校验失败" },
    );
    return result.logged_in;
  } catch {
    clearAdminToken();
    return false;
  }
}

export async function logoutAdmin(): Promise<void> {
  try {
    await fetchJson(
      "/admin/logout",
      z.record(z.string(), z.unknown()),
      { method: "POST" },
      { auth: "admin" },
    );
  } finally {
    clearAdminToken();
  }
}

export function hasAdminToken(): boolean {
  return Boolean(getAdminToken());
}
