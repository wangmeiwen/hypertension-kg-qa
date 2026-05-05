import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { forwardRef, useImperativeHandle } from "react";
import { describe, expect, it, vi } from "vitest";

import KnowledgeGraphCanvas from "./KnowledgeGraphCanvas";
import { renderWithProviders } from "../../../test/testUtils";

interface MockNode {
  id: string;
  name?: string;
  label?: string;
}

interface ForceGraphMockProps {
  graphData: { nodes: MockNode[] };
  onNodeClick?: (node: MockNode) => void;
  onBackgroundClick?: () => void;
}

vi.mock("./ForceGraphCanvas", () => ({
  default: forwardRef(function ForceGraphMock(
    { graphData, onNodeClick, onBackgroundClick }: ForceGraphMockProps,
    ref,
  ) {
    // mock 暴露 KnowledgeGraphCanvas 会调用的最小 imperative API。
    useImperativeHandle(ref, () => ({
      d3Force: () => undefined,
      d3ReheatSimulation: vi.fn(),
      zoomToFit: vi.fn(),
    }));
    return (
      <div data-testid="force-graph">
        {graphData.nodes.map((node) => (
          <button key={node.id} type="button" onClick={() => onNodeClick?.(node)}>
            {node.name || node.id}
          </button>
        ))}
        <button type="button" onClick={() => onBackgroundClick?.()}>
          清除选择
        </button>
      </div>
    );
  }),
}));

describe("KnowledgeGraphCanvas", () => {
  it("renders empty state", () => {
    renderWithProviders(<KnowledgeGraphCanvas graph={{ nodes: [], links: [] }} emptyText="暂无测试图谱" />);

    expect(screen.getByText("暂无测试图谱")).toBeVisible();
  });

  it("selects a node and opens detail panel", async () => {
    const user = userEvent.setup();
    const onNodeSelect = vi.fn();
    const onOpenDetail = vi.fn();

    renderWithProviders(
      <KnowledgeGraphCanvas
        graph={{
          nodes: [{ id: "1", labels: ["Disease"], properties: { name: "高血压" } }],
          links: [],
        }}
        selectedNodeId="1"
        onNodeSelect={onNodeSelect}
        onOpenDetail={onOpenDetail}
      />,
    );

    await user.click(await screen.findByRole("button", { name: "高血压" }));
    await user.click(screen.getByRole("button", { name: "查看详情" }));

    expect(onNodeSelect).toHaveBeenCalledWith(expect.objectContaining({ id: "1" }));
    expect(onOpenDetail).toHaveBeenCalledWith("1");
  });
});
