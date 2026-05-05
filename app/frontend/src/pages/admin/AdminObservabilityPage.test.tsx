import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AdminObservabilityPage from "./AdminObservabilityPage";
import { renderWithProviders } from "../../test/testUtils";

const adminClientMocks = vi.hoisted(() => ({
  getAdminSessionMessages: vi.fn(),
}));

const observabilityClientMocks = vi.hoisted(() => ({
  getAdminAgentObservabilityRuns: vi.fn(),
  getAdminAgentObservabilitySummary: vi.fn(),
  getAdminSessionObservability: vi.fn(),
}));

vi.mock("../../shared/api/adminClient", () => ({
  getAdminSessionMessages: adminClientMocks.getAdminSessionMessages,
}));

vi.mock("../../shared/api/observabilityClient", () => ({
  getAdminAgentObservabilityRuns: observabilityClientMocks.getAdminAgentObservabilityRuns,
  getAdminAgentObservabilitySummary: observabilityClientMocks.getAdminAgentObservabilitySummary,
  getAdminSessionObservability: observabilityClientMocks.getAdminSessionObservability,
}));

describe("AdminObservabilityPage", () => {
  // 观测页测试用固定 run/turn/event 数据覆盖聚合、泳道和详情弹窗。
  beforeEach(() => {
    vi.clearAllMocks();
    observabilityClientMocks.getAdminAgentObservabilitySummary.mockResolvedValue({
      total_runs: 1,
      avg_duration_ms: 120,
      total_tokens: 80,
      total_llm_calls: 1,
      total_tool_calls: 2,
    });
    observabilityClientMocks.getAdminAgentObservabilityRuns.mockResolvedValue([
      { session_id: "s1", timestamp: "2026-01-01T00:00:00Z", llm_calls: 1, tool_calls: 2, total_tokens: 80 },
    ]);
    observabilityClientMocks.getAdminSessionObservability.mockResolvedValue([
      {
        turn_id: "turn-1",
        question: "高血压怎么管理？",
        answer_digest: "控制血压",
        duration_ms: 120,
        flow_events: [
          {
            event_id: "e1",
            lane: "orchestrator",
            title: "主控编排",
            summary: "规划下一步",
            detail: { model_input_prompt: "用户问题", model_output_text: "计划", tool_call_count: 1 },
          },
          {
            event_id: "e2",
            lane: "graph",
            title: "图谱代理",
            summary: "查询图谱",
            detail: { tool_call_inputs: "MATCH", tool_call_results: "高血压" },
          },
        ],
      },
    ]);
    adminClientMocks.getAdminSessionMessages.mockResolvedValue({
      session: { id: "s1", user_id: "u1", title: "观测会话", message_count: 1, created_at: "2026-01-01", updated_at: "2026-01-01" },
      messages: [],
    });
  });

  it("opens a session, groups events by lane, and shows event detail", async () => {
    const user = userEvent.setup();

    renderWithProviders(<AdminObservabilityPage />);

    await user.click(await screen.findByRole("button", { name: "Agent 观测" }));
    expect((await screen.findAllByText("主控编排"))[0]).toBeVisible();
    expect(screen.getAllByText("图谱代理")[0]).toBeVisible();

    await user.click(screen.getByRole("button", { name: /主控编排/ }));
    expect(await screen.findByText("模型输入")).toBeVisible();
    expect(screen.getByText("用户问题")).toBeVisible();
  });
});
