import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import GraphExplorerPage from "./GraphExplorerPage";
import { renderWithProviders } from "../../test/testUtils";

const graphClientMocks = vi.hoisted(() => ({
  fetchGraph: vi.fn(),
  getEntityDetail: vi.fn(),
}));

vi.mock("../../shared/api/graphClient", () => ({
  fetchGraph: graphClientMocks.fetchGraph,
  getEntityDetail: graphClientMocks.getEntityDetail,
}));

vi.mock("../../entities/graph/ui/KnowledgeGraphCanvas", () => ({
  DEFAULT_GRAPH_LIMIT: 80,
  GRAPH_LIMIT_OPTIONS: [30, 80, 150],
  default: ({
    onOpenDetail,
    emptyText,
  }: {
    onOpenDetail?: (id: string) => void;
    emptyText?: string;
  }) => (
    <div>
      <div>{emptyText}</div>
      <button type="button" onClick={() => onOpenDetail?.("1")}>
        打开实体详情
      </button>
    </div>
  ),
}));

describe("GraphExplorerPage", () => {
  // mock 图谱 Canvas，使页面测试聚焦查询参数和实体详情弹窗。
  beforeEach(() => {
    vi.clearAllMocks();
    graphClientMocks.fetchGraph.mockResolvedValue({
      nodes: [{ id: "1", labels: ["Disease"], properties: { name: "高血压" } }],
      links: [],
    });
    graphClientMocks.getEntityDetail.mockResolvedValue({
      entity: { id: "1", name: "高血压", labels: ["Disease"], properties: { code: "I10" } },
      relationships: {
        incoming: [
          { rel_type: "RELATED_TO", source_name: "血压升高", rel_props: { evidence: "指南" } },
        ],
        outgoing: [{ rel_type: "TREATS", target_name: "降压药", rel_props: {} }],
      },
    });
  });

  it("searches graph data and opens entity detail", async () => {
    const user = userEvent.setup();

    renderWithProviders(<GraphExplorerPage />);

    await user.type(screen.getByPlaceholderText("输入实体名称或关键词"), "高血压");
    await user.click(screen.getByRole("button", { name: "搜索" }));
    await waitFor(() => expect(graphClientMocks.fetchGraph).toHaveBeenLastCalledWith("高血压", 80));

    await user.click(screen.getByRole("button", { name: "打开实体详情" }));

    expect(await screen.findByText("实体详情")).toBeVisible();
    expect(screen.getByText("入边关系")).toBeVisible();
    expect(screen.getByText("出边关系")).toBeVisible();
    expect(screen.getByText("降压药")).toBeVisible();
  });
});
