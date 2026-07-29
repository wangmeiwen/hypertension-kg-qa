import { forceCollide, forceX, forceY } from "d3-force-3d";
import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import { type ForceGraphMethods, type LinkObject, type NodeObject } from "react-force-graph-2d";
import { useTranslation } from "react-i18next";

import styles from "./KnowledgeGraphCanvas.module.css";
import type { GraphVisualization } from "../../../shared/api/contracts";
import { Button, SkeletonBlock } from "../../../shared/ui/primitives";
import {
  buildAdjacency,
  GRAPH_NODE_COLORS,
  getEndpointId,
  getGraphNodeColor,
  getLabelText,
  prepareGraph,
  truncateLabel,
  type GraphCanvasLink,
  type GraphCanvasNode,
} from "../model/graph_canvas_model";

export { DEFAULT_GRAPH_LIMIT, GRAPH_LIMIT_OPTIONS } from "../model/graph_canvas_model";
export type { GraphCanvasLink, GraphCanvasNode } from "../model/graph_canvas_model";

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
type RegisteredForce = NonNullable<
  Parameters<ForceGraphMethods<GraphCanvasNode, GraphCanvasLink>["d3Force"]>[1]
>;
type CollideForceFactory = { iterations: (count: number) => unknown };
type AxisForceFactory = { strength: (value: number) => unknown };

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
  const graphRef = useRef<ForceGraphMethods<GraphCanvasNode, GraphCanvasLink> | undefined>(
    undefined,
  );
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
    const result = new Map<string, number>(
      prepared.graphData.nodes.map((node) => [String(node.id), 0]),
    );
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

    const collideForce = (forceCollide(14) as unknown as CollideForceFactory).iterations(
      2,
    ) as RegisteredForce;
    const compactXForce = (forceX(0) as unknown as AxisForceFactory).strength(
      0.055,
    ) as RegisteredForce;
    const compactYForce = (forceY(0) as unknown as AxisForceFactory).strength(
      0.055,
    ) as RegisteredForce;
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

  const renderNode = (
    node: CanvasNode,
    ctx: CanvasRenderingContext2D,
    globalScale: number,
  ): void => {
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

  const renderPointerArea = (
    node: CanvasNode,
    color: string,
    ctx: CanvasRenderingContext2D,
  ): void => {
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.arc(node.x || 0, node.y || 0, 14, 0, 2 * Math.PI, false);
    ctx.fill();
  };

  const selectedNode = selectedId
    ? prepared.graphData.nodes.find((node) => String(node.id) === selectedId) || null
    : null;
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
            {prepared.renderedLinkCount} / {prepared.originalLinkCount}{" "}
            {t("graph.canvas.relationships")}
          </span>
          {prepared.truncated ? (
            <em className={styles.filtered}>{t("graph.canvas.filtered")}</em>
          ) : null}
        </div>

        <div className={styles.tools}>
          <label className={styles.switch}>
            <input
              type="checkbox"
              checked={showAllLabels}
              onChange={(event) => setShowAllLabels(event.target.checked)}
            />
            <span>{t("graph.canvas.showAllLabels")}</span>
          </label>
          <Button tone="secondary" onClick={() => graphRef.current?.zoomToFit?.(650, 24)}>
            {t("graph.canvas.fitView")}
          </Button>
        </div>
      </div>

      <div className={styles.legend} aria-label={t("graph.canvas.legendLabel")}>
        {Object.entries(GRAPH_NODE_COLORS).map(([label, color]) => (
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
                return source === focusId || target === focusId
                  ? "rgba(8, 145, 178, 0.85)"
                  : "rgba(148, 163, 184, 0.12)";
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
            <Button
              tone="secondary"
              className={styles.detailButton}
              onClick={() => onOpenDetail(selectedId)}
            >
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
