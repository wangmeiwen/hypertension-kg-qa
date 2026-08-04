import { beforeEach, describe, expect, it, vi } from "vitest";

const httpMocks = vi.hoisted(() => ({
  clearAdminToken: vi.fn(),
  fetchJson: vi.fn(),
  fetchJsonEnvelope: vi.fn(),
  fetchStream: vi.fn(),
  getAdminToken: vi.fn(),
  getBaseUrl: vi.fn(),
  setAdminToken: vi.fn(),
}));

vi.mock("./http", () => ({
  clearAdminToken: httpMocks.clearAdminToken,
  fetchJson: httpMocks.fetchJson,
  fetchJsonEnvelope: httpMocks.fetchJsonEnvelope,
  fetchStream: httpMocks.fetchStream,
  getAdminToken: httpMocks.getAdminToken,
  getBaseUrl: httpMocks.getBaseUrl,
  setAdminToken: httpMocks.setAdminToken,
}));

// 用异步生成器模拟 fetchStream 的逐行输出。
async function* streamLines(lines: string[]) {
  for (const line of lines) {
    yield line;
  }
}

describe("API clients", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    httpMocks.fetchJson.mockResolvedValue({});
    httpMocks.fetchJsonEnvelope.mockResolvedValue({});
    httpMocks.fetchStream.mockReturnValue(streamLines([]));
    httpMocks.getBaseUrl.mockReturnValue("http://127.0.0.1:8000");
  });

  it("builds session requests and parses streaming events", async () => {
    const sessionClient = await import("./sessionClient");
    httpMocks.fetchJson
      .mockResolvedValueOnce({ id: "s1" })
      .mockResolvedValueOnce([{ id: "s1" }])
      .mockResolvedValueOnce({ question: "问题" });
    httpMocks.fetchStream.mockReturnValue(
      streamLines([
        '{"type":"job","job_id":"j1","request_id":"r1","task_id":"j1"}',
        '{"type":"reply","delta":"回答","task_id":"j1"}',
      ]),
    );

    await expect(sessionClient.createSession("标题")).resolves.toEqual({ id: "s1" });
    expect(httpMocks.fetchJson).toHaveBeenCalledWith(
      "/sessions",
      expect.any(Object),
      expect.objectContaining({ method: "POST" }),
      expect.objectContaining({ fallbackMessage: "创建会话失败" }),
    );

    await expect(sessionClient.listSessions(20)).resolves.toEqual([{ id: "s1" }]);
    await expect(sessionClient.rollbackToQuestion("s1", 1)).resolves.toEqual({
      question: "问题",
    });

    const events = [];
    for await (const event of sessionClient.streamProcessWithSession("高血压", "s1", null, "")) {
      events.push(event);
    }
    expect(events).toEqual([
      { type: "job", job_id: "j1", request_id: "r1", task_id: "j1" },
      { type: "reply", delta: "回答", task_id: "j1" },
    ]);
  });

  it("rejects malformed stream events instead of turning them into progress text", async () => {
    const sessionClient = await import("./sessionClient");
    httpMocks.fetchStream.mockReturnValue(
      streamLines(['{"type":"reply","msg":"旧字段","task_id":"j1"}']),
    );

    const collect = async () => {
      for await (const event of sessionClient.streamProcessWithSession("高血压", "s1")) {
        void event;
      }
    };

    await expect(collect()).rejects.toBeDefined();
  });

  it("builds graph admin and public graph requests", async () => {
    const graphClient = await import("./graphClient");
    const file = new File(["graph"], "graph.txt", { type: "text/plain" });
    httpMocks.fetchJson
      .mockResolvedValueOnce({ nodes: [], links: [] })
      .mockResolvedValueOnce({ entity: { id: 1 } })
      .mockResolvedValueOnce({ data: ["Disease"] })
      .mockResolvedValueOnce({ data: ["TREATS"] })
      .mockResolvedValueOnce({ data: [], total: 0 })
      .mockResolvedValueOnce({ id: 2 })
      .mockResolvedValueOnce({})
      .mockResolvedValueOnce({})
      .mockResolvedValueOnce({})
      .mockResolvedValueOnce({})
      .mockResolvedValueOnce([{ name: "高血压" }])
      .mockResolvedValueOnce({ job_id: "graph-job-1", status: "queued" })
      .mockResolvedValueOnce({ job_id: "graph-job-2", status: "queued" })
      .mockResolvedValueOnce({ status: "idle" });

    await expect(graphClient.fetchGraph("高血压", 80)).resolves.toEqual({ nodes: [], links: [] });
    await expect(graphClient.getEntityDetail(1)).resolves.toEqual({ entity: { id: 1 } });
    await expect(graphClient.getGraphLabels()).resolves.toEqual(["Disease"]);
    await expect(graphClient.getGraphRelationshipTypes()).resolves.toEqual(["TREATS"]);
    await expect(
      graphClient.getGraphNodes({ label: "Disease", keyword: "高血压", limit: 10, offset: 0 }),
    ).resolves.toEqual({
      data: [],
      total: 0,
    });
    await expect(graphClient.createGraphNode("Disease", { name: "高血压" })).resolves.toEqual({
      id: 2,
    });
    await graphClient.updateGraphNode(2, { name: "更新" });
    await graphClient.deleteGraphNode(2);
    await graphClient.createGraphRelationship(1, 2, "TREATS", {});
    await graphClient.deleteGraphRelationship(1);
    await expect(graphClient.executeCypherQuery("MATCH (n) RETURN n")).resolves.toEqual([
      { name: "高血压" },
    ]);
    await expect(graphClient.submitGraphExtractImport(file)).resolves.toMatchObject({
      job_id: "graph-job-1",
    });
    await expect(graphClient.submitGraphEmbedMilvus(["Disease"], 50)).resolves.toMatchObject({
      job_id: "graph-job-2",
    });
    await expect(graphClient.getGraphEmbedStatus()).resolves.toEqual({ status: "idle" });
  });

  it("builds document, admin, observability, runtime, and auth requests", async () => {
    const graphClient = await import("./graphClient");
    const adminClient = await import("./adminClient");
    const observabilityClient = await import("./observabilityClient");
    const runtimeClient = await import("./runtimeClient");
    const authClient = await import("./authClient");
    const file = new File(["doc"], "guide.pdf", { type: "application/pdf" });

    httpMocks.fetchJson
      .mockResolvedValueOnce({ job_id: "doc-job-1", status: "queued" })
      .mockResolvedValueOnce({ job_id: "doc-job-1", status: "succeeded" })
      .mockResolvedValueOnce([{ job_id: "doc-job-1", status: "succeeded" }])
      .mockResolvedValueOnce({ row_count: 4 })
      .mockResolvedValueOnce({ level: "ready" })
      .mockResolvedValueOnce({ session: {}, messages: [] })
      .mockResolvedValueOnce({})
      .mockResolvedValueOnce({})
      .mockResolvedValueOnce({ total_sessions: 1 })
      .mockResolvedValueOnce({ total_runs: 1 })
      .mockResolvedValueOnce([{ session_id: "s1" }])
      .mockResolvedValueOnce({ turns: [] })
      .mockResolvedValueOnce({ flags: { test_mode: true } })
      .mockResolvedValueOnce({ active_threads: 1 })
      .mockResolvedValueOnce({ running: 0 })
      .mockResolvedValueOnce({ token: "token-1" })
      .mockResolvedValueOnce({ logged_in: true });
    httpMocks.fetchJsonEnvelope.mockResolvedValueOnce({ data: [{ id: "s1" }], total: 6 });

    await expect(graphClient.submitTextEmbeddingJob(file)).resolves.toMatchObject({
      job_id: "doc-job-1",
    });
    await expect(graphClient.getTextEmbeddingJobStatus("doc-job-1")).resolves.toMatchObject({
      status: "succeeded",
    });
    await expect(graphClient.getRecentTextEmbeddingJobs()).resolves.toHaveLength(1);
    await expect(graphClient.getDocumentStats()).resolves.toEqual({ row_count: 4 });
    await expect(adminClient.getReadinessStatus()).resolves.toEqual({ level: "ready" });
    await expect(adminClient.getAdminSessions()).resolves.toEqual({
      data: [{ id: "s1" }],
      total: 6,
    });
    await expect(adminClient.getAdminSessionMessages("s1")).resolves.toEqual({
      session: {},
      messages: [],
    });
    await adminClient.deleteAdminSession("s1");
    await adminClient.clearAdminSessionMessages("s1");
    await expect(adminClient.getAdminStats()).resolves.toEqual({ total_sessions: 1 });
    await expect(observabilityClient.getAdminAgentObservabilitySummary()).resolves.toEqual({
      total_runs: 1,
    });
    await expect(observabilityClient.getAdminAgentObservabilityRuns()).resolves.toEqual([
      { session_id: "s1" },
    ]);
    await expect(observabilityClient.getAdminSessionObservability("s1")).resolves.toEqual([]);
    await expect(runtimeClient.getAdminRuntimeFlags()).resolves.toEqual({
      flags: { test_mode: true },
    });
    await expect(runtimeClient.getAdminPerfBaseline()).resolves.toEqual({ active_threads: 1 });
    await expect(runtimeClient.getAdminQueueStatus()).resolves.toEqual({ running: 0 });
    await authClient.loginAsAdmin("admin", "admin123");
    await expect(authClient.checkAdminStatus()).resolves.toBe(true);

    expect(httpMocks.setAdminToken).toHaveBeenCalledWith("token-1");
  });

  it("clears admin token when status check or logout fails", async () => {
    const authClient = await import("./authClient");

    httpMocks.fetchJson
      .mockRejectedValueOnce(new Error("unauthorized"))
      .mockRejectedValueOnce(new Error("network"));

    await expect(authClient.checkAdminStatus()).resolves.toBe(false);
    await expect(authClient.logoutAdmin()).rejects.toThrow("network");
    expect(httpMocks.clearAdminToken).toHaveBeenCalledTimes(2);
  });
});
