import { describe, expect, it } from "vitest";

import { appendThinkStatus, formatThinkStatuses, getLatestThinkStatus } from "./think_status_utils";

describe("think_status_utils", () => {
  it("优先按明确阶段归一化并去重", () => {
    const memory = appendThinkStatus([], "未知文本", " memory ");
    const duplicate = appendThinkStatus(memory, "loading session memory");
    const orchestrator = appendThinkStatus(duplicate, "未知文本", "orchestrator");

    expect(memory).toEqual(["加载会话记忆"]);
    expect(duplicate).toBe(memory);
    expect(orchestrator).toEqual(["加载会话记忆", "执行主编排流程"]);
  });

  it("从中英文关键词归一化四类阶段", () => {
    let statuses: string[] = [];
    statuses = appendThinkStatus(statuses, "prepare_session_turn");
    statuses = appendThinkStatus(statuses, "running orchestrator");
    statuses = appendThinkStatus(statuses, "retrieving support context");
    statuses = appendThinkStatus(statuses, "composing answer");

    expect(statuses).toEqual(["加载会话记忆", "执行主编排流程", "检索支持上下文", "组织最终回答"]);
  });

  it("忽略空值和无法识别的内部状态", () => {
    const statuses = ["加载会话记忆"];

    expect(appendThinkStatus(statuses, "")).toBe(statuses);
    expect(appendThinkStatus(statuses, "completely unknown")).toBe(statuses);
    expect(formatThinkStatuses(["加载会话记忆", "", "组织最终回答"])).toBe(
      "加载会话记忆\n组织最终回答",
    );
  });

  it("从多行历史内容提取并返回最新阶段", () => {
    const content = [
      "loading session memory",
      "planner is running",
      "RAG search completed",
      "orchestrator_done",
    ].join("\n");

    expect(getLatestThinkStatus(content)).toBe("组织最终回答");
    expect(getLatestThinkStatus("加载会话记忆")).toBe("加载会话记忆");
  });

  it("处理空历史和单行混合关键词", () => {
    expect(getLatestThinkStatus("")).toBe("");
    expect(getLatestThinkStatus("memory then search then answer")).toBe("组织最终回答");
  });
});
