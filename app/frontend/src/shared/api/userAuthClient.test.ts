import { afterEach, describe, expect, it, vi } from "vitest";

import { getUserToken } from "./http";
import { getCurrentUser, loginUser, logoutUser, registerUser } from "./userAuthClient";
import { clearTestStorage, mockFetchJson } from "../../test/testUtils";

describe("user auth client", () => {
  afterEach(() => {
    clearTestStorage();
    vi.unstubAllGlobals();
  });

  it.each([
    ["login", loginUser, "/auth/login"],
    ["register", registerUser, "/auth/register"],
  ] as const)("stores the user token after %s", async (_, action, path) => {
    const fetchMock = mockFetchJson({
      ok: true,
      data: {
        token: "user-token",
        expires_at: "2026-08-11T00:00:00Z",
        user: { id: "user-1", username: "tester" },
      },
    });

    await expect(action("tester", "password1")).resolves.toEqual({
      id: "user-1",
      username: "tester",
    });
    expect(fetchMock.mock.calls[0]?.[0]).toContain(path);
    expect(getUserToken()).toBe("user-token");
  });

  it("uses the user bearer token for identity and logout", async () => {
    window.localStorage.setItem("user_token", "user-token");
    const responses = [
      new Response(JSON.stringify({ ok: true, data: { id: "user-1", username: "tester" } })),
      new Response(JSON.stringify({ ok: true, message: "已退出登录" })),
    ];
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(responses.shift()));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getCurrentUser()).resolves.toEqual({ id: "user-1", username: "tester" });
    await logoutUser();

    for (const call of fetchMock.mock.calls) {
      expect(new Headers((call[1] as RequestInit).headers).get("Authorization")).toBe(
        "Bearer user-token",
      );
    }
    expect(getUserToken()).toBe("");
  });
});
