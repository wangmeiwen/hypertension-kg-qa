import { useEffect, useMemo, useRef, useState } from "react";
import ForceGraph2D from "react-force-graph-2d";
import { forceCollide, forceX, forceY } from "d3-force-3d";

export const DEFAULT_GRAPH_LIMIT = 80;
export const GRAPH_LIMIT_OPTIONS = [50, 80, 150, 250];

const GRAPH_RENDER_LIMIT = 160;
const LABEL_KEEP_LENGTH = 12;

const NODE_COLORS = {
  Disease: "#ef4444",
  Symptom: "#f97316",
  Drug: "#22c55e",
  Treatment: "#3b82f6",
  Check: "#8b5cf6",
  Entity: "#6b7280"
};

export function getGraphNodeColor(label) {
  return NODE_COLORS[label] || NODE_COLORS.Entity;
}

function getEndpointId(endpoint) {
  if (endpoint && typeof endpoint === "object") {
    return String(endpoint.id);
  }
  return String(endpoint);
}

function truncateLabel(label) {
  const text = String(label || "");
  return text.length > LABEL_KEEP_LENGTH ? `${text.slice(0, LABEL_KEEP_LENGTH)}...` : text;
}

function getNodeName(node) {
  return node?.name || node?.properties?.name || node?.properties?.title || node?.id || "";
}

function getNodeLabel(node) {
  if (Array.isArray(node?.labels) && node.labels.length > 0) {
    return node.labels[0];
  }
  return node?.label || "Entity";
}

function buildAdjacency(nodes, links) {
  const adjacency = new Map(nodes.map((node) => [String(node.id), new Set()]));
  for (const link of links) {
    const source = getEndpointId(link.source);
    const target = getEndpointId(link.target);
    if (!adjacency.has(source) || !adjacency.has(target)) {
      continue;
    }
    adjacency.get(source).add(target);
    adjacency.get(target).add(source);
  }
  return adjacency;
}

function getConnectedComponents(nodes, links) {
  const adjacency = buildAdjacency(nodes, links);
  const seen = new Set();
  const components = [];

  for (const node of nodes) {
    const id = String(node.id);
    if (seen.has(id)) {
      continue;
    }
    const queue = [id];
    const component = [];
    seen.add(id);
    while (queue.length > 0) {
      const nextId = queue.shift();
      const nextNode = nodes.find((item) => String(item.id) === nextId);
      if (nextNode) {
        component.push(nextNode);
      }
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

function filterByLabelNeighborhood(nodes, links, labelFilter) {
  if (!labelFilter) {
    return { nodes, links };
  }

  const matchingIds = new Set(
    nodes.filter((node) => getNodeLabel(node) === labelFilter).map((node) => String(node.id))
  );
  if (matchingIds.size === 0) {
    return { nodes: [], links: [] };
  }

  const keptIds = new Set(matchingIds);
  for (const link of links) {
    const source = getEndpointId(link.source);
    const target = getEndpointId(link.target);
    if (matchingIds.has(source)) {
      keptIds.add(target);
    }
    if (matchingIds.has(target)) {
      keptIds.add(source);
    }
  }

  return {
    nodes: nodes.filter((node) => keptIds.has(String(node.id))),
    links: links.filter((link) => keptIds.has(getEndpointId(link.source)) && keptIds.has(getEndpointId(link.target)))
  };
}

function sampleGraph(nodes, links, maxNodes) {
  if (nodes.length <= maxNodes) {
    return { nodes, links, truncated: false };
  }

  const degree = new Map(nodes.map((node) => [String(node.id), 0]));
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
    })
  );

  const selected = [];
  const selectedIds = new Set();
  let cursor = 0;
  while (selected.length < maxNodes && components.some((component) => cursor < component.length)) {
    for (const component of components) {
      const node = component[cursor];
      if (node && !selectedIds.has(String(node.id))) {
        selected.push(node);
        selectedIds.add(String(node.id));
        if (selected.length >= maxNodes) {
          break;
        }
      }
    }
    cursor += 1;
  }

  return {
    nodes: selected,
    links: links.filter((link) => selectedIds.has(getEndpointId(link.source)) && selectedIds.has(getEndpointId(link.target))),
    truncated: true
  };
}

function seedNodePositions(nodes, links) {
  const components = getConnectedComponents(nodes, links);
  const componentCount = Math.max(components.length, 1);
  const ringRadius = Math.max(85, Math.min(220, componentCount * 34));
  const seeded = nodes.map((node) => ({ ...node }));
  const byId = new Map(seeded.map((node) => [String(node.id), node]));

  components.forEach((component, componentIndex) => {
    const centerAngle = (Math.PI * 2 * componentIndex) / componentCount;
    const centerX = componentCount === 1 ? 0 : Math.cos(centerAngle) * ringRadius;
    const centerY = componentCount === 1 ? 0 : Math.sin(centerAngle) * ringRadius;

    component.forEach((node, nodeIndex) => {
      const seededNode = byId.get(String(node.id));
      if (!seededNode) {
        return;
      }
      const labelOffset = getNodeLabel(node).charCodeAt(0) || 0;
      const angle = (Math.PI * 2 * nodeIndex) / Math.max(component.length, 1) + labelOffset * 0.07;
      const localRadius = Math.max(35, Math.min(100, component.length * 4.5));
      seededNode.x = centerX + Math.cos(angle) * localRadius;
      seededNode.y = centerY + Math.sin(angle) * localRadius;
    });
  });

  return seeded;
}

function prepareGraph(rawGraph, { labelFilter = "", maxNodes = GRAPH_RENDER_LIMIT } = {}) {
  const rawNodes = Array.isArray(rawGraph?.nodes) ? rawGraph.nodes : [];
  const rawLinks = Array.isArray(rawGraph?.links) ? rawGraph.links : [];
  const nodes = rawNodes.map((node) => ({
    ...node,
    id: String(node.id),
    name: getNodeName(node),
    label: getNodeLabel(node)
  }));
  const nodeIds = new Set(nodes.map((node) => String(node.id)));
  const links = rawLinks
    .map((link, index) => ({
      ...link,
      id: link.id || `${getEndpointId(link.source)}-${getEndpointId(link.target)}-${link.label || index}`,
      source: getEndpointId(link.source),
      target: getEndpointId(link.target),
      label: link.label || link.rel_type || "RELATED_TO"
    }))
    .filter((link) => nodeIds.has(link.source) && nodeIds.has(link.target));

  const filtered = filterByLabelNeighborhood(nodes, links, labelFilter);
  const sampled = sampleGraph(filtered.nodes, filtered.links, maxNodes);
  const seededNodes = seedNodePositions(sampled.nodes, sampled.links);

  return {
    graphData: { nodes: seededNodes, links: sampled.links },
    originalNodeCount: rawNodes.length,
    originalLinkCount: rawLinks.length,
    renderedNodeCount: seededNodes.length,
    renderedLinkCount: sampled.links.length,
    truncated: sampled.truncated || filtered.nodes.length !== rawNodes.length
  };
}

export default function KnowledgeGraphCanvas({
  graph,
  className = "",
  labelFilter = "",
  selectedNodeId = "",
  onNodeSelect,
  onOpenDetail,
  emptyText = "暂无图谱数据"
}) {
  const graphRef = useRef(null);
  const containerRef = useRef(null);
  const [hoveredNodeId, setHoveredNodeId] = useState("");
  const [showAllLabels, setShowAllLabels] = useState(false);
  const [size, setSize] = useState({ width: 900, height: 560 });

  useEffect(() => {
    if (!containerRef.current) {
      return undefined;
    }
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      setSize({
        width: Math.max(320, Math.floor(width)),
        height: Math.max(360, Math.floor(height))
      });
    });
    observer.observe(containerRef.current);
    return () => observer.disconnect();
  }, []);

  const prepared = useMemo(() => prepareGraph(graph, { labelFilter }), [graph, labelFilter]);

  const adjacency = useMemo(
    () => buildAdjacency(prepared.graphData.nodes, prepared.graphData.links),
    [prepared.graphData.links, prepared.graphData.nodes]
  );

  const degree = useMemo(() => {
    const result = new Map(prepared.graphData.nodes.map((node) => [String(node.id), 0]));
    for (const link of prepared.graphData.links) {
      result.set(getEndpointId(link.source), (result.get(getEndpointId(link.source)) || 0) + 1);
      result.set(getEndpointId(link.target), (result.get(getEndpointId(link.target)) || 0) + 1);
    }
    return result;
  }, [prepared.graphData.links, prepared.graphData.nodes]);

  const selectedId = selectedNodeId ? String(selectedNodeId) : "";
  const focusId = hoveredNodeId || selectedId;
  const focusNeighbors = focusId ? adjacency.get(focusId) || new Set() : new Set();

  useEffect(() => {
    const graphApi = graphRef.current;
    if (!graphApi) {
      return;
    }
    const chargeForce = graphApi.d3Force("charge");
    if (chargeForce?.strength) {
      chargeForce.strength(-95);
    }
    const linkForce = graphApi.d3Force("link");
    if (linkForce?.distance) {
      linkForce.distance((link) => {
        const sourceDegree = degree.get(getEndpointId(link.source)) || 1;
        const targetDegree = degree.get(getEndpointId(link.target)) || 1;
        return 56 + Math.min(sourceDegree + targetDegree, 18) * 1.5;
      });
    }
    graphApi.d3Force("collide", forceCollide((node) => 12 + Math.min(degree.get(String(node.id)) || 0, 6)).iterations(2));
    graphApi.d3Force("compact-x", forceX(0).strength(0.055));
    graphApi.d3Force("compact-y", forceY(0).strength(0.055));
    graphApi.d3ReheatSimulation();
    const fitTimer = window.setTimeout(() => graphApi.zoomToFit?.(650, 24), 350);
    return () => window.clearTimeout(fitTimer);
  }, [degree, prepared.graphData]);

  const handleFitView = () => {
    graphRef.current?.zoomToFit?.(650, 24);
  };

  const shouldShowLabel = (node) => {
    const nodeId = String(node.id);
    if (showAllLabels) {
      return true;
    }
    if (!focusId) {
      return (degree.get(nodeId) || 0) >= 2 || prepared.graphData.nodes.length <= 45;
    }
    return nodeId === focusId || focusNeighbors.has(nodeId);
  };

  const isDimmed = (nodeId) => {
    if (!focusId) {
      return false;
    }
    return nodeId !== focusId && !focusNeighbors.has(nodeId);
  };

  const renderNode = (node, ctx, globalScale) => {
    const nodeId = String(node.id);
    const isSelected = selectedId === nodeId;
    const isFocused = focusId === nodeId;
    const dimmed = isDimmed(nodeId);
    const radius = isSelected || isFocused ? 8 : 6;
    const color = getGraphNodeColor(node.label);

    ctx.globalAlpha = dimmed ? 0.18 : 0.95;
    ctx.beginPath();
    ctx.arc(node.x, node.y, radius, 0, 2 * Math.PI, false);
    ctx.fillStyle = color;
    ctx.fill();

    if (isSelected || isFocused) {
      ctx.globalAlpha = 1;
      ctx.beginPath();
      ctx.arc(node.x, node.y, radius + 4, 0, 2 * Math.PI, false);
      ctx.strokeStyle = isSelected ? "#f59e0b" : "#0891b2";
      ctx.lineWidth = 2 / globalScale;
      ctx.stroke();
    }

    if (shouldShowLabel(node)) {
      const label = truncateLabel(node.name || node.id);
      const fontSize = Math.max(11 / globalScale, 4.5);
      ctx.font = `600 ${fontSize}px sans-serif`;
      const textWidth = ctx.measureText(label).width;
      const x = node.x + radius + 5;
      const y = node.y + 4;
      ctx.globalAlpha = dimmed ? 0.3 : 0.92;
      ctx.fillStyle = "rgba(255, 255, 255, 0.9)";
      ctx.fillRect(x - 3, y - fontSize, textWidth + 6, fontSize + 5);
      ctx.fillStyle = "#0f172a";
      ctx.fillText(label, x, y);
    }
    ctx.globalAlpha = 1;
  };

  const renderPointerArea = (node, color, ctx) => {
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.arc(node.x, node.y, 14, 0, 2 * Math.PI, false);
    ctx.fill();
  };

  const selectedNode = selectedId
    ? prepared.graphData.nodes.find((node) => String(node.id) === selectedId)
    : null;

  return (
    <div className={`kgCanvasShell ${className}`}>
      <div className="kgCanvasHeader">
        <div className="kgCanvasStats">
          <strong>{prepared.renderedNodeCount}</strong> / {prepared.originalNodeCount} 个节点
          <span>{prepared.renderedLinkCount} / {prepared.originalLinkCount} 条关系</span>
          {prepared.truncated ? <em>已为可读性裁剪/聚焦</em> : null}
        </div>
        <div className="kgCanvasTools">
          <label className="kgSwitch">
            <input
              type="checkbox"
              checked={showAllLabels}
              onChange={(event) => setShowAllLabels(event.target.checked)}
            />
            <span>显示全部标签</span>
          </label>
          <button type="button" onClick={handleFitView}>
            适配视图
          </button>
        </div>
      </div>

      <div className="kgLegend" aria-label="图谱节点类型图例">
        {Object.entries(NODE_COLORS).map(([label, color]) => (
          <span key={label}>
            <i style={{ background: color }} />
            {label}
          </span>
        ))}
      </div>

      <div className="kgCanvas" ref={containerRef}>
        {prepared.graphData.nodes.length === 0 ? (
          <div className="kgEmpty">{emptyText}</div>
        ) : (
          <ForceGraph2D
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
            onNodeClick={(node) => onNodeSelect?.(node)}
            onBackgroundClick={() => onNodeSelect?.(null)}
            showPointerCursor={(obj) => Boolean(obj)}
            nodeLabel={(node) => `${node.name || node.id} (${node.label || "Entity"})`}
            linkLabel={(link) => link.label}
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
        )}
      </div>

      {selectedId ? (
        <div className="kgSelectedMeta">
          <strong>{selectedNode?.name || selectedId}</strong>
          <span>{selectedNode?.label || "Entity"}</span>
          <span>ID: {selectedId}</span>
          {onOpenDetail ? (
            <button type="button" className="view-detail-btn" onClick={() => onOpenDetail(selectedId)}>
              查看详情
            </button>
          ) : null}
        </div>
      ) : (
        <div className="kgHelpText">提示：悬停节点查看一跳邻居，点击节点固定选择，滚轮可缩放画布。</div>
      )}
    </div>
  );
}
