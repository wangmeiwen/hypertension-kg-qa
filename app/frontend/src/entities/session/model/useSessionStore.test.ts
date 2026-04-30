import { beforeEach, describe, expect, it, vi } from "vitest";

import { clearTestStorage } from "../../../test/testUtils";

describe("useSessionStore", () => {
  beforeEach(() => {
    clearTestStorage();
    vi.resetModules();
  });

  it("creates and persists user and session ids", async () => {
    const { useSessionStore } = await import("./useSessionStore");
    const userId = useSessionStore.getState().userId;

    useSessionStore.getState().setCurrentSessionId("session-1");

    expect(userId).toMatch(/^user_/);
    expect(window.localStorage.getItem(`current_session_${userId}`)).toBe("session-1");
    expect(useSessionStore.getState().currentSessionId).toBe("session-1");
  });

  it("updates messages and clears attachments", async () => {
    const { useSessionStore } = await import("./useSessionStore");
    const file = new File(["data"], "report.txt", { type: "text/plain" });

    useSessionStore.getState().appendMessages({ role: "user", content: "问题" });
    useSessionStore.getState().updateMessages((messages) => [...messages, { role: "assistant", content: "回答" }]);
    useSessionStore.getState().setDraft("草稿");
    useSessionStore.getState().setSelectedFile(file);
    useSessionStore.getState().setFilePreview("data:image/png;base64,abc");
    useSessionStore.getState().clearAttachment();

    expect(useSessionStore.getState().messages).toHaveLength(2);
    expect(useSessionStore.getState().draft).toBe("草稿");
    expect(useSessionStore.getState().selectedFile).toBeNull();
    expect(useSessionStore.getState().filePreview).toBeNull();
  });

  it("resets composer and clears current session selection", async () => {
    const { useSessionStore } = await import("./useSessionStore");

    useSessionStore.getState().setCurrentSessionId("session-2");
    useSessionStore.getState().setMessages([{ role: "user", content: "问题" }]);
    useSessionStore.getState().setDraft("草稿");
    useSessionStore.getState().resetComposer();
    useSessionStore.getState().clearSessionSelection();

    expect(useSessionStore.getState().draft).toBe("");
    expect(useSessionStore.getState().currentSessionId).toBeNull();
    expect(useSessionStore.getState().messages).toEqual([]);
  });
});
