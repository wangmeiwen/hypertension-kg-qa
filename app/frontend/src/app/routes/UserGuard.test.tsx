import { screen, waitFor } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import UserGuard from "./UserGuard";
import { useSessionStore } from "../../entities/session/model/useSessionStore";
import { useUserAuthStore } from "../../features/user_auth/model/useUserAuthStore";
import { clearUserToken, getUserToken, setUserToken } from "../../shared/api/http";
import { renderWithProviders } from "../../test/testUtils";

const authMocks = vi.hoisted(() => ({
  getCurrentUser: vi.fn(),
  hasUserToken: vi.fn(),
}));

vi.mock("../../shared/api/userAuthClient", () => ({
  getCurrentUser: authMocks.getCurrentUser,
  hasUserToken: authMocks.hasUserToken,
}));

const renderGuard = () =>
  renderWithProviders(
    <Routes>
      <Route element={<UserGuard />}>
        <Route path="/chat" element={<div>用户聊天内容</div>} />
      </Route>
      <Route path="/login" element={<div>用户登录页</div>} />
    </Routes>,
    { route: "/chat" },
  );

describe("UserGuard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
    useUserAuthStore.setState({ user: null, isAuthenticated: false });
    useSessionStore.setState({ userId: "", currentSessionId: null, messages: [] });
  });

  it("redirects unauthenticated visitors to login", async () => {
    authMocks.hasUserToken.mockReturnValue(false);
    renderGuard();
    expect(await screen.findByText("用户登录页")).toBeVisible();
    expect(screen.queryByText("用户聊天内容")).not.toBeInTheDocument();
  });

  it("loads server identity before allowing chat access", async () => {
    authMocks.hasUserToken.mockReturnValue(true);
    authMocks.getCurrentUser.mockResolvedValue({ id: "user-1", username: "tester" });
    renderGuard();

    expect(await screen.findByText("用户聊天内容")).toBeVisible();
    await waitFor(() => expect(useSessionStore.getState().userId).toBe("user-1"));
    expect(useUserAuthStore.getState().user?.username).toBe("tester");
  });

  it("redirects when server identity validation rejects an expired token", async () => {
    authMocks.hasUserToken.mockReturnValue(true);
    authMocks.getCurrentUser.mockRejectedValue({ code: "unauthorized", status: 401 });
    renderGuard();

    expect(await screen.findByText("用户登录页")).toBeVisible();
    expect(useUserAuthStore.getState().isAuthenticated).toBe(false);
    expect(useSessionStore.getState().userId).toBe("");
  });

  it("redirects after the current tab clears an expired token", async () => {
    setUserToken("expired-token");
    authMocks.hasUserToken.mockImplementation(() => Boolean(getUserToken()));
    authMocks.getCurrentUser.mockResolvedValue({ id: "user-1", username: "tester" });
    renderGuard();
    expect(await screen.findByText("用户聊天内容")).toBeVisible();

    clearUserToken();

    expect(await screen.findByText("用户登录页")).toBeVisible();
    expect(useUserAuthStore.getState().isAuthenticated).toBe(false);
  });

  it("redirects when another tab removes the user token", async () => {
    setUserToken("shared-token");
    authMocks.hasUserToken.mockImplementation(() => Boolean(getUserToken()));
    authMocks.getCurrentUser.mockResolvedValue({ id: "user-1", username: "tester" });
    renderGuard();
    expect(await screen.findByText("用户聊天内容")).toBeVisible();

    window.localStorage.removeItem("user_token");
    window.dispatchEvent(
      new StorageEvent("storage", {
        key: "user_token",
        oldValue: "shared-token",
        newValue: null,
        storageArea: window.localStorage,
      }),
    );

    expect(await screen.findByText("用户登录页")).toBeVisible();
    expect(useSessionStore.getState().userId).toBe("");
  });
});
