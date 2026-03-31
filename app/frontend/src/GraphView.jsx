import { useEffect, useMemo, useState } from "react";
import ForceGraph2D from "react-force-graph-2d";
import { fetchGraph } from "./api";
import EntityDetailModal from "./EntityDetailModal";

export default function GraphView() {
  const [keyword, setKeyword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [graph, setGraph] = useState({ nodes: [], links: [] });
  const [selected, setSelected] = useState(null);
  const [showDetail, setShowDetail] = useState(false);
  const [selectedEntityId, setSelectedEntityId] = useState(null);

  const loadGraph = async (nextKeyword = "") => {
    setLoading(true);
    setError("");
    try {
      const data = await fetchGraph(nextKeyword, 250);
      setGraph({ nodes: data.nodes || [], links: data.links || [] });
      setSelected(null);
    } catch (e) {
      setError(String(e));
      setGraph({ nodes: [], links: [] });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadGraph("");
  }, []);

  const stats = useMemo(() => {
    return `${graph.nodes.length} 个节点 / ${graph.links.length} 条关系`;
  }, [graph.links.length, graph.nodes.length]);

  const handleNodeClick = (node) => {
    setSelected(node);
    setSelectedEntityId(node.id);
    setShowDetail(true);
  };

  const handleCloseDetail = () => {
    setShowDetail(false);
    setSelectedEntityId(null);
  };

  const getNodeColor = (label) => {
    const colors = {
      "Disease": "#ef4444",
      "Symptom": "#f97316",
      "Drug": "#22c55e",
      "Treatment": "#3b82f6",
      "Check": "#8b5cf6",
      "Entity": "#6b7280"
    };
    return colors[label] || "#6b7280";
  };

  return (
    <section className="graphPanel">
      <div className="graphToolbar">
        <input
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          placeholder="输入实体关键词过滤图谱，如：高血压、氯沙坦"
          disabled={loading}
        />
        <button type="button" onClick={() => loadGraph(keyword)} disabled={loading}>
          {loading ? "加载中..." : "查询图谱"}
        </button>
        <span className="graphStats">{stats}</span>
      </div>
      {error ? <div className="graphError">{error}</div> : null}
      <div className="graphCanvas">
        <ForceGraph2D
          graphData={graph}
          nodeAutoColorBy="label"
          linkDirectionalArrowLength={4}
          linkDirectionalArrowRelPos={1}
          linkCurvature={0.15}
          cooldownTicks={100}
          onNodeClick={handleNodeClick}
          nodeLabel={(node) => `${node.name} (${node.label})`}
          linkLabel={(link) => link.label}
          nodeCanvasObject={(node, ctx, globalScale) => {
            const label = node.name || node.id;
            const fontSize = 12 / globalScale;
            const nodeColor = getNodeColor(node.label);
            
            // 绘制节点圆形
            ctx.beginPath();
            ctx.arc(node.x, node.y, 6, 0, 2 * Math.PI, false);
            ctx.fillStyle = nodeColor;
            ctx.fill();
            
            // 绘制选中效果
            if (selected && selected.id === node.id) {
              ctx.beginPath();
              ctx.arc(node.x, node.y, 9, 0, 2 * Math.PI, false);
              ctx.strokeStyle = "#fbbf24";
              ctx.lineWidth = 2;
              ctx.stroke();
            }
            
            // 绘制文字标签
            ctx.font = `${fontSize}px Sans-Serif`;
            ctx.fillStyle = "#111827";
            ctx.fillText(label, node.x + 8, node.y + 4);
          }}
        />
      </div>
      {selected ? (
        <div className="graphMeta">
          <strong>{selected.name}</strong>
          <span style={{ color: getNodeColor(selected.label) }}>{selected.label}</span>
          <span>ID: {selected.id}</span>
          <button 
            className="view-detail-btn"
            onClick={() => {
              setSelectedEntityId(selected.id);
              setShowDetail(true);
            }}
          >
            查看详情
          </button>
        </div>
      ) : null}

      {/* 实体详情弹窗 */}
      {showDetail && (
        <EntityDetailModal 
          entityId={selectedEntityId} 
          onClose={handleCloseDetail} 
        />
      )}
    </section>
  );
}
