import { describe, expect, it } from "vitest";

import {
  adminSessionListSchema,
  graphEntityDetailSchema,
  graphJobSchema,
  graphNodeSchema,
  graphVisualizationSchema,
  observabilitySessionResponseSchema,
  streamEventSchema,
  taskStatusSchema,
} from "./contracts";

describe("API contracts", () => {
  // 这些测试锁定前端对后端响应的最小解析契约，避免 schema 收紧导致页面运行时失败。
  it("parses supported stream event variants", () => {
    expect(
      streamEventSchema.parse({
        type: "job",
        job_id: "job-1",
        request_id: "req-1",
        task_id: "job-1",
      }),
    ).toEqual({
      type: "job",
      job_id: "job-1",
      request_id: "req-1",
      task_id: "job-1",
    });
    expect(streamEventSchema.parse({ type: "reply", delta: "回答片段", task_id: "job-1" })).toEqual(
      {
        type: "reply",
        delta: "回答片段",
        task_id: "job-1",
      },
    );
    expect(
      streamEventSchema.parse({
        type: "done",
        status: "done",
        task_id: "job-1",
        traces: [],
        processing_steps: [],
        tool_observations: [],
        evidence_summary: {},
        telemetry: {},
      }),
    ).toMatchObject({ type: "done", status: "done" });
    expect(
      streamEventSchema.parse({
        type: "error",
        code: "stream_failed",
        message: "流已终止",
        task_id: "job-1",
        recoverable: false,
      }),
    ).toMatchObject({ type: "error", code: "stream_failed" });
  });

  it("rejects duplicated reply text on done and malformed reply events", () => {
    const done = {
      type: "done",
      status: "done",
      task_id: "job-1",
      traces: [],
      processing_steps: [],
      tool_observations: [],
      evidence_summary: {},
      telemetry: {},
    };
    expect(streamEventSchema.safeParse({ ...done, msg: "重复正文" }).success).toBe(false);
    expect(
      streamEventSchema.safeParse({ type: "reply", msg: "旧字段", task_id: "job-1" }).success,
    ).toBe(false);
  });

  it("rejects unsupported task statuses", () => {
    expect(taskStatusSchema.safeParse("running").success).toBe(true);
    expect(taskStatusSchema.safeParse("unknown").success).toBe(false);
  });

  it("normalizes graph visualization defaults", () => {
    expect(graphVisualizationSchema.parse({})).toEqual({ nodes: [], links: [] });
    expect(graphNodeSchema.parse({ id: 1, properties: { name: "高血压" } })).toMatchObject({
      id: 1,
      properties: { name: "高血压" },
    });
  });

  it("parses graph entity detail relationships", () => {
    const parsed = graphEntityDetailSchema.parse({
      entity: { id: "1", name: "高血压", labels: ["Disease"] },
      relationships: {
        incoming: [],
        outgoing: [{ rel_type: "TREATS", target_name: "降压药" }],
      },
    });

    expect(parsed.relationships.outgoing[0]?.rel_props).toEqual({});
  });

  it("parses shared task models for graph and document jobs", () => {
    const parsed = graphJobSchema.parse({
      job_id: "job-1",
      status: "queued",
      result: { file_name: "guide.pdf" },
    });

    expect(parsed.result?.file_name).toBe("guide.pdf");
  });

  it("parses admin session and observability responses", () => {
    const sessions = adminSessionListSchema.parse({
      data: [
        {
          id: "s1",
          user_id: "u1",
          title: "测试",
          created_at: "2026-01-01",
          updated_at: "2026-01-01",
        },
      ],
    });
    const observability = observabilitySessionResponseSchema.parse({
      turns: [
        { turn_id: "t1", flow_events: [{ title: "主控编排", detail: { tool_call_count: 1 } }] },
      ],
    });

    expect(sessions.total).toBe(0);
    expect(observability.turns[0]?.flow_events[0]?.detail?.tool_call_count).toBe(1);
  });
});
