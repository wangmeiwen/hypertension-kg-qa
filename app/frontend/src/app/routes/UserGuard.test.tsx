import { screen, waitFor } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import UserGuard from "./UserGuard";
import { useSessionStore } from "../../entities/session/model/useSessionStore";
import { useUserAuthStore } from "../../features/user_auth/model/useUserAuthStore";
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
});
