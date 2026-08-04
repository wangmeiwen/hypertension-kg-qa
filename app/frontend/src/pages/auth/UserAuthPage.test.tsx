import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import UserAuthPage from "./UserAuthPage";
import { useSessionStore } from "../../entities/session/model/useSessionStore";
import { useUserAuthStore } from "../../features/user_auth/model/useUserAuthStore";
import { renderWithProviders } from "../../test/testUtils";

const authMocks = vi.hoisted(() => ({
  loginUser: vi.fn(),
  registerUser: vi.fn(),
}));

vi.mock("../../shared/api/userAuthClient", async () => {
  const actual = await vi.importActual<typeof import("../../shared/api/userAuthClient")>(
    "../../shared/api/userAuthClient",
  );
  return { ...actual, loginUser: authMocks.loginUser, registerUser: authMocks.registerUser };
});

describe("UserAuthPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useUserAuthStore.setState({ user: null, isAuthenticated: false });
    useSessionStore.setState({ userId: "", currentSessionId: null, messages: [] });
  });

  it("logs in and binds the session store to the returned user", async () => {
    const user = userEvent.setup();
    authMocks.loginUser.mockResolvedValue({ id: "user-1", username: "tester" });
    renderWithProviders(<UserAuthPage mode="login" />, { route: "/login" });

    await user.type(screen.getByLabelText("用户名"), "tester");
    await user.type(screen.getByLabelText("密码"), "password1");
    await user.click(screen.getByRole("button", { name: "登录" }));

    await waitFor(() => expect(authMocks.loginUser).toHaveBeenCalledWith("tester", "password1"));
    expect(useUserAuthStore.getState().user?.username).toBe("tester");
    expect(useSessionStore.getState().userId).toBe("user-1");
  });

  it("rejects mismatched registration passwords before calling the API", async () => {
    const user = userEvent.setup();
    renderWithProviders(<UserAuthPage mode="register" />, { route: "/register" });

    await user.type(screen.getByLabelText("用户名"), "tester");
    await user.type(screen.getByLabelText("密码"), "password1");
    await user.type(screen.getByLabelText("确认密码"), "password2");
    await user.click(screen.getByRole("button", { name: "注册并登录" }));

    expect(await screen.findByText("两次输入的密码不一致")).toBeVisible();
    expect(authMocks.registerUser).not.toHaveBeenCalled();
  });
});
