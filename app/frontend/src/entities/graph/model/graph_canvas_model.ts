import type { NodeObject } from "react-force-graph-2d";

import type { GraphNode, GraphVisualization } from "../../../shared/api/contracts";

export const DEFAULT_GRAPH_LIMIT = 80;
export const GRAPH_LIMIT_OPTIONS = [30, 50, 80, 120, 150, 200, 250, 400];

const GRAPH_RENDER_LIMIT = 160;
const LABEL_KEEP_LENGTH = 12;

export const GRAPH_NODE_COLORS: Record<string, string> = {
  Disease: "#ef4444",
  Symptom: "#f97316",
  Drug: "#22c55e",
  Treatment: "#3b82f6",
  Check: "#8b5cf6",
  Entity: "#6b7280",
};

const NODE_LABEL_TEXT: Record<"zh-CN" | "en-US", Record<string, string>> = {
  "zh-CN": {
    Disease: "疾病",
    Symptom: "症状",
    Drug: "药物",
    Treatment: "治疗",
    Check: "检查",
    Entity: "实体",
  },
  "en-US": {
    Disease: "Disease",
    Symptom: "Symptom",
    Drug: "Drug",
    Treatment: "Treatment",
    Check: "Check",
    Entity: "Entity",
  },
};

export interface GraphCanvasNode extends GraphNode {
  id: string;
  name: string;
  label: string;
  x?: number;
  y?: number;
}

export interface GraphCanvasLink {
  id: string;
  source: string | GraphCanvasNode;
  target: string | GraphCanvasNode;
  label: string;
  rel_type?: string;
  properties?: Record<string, unknown>;
}

export interface PreparedGraph {
  graphData: {
    nodes: GraphCanvasNode[];
    links: GraphCanvasLink[];
  };
  originalNodeCount: number;
  originalLinkCount: number;
  renderedNodeCount: number;
  renderedLinkCount: number;
  truncated: boolean;
}

type CanvasNode = NodeObject<GraphCanvasNode>;

export const getGraphNodeColor = (label: string): string =>
  GRAPH_NODE_COLORS[label] ?? GRAPH_NODE_COLORS.Entity;

export const getEndpointId = (
  endpoint: string | number | GraphNode | GraphCanvasNode | CanvasNode | undefined,
): string => {
  if (endpoint && typeof endpoint === "object") return String(endpoint.id);
  return String(endpoint ?? "");
};

export const truncateLabel = (label: string): string => {
  const text = String(label);
  return text.length > LABEL_KEEP_LENGTH ? `${text.slice(0, LABEL_KEEP_LENGTH)}...` : text;
};

const getNodeName = (node: GraphNode): string => {
  const properties = node.properties ?? {};
  return String(node.name ?? properties.name ?? properties.title ?? node.id);
};

const getNodeLabel = (node: GraphNode): string => {
  if (Array.isArray(node.labels) && node.labels.length > 0) return String(node.labels[0]);
  return String(node.label ?? "Entity");
};

export const buildAdjacency = (
  nodes: GraphCanvasNode[],
  links: GraphCanvasLink[],
): Map<string, Set<string>> => {
  // 构建无向邻接表，用于 hover/selected 节点的邻域高亮。
  const adjacency = new Map<string, Set<string>>(
    nodes.map((node) => [String(node.id), new Set<string>()]),
  );
  links.forEach((link) => {
    const source = getEndpointId(link.source);
    const target = getEndpointId(link.target);
    adjacency.get(source)?.add(target);
    adjacency.get(target)?.add(source);
  });
  return adjacency;
};

const getConnectedComponents = (
  nodes: GraphCanvasNode[],
  links: GraphCanvasLink[],
): GraphCanvasNode[][] => {
  const adjacency = buildAdjacency(nodes, links);
  const nodesById = new Map(nodes.map((node) => [String(node.id), node]));
  const seen = new Set<string>();
  const components: GraphCanvasNode[][] = [];

  nodes.forEach((node) => {
    const id = String(node.id);
    if (seen.has(id)) return;
    const queue = [id];
    const component: GraphCanvasNode[] = [];
    seen.add(id);

    while (queue.length > 0) {
      const nextId = queue.shift();
      if (!nextId) continue;
      const nextNode = nodesById.get(nextId);
      if (nextNode) component.push(nextNode);
      (adjacency.get(nextId) ?? new Set<string>()).forEach((neighbor) => {
        if (seen.has(neighbor)) return;
        seen.add(neighbor);
        queue.push(neighbor);
      });
    }
    components.push(component);
  });

  return components.sort((left, right) => right.length - left.length);
};

const filterByLabelNeighborhood = (
  nodes: GraphCanvasNode[],
  links: GraphCanvasLink[],
  labelFilter: string,
): { nodes: GraphCanvasNode[]; links: GraphCanvasLink[] } => {
  if (!labelFilter) return { nodes, links };
  const matchingIds = new Set(
    nodes.filter((node) => getNodeLabel(node) === labelFilter).map((node) => String(node.id)),
  );
  if (matchingIds.size === 0) return { nodes: [], links: [] };

  const keptIds = new Set(matchingIds);
  links.forEach((link) => {
    const source = getEndpointId(link.source);
    const target = getEndpointId(link.target);
    if (matchingIds.has(source)) keptIds.add(target);
    if (matchingIds.has(target)) keptIds.add(source);
  });
  return {
    nodes: nodes.filter((node) => keptIds.has(String(node.id))),
    links: links.filter(
      (link) => keptIds.has(getEndpointId(link.source)) && keptIds.has(getEndpointId(link.target)),
    ),
  };
};

const sampleGraph = (
  nodes: GraphCanvasNode[],
  links: GraphCanvasLink[],
  maxNodes: number,
): { nodes: GraphCanvasNode[]; links: GraphCanvasLink[]; truncated: boolean } => {
  if (nodes.length <= maxNodes) return { nodes, links, truncated: false };

  const degree = new Map<string, number>(nodes.map((node) => [String(node.id), 0]));
  links.forEach((link) => {
    const source = getEndpointId(link.source);
    const target = getEndpointId(link.target);
    degree.set(source, (degree.get(source) ?? 0) + 1);
    degree.set(target, (degree.get(target) ?? 0) + 1);
  });
  const components = getConnectedComponents(nodes, links).map((component) =>
    [...component].sort((left, right) => {
      const degreeDelta = (degree.get(String(right.id)) ?? 0) - (degree.get(String(left.id)) ?? 0);
      return degreeDelta !== 0
        ? degreeDelta
        : getNodeLabel(left).localeCompare(getNodeLabel(right));
    }),
  );

  const selected: GraphCanvasNode[] = [];
  const selectedIds = new Set<string>();
  let cursor = 0;
  while (selected.length < maxNodes && components.some((component) => cursor < component.length)) {
    components.forEach((component) => {
      const node = component[cursor];
      if (!node || selectedIds.has(String(node.id)) || selected.length >= maxNodes) return;
      selected.push(node);
      selectedIds.add(String(node.id));
    });
    cursor += 1;
  }
  return {
    nodes: selected,
    links: links.filter(
      (link) =>
        selectedIds.has(getEndpointId(link.source)) && selectedIds.has(getEndpointId(link.target)),
    ),
    truncated: true,
  };
};

const seedNodePositions = (
  nodes: GraphCanvasNode[],
  links: GraphCanvasLink[],
): GraphCanvasNode[] => {
  const components = getConnectedComponents(nodes, links);
  const componentCount = Math.max(components.length, 1);
  const ringRadius = Math.max(85, Math.min(220, componentCount * 34));
  const seeded = nodes.map((node) => ({ ...node }));
  const nodesById = new Map(seeded.map((node) => [String(node.id), node]));

  components.forEach((component, componentIndex) => {
    const centerAngle = (Math.PI * 2 * componentIndex) / componentCount;
    const centerX = componentCount === 1 ? 0 : Math.cos(centerAngle) * ringRadius;
    const centerY = componentCount === 1 ? 0 : Math.sin(centerAngle) * ringRadius;
    component.forEach((node, nodeIndex) => {
      const seededNode = nodesById.get(String(node.id));
      if (!seededNode) return;
      const labelOffset = getNodeLabel(node).charCodeAt(0);
      const angle = (Math.PI * 2 * nodeIndex) / Math.max(component.length, 1) + labelOffset * 0.07;
      const localRadius = Math.max(35, Math.min(100, component.length * 4.5));
      seededNode.x = centerX + Math.cos(angle) * localRadius;
      seededNode.y = centerY + Math.sin(angle) * localRadius;
    });
  });
  return seeded;
};

export const prepareGraph = (
  rawGraph: GraphVisualization | null | undefined,
  options: { labelFilter?: string; maxNodes?: number } = {},
): PreparedGraph => {
  const labelFilter = options.labelFilter ?? "";
  const maxNodes = options.maxNodes ?? GRAPH_RENDER_LIMIT;
  const rawNodes = Array.isArray(rawGraph?.nodes) ? rawGraph.nodes : [];
  const rawLinks = Array.isArray(rawGraph?.links) ? rawGraph.links : [];
  const nodes: GraphCanvasNode[] = rawNodes.map((node) => ({
    ...node,
    id: String(node.id),
    name: getNodeName(node),
    label: getNodeLabel(node),
  }));
  const nodeIds = new Set(nodes.map((node) => String(node.id)));
  const links: GraphCanvasLink[] = rawLinks
    .map((link, index) => ({
      ...link,
      id: String(
        link.id ??
          `${getEndpointId(link.source)}-${getEndpointId(link.target)}-${link.label ?? index}`,
      ),
      source: getEndpointId(link.source),
      target: getEndpointId(link.target),
      label: String(link.label ?? link.rel_type ?? "RELATED_TO"),
    }))
    .filter(
      (link) => nodeIds.has(getEndpointId(link.source)) && nodeIds.has(getEndpointId(link.target)),
    );
  const filtered = filterByLabelNeighborhood(nodes, links, labelFilter);
  const sampled = sampleGraph(filtered.nodes, filtered.links, maxNodes);
  const seededNodes = seedNodePositions(sampled.nodes, sampled.links);
  return {
    graphData: { nodes: seededNodes, links: sampled.links },
    originalNodeCount: rawNodes.length,
    originalLinkCount: rawLinks.length,
    renderedNodeCount: seededNodes.length,
    renderedLinkCount: sampled.links.length,
    truncated: sampled.truncated || filtered.nodes.length !== rawNodes.length,
  };
};

export const getLabelText = (label: string, locale: string, fallback: string): string => {
  const dictionary = NODE_LABEL_TEXT[locale === "zh-CN" ? "zh-CN" : "en-US"];
  return dictionary[label] ?? fallback;
};
