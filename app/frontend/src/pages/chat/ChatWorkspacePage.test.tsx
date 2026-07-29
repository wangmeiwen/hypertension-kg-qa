import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ChatWorkspacePage from "./ChatWorkspacePage";
import { useSessionStore } from "../../entities/session/model/useSessionStore";
import { renderWithProviders } from "../../test/testUtils";

const sessionClientMocks = vi.hoisted(() => ({
  cancelQaJob: vi.fn(),
  createSession: vi.fn(),
  getSession: vi.fn(),
  listSessions: vi.fn(),
  rollbackToQuestion: vi.fn(),
  streamProcessWithSession: vi.fn(),
  uploadChatFile: vi.fn(),
}));

vi.mock("../../shared/api/sessionClient", () => ({
  cancelQaJob: sessionClientMocks.cancelQaJob,
  createSession: sessionClientMocks.createSession,
  getSession: sessionClientMocks.getSession,
  listSessions: sessionClientMocks.listSessions,
  rollbackToQuestion: sessionClientMocks.rollbackToQuestion,
  streamProcessWithSession: sessionClientMocks.streamProcessWithSession,
  uploadChatFile: sessionClientMocks.uploadChatFile,
}));

vi.mock("../../shared/lib/observability", () => ({
  reportFrontendError: vi.fn(),
  trackEvent: vi.fn(),
}));

// 模拟一次完整成功的 NDJSON 事件序列。
async function* successfulStream() {
  yield { type: "job" as const, job_id: "job-1", request_id: "req-1", task_id: "job-1" };
  yield { type: "session" as const, session_id: "s1", user_message_id: 11 };
  yield {
    type: "msg" as const,
    msg: "running orchestrator",
    stage: "orchestrator" as const,
    task_id: "job-1",
  };
  yield { type: "reply" as const, delta: "基于当前知识库，建议规律监测血压。", task_id: "job-1" };
  yield {
    type: "persisted" as const,
    session_id: "s1",
    user_message_id: 11,
    assistant_message_id: 12,
  };
  yield {
    type: "done" as const,
    status: "done" as const,
    task_id: "job-1",
    traces: [],
    processing_steps: [],
    tool_observations: [],
    evidence_summary: {},
    telemetry: {},
  };
}

async function* failedStream() {
  // 先产出进度事件再抛错，覆盖页面的失败反馈路径。
  yield { type: "msg" as const, msg: "准备失败", stage: "orchestrator" as const, task_id: "job-1" };
  throw new Error("问答流式请求失败");
}

async function* cumulativeReplyStream() {
  yield { type: "session" as const, session_id: "s1", user_message_id: 11 };
  yield { type: "reply" as const, delta: "第一段", task_id: "job-1" };
  yield { type: "reply" as const, delta: "第二段", task_id: "job-1" };
  yield {
    type: "done" as const,
    status: "done" as const,
    task_id: "job-1",
    traces: [],
    processing_steps: [],
    tool_observations: [],
    evidence_summary: {},
    telemetry: {},
  };
}

describe("ChatWorkspacePage", () => {
  // 每个用例重置 store 和 session API mock，避免流式状态串扰。
  beforeEach(() => {
    vi.clearAllMocks();
    useSessionStore.setState({
      currentSessionId: null,
      messages: [],
      draft: "",
      loading: false,
      selectedFile: null,
      filePreview: null,
      currentJobId: "",
      stoppingJob: false,
    });
    sessionClientMocks.listSessions.mockResolvedValue([]);
    sessionClientMocks.createSession.mockResolvedValue({
      id: "s1",
      user_id: "u1",
      title: "新建会话",
      message_count: 0,
      created_at: "2026-01-01",
      updated_at: "2026-01-01",
    });
    sessionClientMocks.getSession.mockResolvedValue({
      id: "s1",
      user_id: "u1",
      title: "新建会话",
      message_count: 0,
      created_at: "2026-01-01",
      updated_at: "2026-01-01",
      messages: [],
    });
    sessionClientMocks.rollbackToQuestion.mockResolvedValue({ question: "高血压怎么管理？" });
    sessionClientMocks.cancelQaJob.mockResolvedValue(undefined);
    sessionClientMocks.uploadChatFile.mockResolvedValue({ base64: "file-data" });
  });

  it("sends a question and renders streaming response", async () => {
    const user = userEvent.setup();
    sessionClientMocks.streamProcessWithSession.mockImplementation(successfulStream);
    sessionClientMocks.getSession.mockResolvedValue({
      id: "s1",
      user_id: "u1",
      title: "新建会话",
      message_count: 2,
      created_at: "2026-01-01",
      updated_at: "2026-01-01",
      messages: [
        { id: 11, role: "user", content: "高血压怎么管理？" },
        { id: 12, role: "assistant", content: "基于当前知识库，建议规律监测血压。" },
      ],
    });

    renderWithProviders(<ChatWorkspacePage />);

    await user.type(screen.getByRole("textbox", { name: "问题输入" }), "高血压怎么管理？");
    await user.click(screen.getByRole("button", { name: "发送" }));

    expect(await screen.findByText("基于当前知识库，建议规律监测血压。")).toBeVisible();
    expect(sessionClientMocks.streamProcessWithSession).toHaveBeenCalledWith(
      "高血压怎么管理？",
      expect.any(String),
      "s1",
      null,
      "",
      "",
    );
  });

  it("passes uploaded aliyun file id to the streaming request", async () => {
    const user = userEvent.setup();
    sessionClientMocks.uploadChatFile.mockResolvedValue({
      base64: "",
      file_id: "file-abc",
      original_name: "guide.pdf",
    });
    sessionClientMocks.streamProcessWithSession.mockImplementation(successfulStream);

    renderWithProviders(<ChatWorkspacePage />);

    await user.upload(
      screen.getByLabelText("上传文件"),
      new File(["pdf"], "guide.pdf", { type: "application/pdf" }),
    );
    await user.type(screen.getByRole("textbox", { name: "问题输入" }), "总结这个文件");
    await user.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => {
      expect(sessionClientMocks.streamProcessWithSession).toHaveBeenCalledWith(
        "总结这个文件",
        expect.any(String),
        "s1",
        null,
        "guide.pdf",
        "file-abc",
      );
    });
  });

  it("shows failure feedback when streaming fails", async () => {
    const user = userEvent.setup();
    sessionClientMocks.streamProcessWithSession.mockImplementation(failedStream);

    renderWithProviders(<ChatWorkspacePage />);

    await user.type(screen.getByRole("textbox", { name: "问题输入" }), "触发失败");
    await user.click(screen.getByRole("button", { name: "发送" }));

    expect(await screen.findAllByText("问答流式请求失败")).not.toHaveLength(0);
  });

  it("appends reply deltas exactly once", async () => {
    const user = userEvent.setup();
    sessionClientMocks.streamProcessWithSession.mockImplementation(cumulativeReplyStream);
    sessionClientMocks.getSession.mockResolvedValue({
      id: "s1",
      user_id: "u1",
      title: "新建会话",
      message_count: 2,
      created_at: "2026-01-01",
      updated_at: "2026-01-01",
      messages: [
        { id: 11, role: "user", content: "测试累计片段" },
        { id: 12, role: "assistant", content: "第一段第二段" },
      ],
    });

    renderWithProviders(<ChatWorkspacePage />);

    await user.type(screen.getByRole("textbox", { name: "问题输入" }), "测试累计片段");
    await user.click(screen.getByRole("button", { name: "发送" }));

    expect(await screen.findByText("第一段第二段")).toBeVisible();
    expect(screen.queryByText("第一段第一段第二段")).not.toBeInTheDocument();
  });

  it("restores a user message for resend", async () => {
    const user = userEvent.setup();
    sessionClientMocks.getSession.mockResolvedValue({
      id: "s1",
      user_id: "u1",
      title: "新建会话",
      message_count: 1,
      created_at: "2026-01-01",
      updated_at: "2026-01-01",
      messages: [{ id: 1, role: "user", content: "高血压怎么管理？" }],
    });
    useSessionStore.setState({
      messages: [{ id: 1, role: "user", content: "高血压怎么管理？" }],
      currentSessionId: "s1",
    });

    renderWithProviders(<ChatWorkspacePage />);

    await user.click(screen.getByRole("button", { name: "重新发送" }));

    await waitFor(() =>
      expect(screen.getByRole("textbox", { name: "问题输入" })).toHaveValue("高血压怎么管理？"),
    );
    expect(screen.getByText("已将消息重新写回输入框，可继续发送。")).toBeVisible();
  });
});
