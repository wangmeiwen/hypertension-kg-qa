import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AdminSessionsPage from "./AdminSessionsPage";
import { renderWithProviders } from "../../test/testUtils";

const adminClientMocks = vi.hoisted(() => ({
  clearAdminSessionMessages: vi.fn(),
  deleteAdminSession: vi.fn(),
  getAdminSessionMessages: vi.fn(),
  getAdminSessions: vi.fn(),
  getAdminStats: vi.fn(),
  getReadinessStatus: vi.fn(),
}));

vi.mock("../../shared/api/adminClient", () => ({
  clearAdminSessionMessages: adminClientMocks.clearAdminSessionMessages,
  deleteAdminSession: adminClientMocks.deleteAdminSession,
  getAdminSessionMessages: adminClientMocks.getAdminSessionMessages,
  getAdminSessions: adminClientMocks.getAdminSessions,
  getAdminStats: adminClientMocks.getAdminStats,
  getReadinessStatus: adminClientMocks.getReadinessStatus,
}));

describe("AdminSessionsPage", () => {
  // 覆盖后台会话页的概览、详情、清空和删除确认流程。
  beforeEach(() => {
    vi.clearAllMocks();
    adminClientMocks.getAdminStats.mockResolvedValue({
      total_sessions: 1,
      total_messages: 2,
      today_sessions: 1,
      today_messages: 2,
    });
    adminClientMocks.getReadinessStatus.mockResolvedValue({
      level: "ready",
      checked_at: "2026-01-01T00:00:00Z",
      critical_not_ready: [],
    });
    adminClientMocks.getAdminSessions.mockResolvedValue({
      data: [{ id: "s1", user_id: "u1", title: "复诊会话", message_count: 2, created_at: "2026-01-01", updated_at: "2026-01-01" }],
      total: 1,
    });
    adminClientMocks.getAdminSessionMessages.mockResolvedValue({
      session: { id: "s1", user_id: "u1", title: "复诊会话", message_count: 2, created_at: "2026-01-01", updated_at: "2026-01-01" },
      messages: [{ id: 1, role: "user", content: "高血压怎么管理？", created_at: "2026-01-01" }],
    });
    adminClientMocks.clearAdminSessionMessages.mockResolvedValue(undefined);
    adminClientMocks.deleteAdminSession.mockResolvedValue(undefined);
  });

  it("shows session stats and opens session messages", async () => {
    const user = userEvent.setup();

    renderWithProviders(<AdminSessionsPage />);

    expect(await screen.findByText("复诊会话")).toBeVisible();
    expect(screen.getByText("总会话数")).toBeVisible();
    await user.click(screen.getByTestId("admin-open-session-s1"));

    expect(await screen.findByText("高血压怎么管理？")).toBeVisible();
  });

  it("clears and deletes selected session", async () => {
    const user = userEvent.setup();

    renderWithProviders(<AdminSessionsPage />);
    await user.click(await screen.findByTestId("admin-open-session-s1"));
    await user.click(await screen.findByRole("button", { name: "清空消息" }));
    await waitFor(() => expect(adminClientMocks.clearAdminSessionMessages).toHaveBeenCalledWith("s1"));

    await user.click(screen.getByRole("button", { name: "删除会话" }));
    await waitFor(() => expect(adminClientMocks.deleteAdminSession).toHaveBeenCalledWith("s1"));
  });

  it("deletes a session directly from the table row after confirmation", async () => {
    const user = userEvent.setup();

    renderWithProviders(<AdminSessionsPage />);
    await user.click(await screen.findByTestId("admin-delete-session-s1"));

    expect(await screen.findByText("确认删除这个会话吗？该操作会删除会话、消息和相关运行日志，且不可恢复。")).toBeVisible();
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "删除" }));

    await waitFor(() => expect(adminClientMocks.deleteAdminSession).toHaveBeenCalledWith("s1"));
  });
});
