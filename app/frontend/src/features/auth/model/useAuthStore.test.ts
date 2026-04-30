import { beforeEach, describe, expect, it, vi } from "vitest";

import { clearTestStorage } from "../../../test/testUtils";

describe("useAuthStore", () => {
  beforeEach(() => {
    clearTestStorage();
    vi.resetModules();
  });

  it("initializes authentication from local storage token", async () => {
    window.localStorage.setItem("admin_token", "token-1");
    const { useAuthStore } = await import("./useAuthStore");

    expect(useAuthStore.getState().isAdminAuthenticated).toBe(true);
  });

  it("manages login dialog and authentication state", async () => {
    const { useAuthStore } = await import("./useAuthStore");

    useAuthStore.getState().openLoginDialog();
    useAuthStore.getState().setAuthenticated(true);
    expect(useAuthStore.getState().isLoginDialogOpen).toBe(true);
    expect(useAuthStore.getState().isAdminAuthenticated).toBe(true);

    useAuthStore.getState().closeLoginDialog();
    useAuthStore.getState().setAuthenticated(false);
    expect(useAuthStore.getState().isLoginDialogOpen).toBe(false);
    expect(useAuthStore.getState().isAdminAuthenticated).toBe(false);
  });
});
