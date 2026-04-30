import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import SessionListPanel from "./SessionListPanel";
import { useSessionStore } from "../../entities/session/model/useSessionStore";
import { renderWithProviders } from "../../test/testUtils";

const sessionClientMocks = vi.hoisted(() => ({
  createSession: vi.fn(),
  deleteSession: vi.fn(),
  listSessions: vi.fn(),
  updateSession: vi.fn(),
}));

vi.mock("../../shared/api/sessionClient", () => ({
  createSession: sessionClientMocks.createSession,
  deleteSession: sessionClientMocks.deleteSession,
  listSessions: sessionClientMocks.listSessions,
  updateSession: sessionClientMocks.updateSession,
}));

describe("SessionListPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    useSessionStore.setState({ currentSessionId: null, messages: [] });
    sessionClientMocks.listSessions.mockResolvedValue([
      {
        id: "s1",
        user_id: "u1",
        title: "高血压随访",
        message_count: 2,
        created_at: "2026-01-01T00:00:00Z",
        updated_at: "2026-01-01T00:00:00Z",
      },
    ]);
    sessionClientMocks.createSession.mockResolvedValue({
      id: "s2",
      user_id: "u1",
      title: "新建会话",
      message_count: 0,
      created_at: "2026-01-02T00:00:00Z",
      updated_at: "2026-01-02T00:00:00Z",
    });
    sessionClientMocks.updateSession.mockResolvedValue(undefined);
    sessionClientMocks.deleteSession.mockResolvedValue(undefined);
  });

  it("creates and selects a new session", async () => {
    const user = userEvent.setup();

    renderWithProviders(<SessionListPanel />);

    await user.click(screen.getByRole("button", { name: "新建会话" }));

    await waitFor(() => expect(sessionClientMocks.createSession).toHaveBeenCalled());
    expect(useSessionStore.getState().currentSessionId).toBe("s2");
  });

  it("renames and deletes a session", async () => {
    const user = userEvent.setup();

    renderWithProviders(<SessionListPanel />);

    await screen.findByText("高血压随访");
    await user.click(screen.getByTestId("session-rename-button-s1"));
    await user.clear(screen.getByTestId("session-rename-input-s1"));
    await user.type(screen.getByTestId("session-rename-input-s1"), "复诊计划");
    await user.tab();
    await waitFor(() => expect(sessionClientMocks.updateSession).toHaveBeenCalledWith("s1", expect.any(String), "复诊计划"));

    await user.click(screen.getByTestId("session-delete-button-s1"));
    await waitFor(() => expect(sessionClientMocks.deleteSession).toHaveBeenCalledWith("s1", expect.any(String)));
  });

  it("selects a session and clears stale messages", async () => {
    const user = userEvent.setup();
    useSessionStore.setState({ messages: [{ role: "assistant", content: "旧消息" }] });

    renderWithProviders(<SessionListPanel />);
    await user.click(await screen.findByText("高血压随访"));

    expect(useSessionStore.getState().currentSessionId).toBe("s1");
    expect(useSessionStore.getState().messages).toEqual([]);
  });
});
