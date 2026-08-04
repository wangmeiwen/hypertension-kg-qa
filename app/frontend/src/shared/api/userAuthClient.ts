import { z } from "zod";

import { clearUserToken, fetchJson, getUserToken, setUserToken } from "./http";

export const userSchema = z.object({
  id: z.string(),
  username: z.string(),
});

const userAuthSchema = z.object({
  token: z.string().min(1),
  expires_at: z.string(),
  user: userSchema,
});

export type AuthenticatedUser = z.infer<typeof userSchema>;

async function authenticate(
  path: "/auth/login" | "/auth/register",
  username: string,
  password: string,
) {
  const result = await fetchJson(
    path,
    userAuthSchema,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    },
    { fallbackMessage: path === "/auth/login" ? "登录失败" : "注册失败" },
  );
  setUserToken(result.token);
  return result.user;
}

export const loginUser = (username: string, password: string) =>
  authenticate("/auth/login", username, password);

export const registerUser = (username: string, password: string) =>
  authenticate("/auth/register", username, password);

export const getCurrentUser = () =>
  fetchJson("/auth/me", userSchema, {}, { auth: "user", fallbackMessage: "登录状态已失效" });

export async function logoutUser(): Promise<void> {
  try {
    await fetchJson(
      "/auth/logout",
      z.record(z.string(), z.unknown()),
      { method: "POST" },
      { auth: "user", fallbackMessage: "退出登录失败" },
    );
  } finally {
    clearUserToken();
  }
}

export const hasUserToken = () => Boolean(getUserToken());
