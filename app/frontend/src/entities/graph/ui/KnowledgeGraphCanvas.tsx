import { forceCollide, forceX, forceY } from "d3-force-3d";
import {
  lazy,
  Suspense,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  type ForceGraphMethods,
  type LinkObject,
  type NodeObject,
} from "react-force-graph-2d";
import { useTranslation } from "react-i18next";

import styles from "./KnowledgeGraphCanvas.module.css";
import type { GraphNode, GraphVisualization } from "../../../shared/api/contracts";
import { Button, SkeletonBlock } from "../../../shared/ui/primitives";

export const DEFAULT_GRAPH_LIMIT = 80;
export const GRAPH_LIMIT_OPTIONS = [30, 50, 80, 120, 150, 200, 250, 400];

const GRAPH_RENDER_LIMIT = 160;
const LABEL_KEEP_LENGTH = 12;

const NODE_COLORS: Record<string, string> = {
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

interface PreparedGraph {
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

interface KnowledgeGraphCanvasProps {
  graph: GraphVisualization | null | undefined;
  className?: string;
  labelFilter?: string;
  selectedNodeId?: string;
  onNodeSelect?: (node: GraphCanvasNode | null) => void;
  onOpenDetail?: (entityId: string) => void;
  emptyText?: string;
}

type CanvasNode = NodeObject<GraphCanvasNode>;
type CanvasLink = LinkObject<GraphCanvasNode, GraphCanvasLink>;
const ForceGraphCanvas = lazy(() => import("./ForceGraphCanvas"));

type D3ChargeForce = { strength?: (value: number) => unknown };
type D3LinkForce = { distance?: (value: number | ((link: CanvasLink) => number)) => unknown };
type RegisteredForce = NonNullable<Parameters<ForceGraphMethods<GraphCanvasNode, GraphCanvasLink>["d3Force"]>[1]>;
type CollideForceFactory = { iterations: (count: number) => unknown };
type AxisForceFactory = { strength: (value: number) => unknown };

function getGraphNodeColor(label: string): string {
  return NODE_COLORS[label] || NODE_COLORS.Entity;
}

function getEndpointId(endpoint: string | number | GraphNode | GraphCanvasNode | CanvasNode | undefined): string {
  if (endpoint && typeof endpoint === "object") {
    return String(endpoint.id);
  }
  return String(endpoint ?? "");
}

function truncateLabel(label: string): string {
  const text = String(label || "");
  return text.length > LABEL_KEEP_LENGTH ? `${text.slice(0, LABEL_KEEP_LENGTH)}...` : text;
}

function getNodeName(node: GraphNode): string {
  const properties = node.properties || {};
  const propertyName = properties.name;
  const propertyTitle = properties.title;
  return String(node.name || propertyName || propertyTitle || node.id || "");
}

function getNodeLabel(node: GraphNode): string {
  if (Array.isArray(node.labels) && node.labels.length > 0) {
    return String(node.labels[0]);
  }
  return String(node.label || "Entity");
}

function buildAdjacency(nodes: GraphCanvasNode[], links: GraphCanvasLink[]): Map<string, Set<string>> {
  // 构建无向邻接表，用于 hover/selected 节点的邻域高亮。
  const adjacency = new Map<string, Set<string>>(nodes.map((node) => [String(node.id), new Set<string>()]));
  for (const link of links) {
    const source = getEndpointId(link.source);
    const target = getEndpointId(link.target);
    adjacency.get(source)?.add(target);
    adjacency.get(target)?.add(source);
  }
  return adjacency;
}

function getConnectedComponents(nodes: GraphCanvasNode[], links: GraphCanvasLink[]): GraphCanvasNode[][] {
  // 找出连通分量，后续用于大图采样和初始布局分区。
  const adjacency = buildAdjacency(nodes, links);
  const seen = new Set<string>();
  const components: GraphCanvasNode[][] = [];

  for (const node of nodes) {
    const id = String(node.id);
    if (seen.has(id)) continue;

    const queue: string[] = [id];
    const component: GraphCanvasNode[] = [];
    seen.add(id);

    while (queue.length > 0) {
      const nextId = queue.shift();
      if (!nextId) continue;
      const nextNode = nodes.find((item) => String(item.id) === nextId);
      if (nextNode) component.push(nextNode);
      for (const neighbor of adjacency.get(nextId) || []) {
        if (!seen.has(neighbor)) {
          seen.add(neighbor);
          queue.push(neighbor);
        }
      }
    }

    components.push(component);
  }

  return components.sort((a, b) => b.length - a.length);
}

function filterByLabelNeighborhood(
  nodes: GraphCanvasNode[],
  links: GraphCanvasLink[],
  labelFilter: string,
): { nodes: GraphCanvasNode[]; links: GraphCanvasLink[] } {
  if (!labelFilter) {
    return { nodes, links };
  }

  const matchingIds = new Set(nodes.filter((node) => getNodeLabel(node) === labelFilter).map((node) => String(node.id)));
  if (matchingIds.size === 0) {
    return { nodes: [], links: [] };
  }

  const keptIds = new Set<string>(matchingIds);
  for (const link of links) {
    const source = getEndpointId(link.source);
    const target = getEndpointId(link.target);
    if (matchingIds.has(source)) keptIds.add(target);
    if (matchingIds.has(target)) keptIds.add(source);
  }

  return {
    nodes: nodes.filter((node) => keptIds.has(String(node.id))),
    links: links.filter((link) => keptIds.has(getEndpointId(link.source)) && keptIds.has(getEndpointId(link.target))),
  };
}

function sampleGraph(
  nodes: GraphCanvasNode[],
  links: GraphCanvasLink[],
  maxNodes: number,
): { nodes: GraphCanvasNode[]; links: GraphCanvasLink[]; truncated: boolean } {
  // 大图按连通分量和节点度数轮询采样，避免只保留单个高密度子图。
  if (nodes.length <= maxNodes) {
    return { nodes, links, truncated: false };
  }

  const degree = new Map<string, number>(nodes.map((node) => [String(node.id), 0]));
  for (const link of links) {
    const source = getEndpointId(link.source);
    const target = getEndpointId(link.target);
    degree.set(source, (degree.get(source) || 0) + 1);
    degree.set(target, (degree.get(target) || 0) + 1);
  }

  const components = getConnectedComponents(nodes, links).map((component) =>
    [...component].sort((a, b) => {
      const degreeDelta = (degree.get(String(b.id)) || 0) - (degree.get(String(a.id)) || 0);
      if (degreeDelta !== 0) return degreeDelta;
      return getNodeLabel(a).localeCompare(getNodeLabel(b));
    }),
  );

  const selected: GraphCanvasNode[] = [];
  const selectedIds = new Set<string>();
  let cursor = 0;

  while (selected.length < maxNodes && components.some((component) => cursor < component.length)) {
    for (const component of components) {
      const node = component[cursor];
      if (node && !selectedIds.has(String(node.id))) {
        selected.push(node);
        selectedIds.add(String(node.id));
        if (selected.length >= maxNodes) break;
      }
    }
    cursor += 1;
  }

  return {
    nodes: selected,
    links: links.filter((link) => selectedIds.has(getEndpointId(link.source)) && selectedIds.has(getEndpointId(link.target))),
    truncated: true,
  };
}

function seedNodePositions(nodes: GraphCanvasNode[], links: GraphCanvasLink[]): GraphCanvasNode[] {
  // 给每个连通分量设置初始环形坐标，减少力导向图冷启动重叠。
  const components = getConnectedComponents(nodes, links);
  const componentCount = Math.max(components.length, 1);
  const ringRadius = Math.max(85, Math.min(220, componentCount * 34));
  const seeded = nodes.map((node) => ({ ...node }));
  const byId = new Map<string, GraphCanvasNode>(seeded.map((node) => [String(node.id), node]));

  components.forEach((component, componentIndex) => {
    const centerAngle = (Math.PI * 2 * componentIndex) / componentCount;
    const centerX = componentCount === 1 ? 0 : Math.cos(centerAngle) * ringRadius;
    const centerY = componentCount === 1 ? 0 : Math.sin(centerAngle) * ringRadius;

    component.forEach((node, nodeIndex) => {
      const seededNode = byId.get(String(node.id));
      if (!seededNode) return;

      const labelOffset = getNodeLabel(node).charCodeAt(0) || 0;
      const angle = (Math.PI * 2 * nodeIndex) / Math.max(component.length, 1) + labelOffset * 0.07;
      const localRadius = Math.max(35, Math.min(100, component.length * 4.5));
      seededNode.x = centerX + Math.cos(angle) * localRadius;
      seededNode.y = centerY + Math.sin(angle) * localRadius;
    });
  });

  return seeded;
}

function prepareGraph(
  rawGraph: GraphVisualization | null | undefined,
  options: { labelFilter?: string; maxNodes?: number } = {},
): PreparedGraph {
  // 将后端图谱响应转换为 react-force-graph 可直接渲染的数据结构。
  const labelFilter = options.labelFilter || "";
  const maxNodes = options.maxNodes || GRAPH_RENDER_LIMIT;
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
      id: String(link.id || `${getEndpointId(link.source)}-${getEndpointId(link.target)}-${link.label || index}`),
      source: getEndpointId(link.source),
      target: getEndpointId(link.target),
      label: String(link.label || link.rel_type || "RELATED_TO"),
    }))
    .filter((link) => nodeIds.has(getEndpointId(link.source)) && nodeIds.has(getEndpointId(link.target)));

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
}

function getLabelText(label: string, locale: string, fallback: string): string {
  const dictionary = NODE_LABEL_TEXT[locale === "zh-CN" ? "zh-CN" : "en-US"];
  return dictionary[label] || fallback;
}

export default function KnowledgeGraphCanvas({
  graph,
  className = "",
  labelFilter = "",
  selectedNodeId = "",
  onNodeSelect,
  onOpenDetail,
  emptyText,
}: KnowledgeGraphCanvasProps) {
  const { t, i18n } = useTranslation();
  const graphRef = useRef<ForceGraphMethods<GraphCanvasNode, GraphCanvasLink> | undefined>(undefined);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [hoveredNodeId, setHoveredNodeId] = useState("");
  const [showAllLabels, setShowAllLabels] = useState(false);
  const [size, setSize] = useState({ width: 900, height: 560 });

  useEffect(() => {
    if (!containerRef.current) return undefined;
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      setSize({
        width: Math.max(320, Math.floor(width)),
        height: Math.max(360, Math.floor(height)),
      });
    });
    observer.observe(containerRef.current);
    return () => observer.disconnect();
  }, []);

  const prepared = useMemo(() => prepareGraph(graph, { labelFilter }), [graph, labelFilter]);
  const adjacency = useMemo(
    () => buildAdjacency(prepared.graphData.nodes, prepared.graphData.links),
    [prepared.graphData.links, prepared.graphData.nodes],
  );

  const degree = useMemo(() => {
    const result = new Map<string, number>(prepared.graphData.nodes.map((node) => [String(node.id), 0]));
    for (const link of prepared.graphData.links) {
      result.set(getEndpointId(link.source), (result.get(getEndpointId(link.source)) || 0) + 1);
      result.set(getEndpointId(link.target), (result.get(getEndpointId(link.target)) || 0) + 1);
    }
    return result;
  }, [prepared.graphData.links, prepared.graphData.nodes]);

  const selectedId = selectedNodeId ? String(selectedNodeId) : "";
  const focusId = hoveredNodeId || selectedId;
  const focusNeighbors = focusId ? adjacency.get(focusId) || new Set<string>() : new Set<string>();

  useEffect(() => {
    const graphApi = graphRef.current;
    if (!graphApi) return;

    // 调整力导向参数，让医学实体图在较小画布内保持紧凑且可读。
    const chargeForce = graphApi.d3Force("charge") as D3ChargeForce | undefined;
    if (chargeForce?.strength) {
      chargeForce.strength(-95);
    }

    const linkForce = graphApi.d3Force("link") as D3LinkForce | undefined;
    if (linkForce?.distance) {
      linkForce.distance((link: CanvasLink) => {
        const sourceDegree = degree.get(getEndpointId(link.source)) || 1;
        const targetDegree = degree.get(getEndpointId(link.target)) || 1;
        return 56 + Math.min(sourceDegree + targetDegree, 18) * 1.5;
      });
    }

    const collideForce = (forceCollide(14) as unknown as CollideForceFactory).iterations(2) as RegisteredForce;
    const compactXForce = (forceX(0) as unknown as AxisForceFactory).strength(0.055) as RegisteredForce;
    const compactYForce = (forceY(0) as unknown as AxisForceFactory).strength(0.055) as RegisteredForce;
    graphApi.d3Force("collide", collideForce);
    graphApi.d3Force("compact-x", compactXForce);
    graphApi.d3Force("compact-y", compactYForce);
    graphApi.d3ReheatSimulation();

    const fitTimer = window.setTimeout(() => graphApi.zoomToFit?.(650, 24), 350);
    return () => window.clearTimeout(fitTimer);
  }, [degree, prepared.graphData]);

  const shouldShowLabel = (node: CanvasNode): boolean => {
    const nodeId = String(node.id);
    if (showAllLabels) return true;
    if (!focusId) {
      return (degree.get(nodeId) || 0) >= 2 || prepared.graphData.nodes.length <= 45;
    }
    return nodeId === focusId || focusNeighbors.has(nodeId);
  };

  const isDimmed = (nodeId: string): boolean => {
    if (!focusId) return false;
    return nodeId !== focusId && !focusNeighbors.has(nodeId);
  };

  const renderNode = (node: CanvasNode, ctx: CanvasRenderingContext2D, globalScale: number): void => {
    const nodeId = String(node.id);
    const isSelected = selectedId === nodeId;
    const isFocused = focusId === nodeId;
    const dimmed = isDimmed(nodeId);
    const radius = isSelected || isFocused ? 8 : 6;
    const color = getGraphNodeColor(String(node.label || "Entity"));

    ctx.globalAlpha = dimmed ? 0.18 : 0.95;
    ctx.beginPath();
    ctx.arc(node.x || 0, node.y || 0, radius, 0, 2 * Math.PI, false);
    ctx.fillStyle = color;
    ctx.fill();

    if (isSelected || isFocused) {
      ctx.globalAlpha = 1;
      ctx.beginPath();
      ctx.arc(node.x || 0, node.y || 0, radius + 4, 0, 2 * Math.PI, false);
      ctx.strokeStyle = isSelected ? "#f59e0b" : "#0891b2";
      ctx.lineWidth = 2 / globalScale;
      ctx.stroke();
    }

    if (shouldShowLabel(node)) {
      const label = truncateLabel(String(node.name || node.id));
      const fontSize = Math.max(11 / globalScale, 4.5);
      ctx.font = `600 ${fontSize}px sans-serif`;
      const textWidth = ctx.measureText(label).width;
      const x = (node.x || 0) + radius + 5;
      const y = (node.y || 0) + 4;
      ctx.globalAlpha = dimmed ? 0.3 : 0.92;
      ctx.fillStyle = "rgba(255, 255, 255, 0.9)";
      ctx.fillRect(x - 3, y - fontSize, textWidth + 6, fontSize + 5);
      ctx.fillStyle = "#0f172a";
      ctx.fillText(label, x, y);
    }

    ctx.globalAlpha = 1;
  };

  const renderPointerArea = (node: CanvasNode, color: string, ctx: CanvasRenderingContext2D): void => {
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.arc(node.x || 0, node.y || 0, 14, 0, 2 * Math.PI, false);
    ctx.fill();
  };

  const selectedNode = selectedId ? prepared.graphData.nodes.find((node) => String(node.id) === selectedId) || null : null;
  const labelFallback = t("graph.entityLabelFallback");
  const resolvedEmptyText = emptyText || t("graph.noGraph");

  return (
    <div className={`${styles.shell} ${className}`.trim()}>
      <div className={styles.header}>
        <div className={styles.stats}>
          <strong className={styles.statsStrong}>{prepared.renderedNodeCount}</strong>
          <span>
            / {prepared.originalNodeCount} {t("graph.canvas.nodes")}
          </span>
          <span>
            {prepared.renderedLinkCount} / {prepared.originalLinkCount} {t("graph.canvas.relationships")}
          </span>
          {prepared.truncated ? <em className={styles.filtered}>{t("graph.canvas.filtered")}</em> : null}
        </div>

        <div className={styles.tools}>
          <label className={styles.switch}>
            <input type="checkbox" checked={showAllLabels} onChange={(event) => setShowAllLabels(event.target.checked)} />
            <span>{t("graph.canvas.showAllLabels")}</span>
          </label>
          <Button tone="secondary" onClick={() => graphRef.current?.zoomToFit?.(650, 24)}>
            {t("graph.canvas.fitView")}
          </Button>
        </div>
      </div>

      <div className={styles.legend} aria-label={t("graph.canvas.legendLabel")}>
        {Object.entries(NODE_COLORS).map(([label, color]) => (
          <span key={label} className={styles.legendItem}>
            <i className={styles.legendDot} style={{ background: color }} />
            {getLabelText(label, i18n.language, labelFallback)}
          </span>
        ))}
      </div>

      <div className={styles.canvas} ref={containerRef}>
        {prepared.graphData.nodes.length === 0 ? (
          <div className={styles.empty}>{resolvedEmptyText}</div>
        ) : (
          <Suspense fallback={<SkeletonBlock lines={8} title />}>
            <ForceGraphCanvas
              ref={graphRef}
              graphData={prepared.graphData}
              width={size.width}
              height={size.height}
              backgroundColor="#ffffff"
              nodeRelSize={4}
              minZoom={0.25}
              maxZoom={7}
              linkDirectionalArrowLength={3}
              linkDirectionalArrowRelPos={1}
              linkCurvature={0.06}
              cooldownTicks={140}
              d3VelocityDecay={0.34}
              onNodeHover={(node) => setHoveredNodeId(node ? String(node.id) : "")}
              onNodeClick={(node) => onNodeSelect?.(node as GraphCanvasNode)}
              onBackgroundClick={() => onNodeSelect?.(null)}
              showPointerCursor={(obj) => Boolean(obj)}
              nodeLabel={(node) =>
                `${String(node.name || node.id)} (${getLabelText(String(node.label || "Entity"), i18n.language, labelFallback)})`
              }
              linkLabel={(link) => String(link.label || "")}
              linkColor={(link) => {
                if (!focusId) return "rgba(148, 163, 184, 0.45)";
                const source = getEndpointId(link.source);
                const target = getEndpointId(link.target);
                return source === focusId || target === focusId ? "rgba(8, 145, 178, 0.85)" : "rgba(148, 163, 184, 0.12)";
              }}
              linkWidth={(link) => {
                if (!focusId) return 1;
                const source = getEndpointId(link.source);
                const target = getEndpointId(link.target);
                return source === focusId || target === focusId ? 2 : 0.6;
              }}
              nodeCanvasObject={renderNode}
              nodePointerAreaPaint={renderPointerArea}
            />
          </Suspense>
        )}
      </div>

      {selectedId ? (
        <div className={styles.footer}>
          <strong className={styles.footerTitle}>{selectedNode?.name || selectedId}</strong>
          <span className={styles.footerMeta}>
            {getLabelText(String(selectedNode?.label || "Entity"), i18n.language, labelFallback)}
          </span>
          <span className={styles.footerMeta}>ID: {selectedId}</span>
          {onOpenDetail ? (
            <Button tone="secondary" className={styles.detailButton} onClick={() => onOpenDetail(selectedId)}>
              {t("graph.canvas.viewDetail")}
            </Button>
          ) : null}
        </div>
      ) : (
        <div className={styles.footer}>{t("graph.canvas.helpText")}</div>
      )}
    </div>
  );
}
