import { screen, waitFor } from "@testing-library/react";
import { fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";

import AdminGraphWorkspace from "./AdminGraphWorkspace";
import { renderWithProviders } from "../../../test/testUtils";

const graphClientMocks = vi.hoisted(() => ({
  createGraphNode: vi.fn(),
  createGraphRelationship: vi.fn(),
  deleteGraphNode: vi.fn(),
  deleteGraphRelationship: vi.fn(),
  executeCypherQuery: vi.fn(),
  getGraphEmbedStatus: vi.fn(),
  getGraphLabels: vi.fn(),
  getGraphNode: vi.fn(),
  getGraphNodes: vi.fn(),
  getGraphRelationshipTypes: vi.fn(),
  getRecentGraphJobs: vi.fn(),
  submitGraphEmbedMilvus: vi.fn(),
  submitGraphExtractImport: vi.fn(),
  updateGraphNode: vi.fn(),
}));

vi.mock("../../../shared/api/graphClient", () => ({
  createGraphNode: graphClientMocks.createGraphNode,
  createGraphRelationship: graphClientMocks.createGraphRelationship,
  deleteGraphNode: graphClientMocks.deleteGraphNode,
  deleteGraphRelationship: graphClientMocks.deleteGraphRelationship,
  executeCypherQuery: graphClientMocks.executeCypherQuery,
  getGraphEmbedStatus: graphClientMocks.getGraphEmbedStatus,
  getGraphLabels: graphClientMocks.getGraphLabels,
  getGraphNode: graphClientMocks.getGraphNode,
  getGraphNodes: graphClientMocks.getGraphNodes,
  getGraphRelationshipTypes: graphClientMocks.getGraphRelationshipTypes,
  getRecentGraphJobs: graphClientMocks.getRecentGraphJobs,
  submitGraphEmbedMilvus: graphClientMocks.submitGraphEmbedMilvus,
  submitGraphExtractImport: graphClientMocks.submitGraphExtractImport,
  updateGraphNode: graphClientMocks.updateGraphNode,
}));

describe("AdminGraphWorkspace", () => {
  // 统一初始化图谱管理页依赖的标签、节点、任务和 mutation mock。
  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    graphClientMocks.getGraphLabels.mockResolvedValue(["Disease"]);
    graphClientMocks.getGraphRelationshipTypes.mockResolvedValue(["TREATS"]);
    graphClientMocks.getGraphNodes.mockResolvedValue({
      data: [{ id: 1, labels: ["Disease"], properties: { name: "高血压" } }],
      total: 1,
    });
    graphClientMocks.getGraphNode.mockResolvedValue({
      node: { id: 1, labels: ["Disease"], properties: { name: "高血压" } },
      relationships: { incoming: [], outgoing: [] },
    });
    graphClientMocks.getRecentGraphJobs.mockResolvedValue([]);
    graphClientMocks.getGraphEmbedStatus.mockResolvedValue({ success: true });
    graphClientMocks.createGraphNode.mockResolvedValue({ id: 2, labels: ["Disease"], properties: { name: "继发性高血压" } });
    graphClientMocks.createGraphRelationship.mockResolvedValue({ ok: true });
    graphClientMocks.deleteGraphNode.mockResolvedValue(undefined);
    graphClientMocks.deleteGraphRelationship.mockResolvedValue(undefined);
    graphClientMocks.executeCypherQuery.mockResolvedValue([]);
    graphClientMocks.submitGraphEmbedMilvus.mockResolvedValue({ job_id: "job-2", status: "queued" });
    graphClientMocks.submitGraphExtractImport.mockResolvedValue({ job_id: "job-3", status: "queued" });
    graphClientMocks.updateGraphNode.mockResolvedValue(undefined);
  });

  it("creates a node from the modal form", async () => {
    const user = userEvent.setup();
    const view = renderWithProviders(<AdminGraphWorkspace />);

    await user.click(screen.getByRole("button", { name: "创建节点" }));
    await user.type(screen.getByPlaceholderText("节点标签"), "Disease");
    const textarea = view.container.querySelector("textarea");
    if (!textarea) throw new Error("textarea not found");
    fireEvent.change(textarea, { target: { value: '{"name":"继发性高血压"}' } });
    await user.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(graphClientMocks.createGraphNode).toHaveBeenCalledWith("Disease", { name: "继发性高血压" }));
  });

  it("updates and deletes an existing node", async () => {
    const user = userEvent.setup();
    const view = renderWithProviders(<AdminGraphWorkspace />);

    await user.click(await screen.findByRole("button", { name: "编辑节点" }));
    const textarea = view.container.querySelector("textarea");
    if (!textarea) throw new Error("textarea not found");
    fireEvent.change(textarea, { target: { value: '{"name":"高血压更新"}' } });
    await user.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(graphClientMocks.updateGraphNode).toHaveBeenCalledWith(1, { name: "高血压更新" }));

    await user.click(await screen.findByRole("button", { name: "删除节点" }));
    await waitFor(() => expect(graphClientMocks.deleteGraphNode).toHaveBeenCalledWith(1));
  });

  it("creates a relationship and runs read-only cypher", async () => {
    const user = userEvent.setup();
    graphClientMocks.executeCypherQuery.mockResolvedValue([{ name: "高血压" }]);

    renderWithProviders(<AdminGraphWorkspace />);

    await user.click(await screen.findByRole("button", { name: "创建关系" }));
    await user.type(screen.getByPlaceholderText("源节点 ID"), "1");
    await user.type(screen.getByPlaceholderText("目标节点 ID"), "2");
    await user.type(screen.getByPlaceholderText("关系类型"), "TREATS");
    await user.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(graphClientMocks.createGraphRelationship).toHaveBeenCalledWith(1, 2, "TREATS", {}));

    await user.click(screen.getByRole("button", { name: "执行 Cypher" }));
    await user.click(screen.getByRole("button", { name: "提交" }));
    await waitFor(() => expect(graphClientMocks.executeCypherQuery).toHaveBeenCalledWith("MATCH (n) RETURN n LIMIT 20"));
    expect((await screen.findAllByText(/高血压/))[0]).toBeVisible();
  });

  it("submits graph import and embedding jobs", async () => {
    const user = userEvent.setup();
    const view = renderWithProviders(<AdminGraphWorkspace />);

    const fileInput = view.container.querySelector('input[type="file"]') as HTMLInputElement | null;
    if (!fileInput) throw new Error("file input not found");
    const file = new File(["graph"], "graph.txt", { type: "text/plain" });
    await user.upload(fileInput, file);
    await user.click(await screen.findByRole("button", { name: "导入图谱" }));
    await waitFor(() => expect(graphClientMocks.submitGraphExtractImport).toHaveBeenCalledWith(file));

    await user.click(screen.getByRole("button", { name: "图谱向量化" }));
    await waitFor(() => expect(graphClientMocks.submitGraphEmbedMilvus).toHaveBeenCalledWith([], 100));
  });
});
