import { describe, expect, it } from "vitest";

import { buildAdjacency, prepareGraph } from "./graph_canvas_model";

describe("graph_canvas_model", () => {
  it("normalizes graph records and discards dangling links", () => {
    const prepared = prepareGraph({
      nodes: [
        { id: 1, labels: ["Disease"], properties: { name: "高血压" } },
        { id: 2, label: "Drug", name: "氨氯地平" },
      ],
      links: [
        { source: 1, target: 2, rel_type: "TREATS" },
        { source: 1, target: 99, rel_type: "UNKNOWN" },
      ],
    });

    expect(prepared.graphData.nodes.map((node) => node.name)).toEqual(["高血压", "氨氯地平"]);
    expect(prepared.graphData.links).toHaveLength(1);
    expect(prepared.graphData.links[0]).toMatchObject({
      source: "1",
      target: "2",
      label: "TREATS",
    });
  });

  it("keeps matching labels and their direct neighbors", () => {
    const prepared = prepareGraph(
      {
        nodes: [
          { id: "1", label: "Disease", name: "高血压" },
          { id: "2", label: "Drug", name: "氨氯地平" },
          { id: "3", label: "Check", name: "血常规" },
        ],
        links: [{ source: "1", target: "2", label: "TREATS" }],
      },
      { labelFilter: "Disease" },
    );

    expect(prepared.graphData.nodes.map((node) => node.id)).toEqual(["1", "2"]);
    expect(prepared.truncated).toBe(true);
  });

  it("builds symmetric adjacency for highlight calculations", () => {
    const prepared = prepareGraph({
      nodes: [
        { id: "1", label: "Disease", name: "高血压" },
        { id: "2", label: "Drug", name: "氨氯地平" },
      ],
      links: [{ source: "1", target: "2", label: "TREATS" }],
    });
    const adjacency = buildAdjacency(prepared.graphData.nodes, prepared.graphData.links);

    expect([...adjacency.get("1")!]).toEqual(["2"]);
    expect([...adjacency.get("2")!]).toEqual(["1"]);
  });
});
