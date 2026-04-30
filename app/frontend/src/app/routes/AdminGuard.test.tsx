import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AdminGuard from "./AdminGuard";
import { useAuthStore } from "../../features/auth/model/useAuthStore";
import { renderWithProviders } from "../../test/testUtils";

const authClientMocks = vi.hoisted(() => ({
  checkAdminStatus: vi.fn(),
  loginAsAdmin: vi.fn(),
}));

vi.mock("../../shared/api/authClient", async () => {
  const actual = await vi.importActual<typeof import("../../shared/api/authClient")>("../../shared/api/authClient");
  return {
    ...actual,
    checkAdminStatus: authClientMocks.checkAdminStatus,
    loginAsAdmin: authClientMocks.loginAsAdmin,
  };
});

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return {
    ...actual,
    Outlet: () => <div>受保护后台内容</div>,
  };
});

describe("AdminGuard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAuthStore.setState({ isAdminAuthenticated: false, isLoginDialogOpen: false });
  });

  it("shows login gate when admin status is invalid", async () => {
    authClientMocks.checkAdminStatus.mockResolvedValue(false);

    renderWithProviders(<AdminGuard />);

    expect((await screen.findAllByText("需要管理员权限"))[0]).toBeVisible();
    expect(screen.queryByText("受保护后台内容")).not.toBeInTheDocument();
  });

  it("allows authenticated users through", async () => {
    authClientMocks.checkAdminStatus.mockResolvedValue(true);

    renderWithProviders(<AdminGuard />);

    expect(await screen.findByText("受保护后台内容")).toBeVisible();
  });

  it("opens login dialog and submits credentials", async () => {
    const user = userEvent.setup();
    authClientMocks.checkAdminStatus.mockResolvedValue(false);
    authClientMocks.loginAsAdmin.mockResolvedValue(undefined);

    renderWithProviders(<AdminGuard />);

    await user.click(await screen.findByRole("button", { name: "管理员登录" }));
    await user.clear(screen.getByLabelText("用户名"));
    await user.type(screen.getByLabelText("用户名"), "admin");
    await user.clear(screen.getByLabelText("密码"));
    await user.type(screen.getByLabelText("密码"), "admin123");
    await user.click(screen.getByRole("dialog").querySelector('button[type="submit"]') as HTMLButtonElement);

    await waitFor(() => expect(authClientMocks.loginAsAdmin).toHaveBeenCalledWith("admin", "admin123"));
  });
});
