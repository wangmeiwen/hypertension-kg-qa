import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AdminSettingsPage from "./AdminSettingsPage";
import { renderWithProviders } from "../../test/testUtils";

const runtimeClientMocks = vi.hoisted(() => ({
  getAdminPerfBaseline: vi.fn(),
  getAdminQueueStatus: vi.fn(),
  getAdminRuntimeFlags: vi.fn(),
}));

vi.mock("../../shared/api/runtimeClient", () => ({
  getAdminPerfBaseline: runtimeClientMocks.getAdminPerfBaseline,
  getAdminQueueStatus: runtimeClientMocks.getAdminQueueStatus,
  getAdminRuntimeFlags: runtimeClientMocks.getAdminRuntimeFlags,
}));

describe("AdminSettingsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    runtimeClientMocks.getAdminRuntimeFlags.mockResolvedValue({ flags: { test_mode: true }, updated_at: "2026-01-01T00:00:00Z" });
    runtimeClientMocks.getAdminQueueStatus.mockResolvedValue({ graph: { running: 1 } });
    runtimeClientMocks.getAdminPerfBaseline.mockResolvedValue({ window_days: 1, active_threads: 2 });
  });

  it("renders runtime, queue, backend, and language information", async () => {
    renderWithProviders(<AdminSettingsPage />);

    expect(screen.getByText("后端地址")).toBeVisible();
    expect(await screen.findByText(/test_mode/)).toBeVisible();
    expect(screen.getByText(/active_threads/)).toBeVisible();
    expect(screen.getByText(/graph/)).toBeVisible();
  });
});
