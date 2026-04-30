import { afterEach, describe, expect, it, vi } from "vitest";
import { z } from "zod";

import { clearAdminToken, fetchJson, fetchJsonEnvelope, fetchStream, getAdminToken, setAdminToken } from "./http";
import { clearTestStorage, mockFetchJson, mockFetchStream, mockFetchText } from "../../test/testUtils";

describe("http client", () => {
  afterEach(() => {
    clearTestStorage();
    vi.unstubAllGlobals();
  });

  it("unwraps API envelopes and validates the response schema", async () => {
    mockFetchJson({ ok: true, data: { id: "s1", title: "测试会话" } });

    await expect(fetchJson("/sessions/s1", z.object({ id: z.string(), title: z.string() }))).resolves.toEqual({
      id: "s1",
      title: "测试会话",
    });
  });

  it("keeps envelope fields when the caller needs top-level pagination metadata", async () => {
    mockFetchJson({
      ok: true,
      data: [{ id: "s1" }],
      total: 6,
    });

    await expect(
      fetchJsonEnvelope(
        "/admin/sessions",
        z.object({
          data: z.array(z.object({ id: z.string() })),
          total: z.number(),
        }),
      ),
    ).resolves.toEqual({
      data: [{ id: "s1" }],
      total: 6,
    });
  });

  it("rejects envelope responses with ok false", async () => {
    mockFetchJson({ ok: false, error: "会话加载失败" });

    await expect(fetchJsonEnvelope("/admin/sessions", z.object({}), {}, { fallbackMessage: "加载失败" })).rejects.toMatchObject({
      code: "api_error",
      message: "会话加载失败",
    });
  });

  it("passes admin authorization header when auth is enabled", async () => {
    setAdminToken("token-1");
    const fetchMock = mockFetchJson({ ok: true, data: { logged_in: true } });

    await fetchJson("/admin/status", z.object({ logged_in: z.boolean() }), {}, { auth: true });

    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer token-1");
  });

  it("clears admin token on unauthorized responses", async () => {
    setAdminToken("expired");
    mockFetchJson({ ok: false, error: "登录已过期" }, { status: 401 });

    await expect(fetchJson("/admin/status", z.object({}), {}, { auth: true })).rejects.toMatchObject({
      code: "unauthorized",
      message: "登录已过期",
      status: 401,
    });
    expect(getAdminToken()).toBe("");
  });

  it("turns schema mismatches into schema_error", async () => {
    mockFetchJson({ ok: true, data: { count: "not-number" } });

    await expect(fetchJson("/stats", z.object({ count: z.number() }))).rejects.toMatchObject({
      code: "schema_error",
    });
  });

  it("keeps non JSON error bodies readable", async () => {
    mockFetchText("gateway timeout", { status: 502 });

    await expect(fetchJson("/health", z.object({}))).rejects.toMatchObject({
      message: "gateway timeout",
      status: 502,
    });
  });

  it("parses newline separated streaming chunks", async () => {
    mockFetchStream(['{"type":"job","job_id":"j1"}', "", '{"type":"reply","msg":"你好"}']);

    const chunks: string[] = [];
    for await (const line of fetchStream("/process_with_session", { input: "高血压" })) {
      chunks.push(line);
    }

    expect(chunks).toEqual(['{"type":"job","job_id":"j1"}', '{"type":"reply","msg":"你好"}']);
  });

  it("throws for failed streaming responses", async () => {
    mockFetchJson({ error: "failed" }, { status: 500 });

    const stream = fetchStream("/process_with_session", { input: "高血压" });
    await expect(stream.next()).rejects.toMatchObject({ status: 500 });
  });

  it("manages the admin token lifecycle", () => {
    setAdminToken("token-2");
    expect(getAdminToken()).toBe("token-2");
    clearAdminToken();
    expect(getAdminToken()).toBe("");
  });
});
