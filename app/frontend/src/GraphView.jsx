import { useEffect, useMemo, useState } from "react";
import { fetchGraph } from "./api";
import EntityDetailModal from "./EntityDetailModal";
import KnowledgeGraphCanvas, { DEFAULT_GRAPH_LIMIT, GRAPH_LIMIT_OPTIONS } from "./KnowledgeGraphCanvas";

export default function GraphView() {
  const [keyword, setKeyword] = useState("");
  const [limit, setLimit] = useState(DEFAULT_GRAPH_LIMIT);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [graph, setGraph] = useState({ nodes: [], links: [] });
  const [selected, setSelected] = useState(null);
  const [showDetail, setShowDetail] = useState(false);
  const [selectedEntityId, setSelectedEntityId] = useState(null);

  const loadGraph = async (nextKeyword = keyword, nextLimit = limit) => {
    setLoading(true);
    setError("");
    try {
      const data = await fetchGraph(nextKeyword, nextLimit);
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
    loadGraph("", DEFAULT_GRAPH_LIMIT);
  }, []);

  const stats = useMemo(() => {
    return `接口返回 ${graph.nodes.length} 个节点 / ${graph.links.length} 条关系`;
  }, [graph.links.length, graph.nodes.length]);

  const handleLimitChange = (event) => {
    const nextLimit = Number(event.target.value);
    setLimit(nextLimit);
    loadGraph(keyword, nextLimit);
  };

  const handleOpenDetail = (entityId) => {
    setSelectedEntityId(entityId);
    setShowDetail(true);
  };

  const handleCloseDetail = () => {
    setShowDetail(false);
    setSelectedEntityId(null);
  };

  return (
    <section className="graphPanel">
      <div className="graphToolbar graphToolbarEnhanced">
        <input
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          placeholder="输入实体关键词过滤图谱，如：高血压、氯沙坦"
          disabled={loading}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              loadGraph(keyword, limit);
            }
          }}
        />
        <select value={limit} onChange={handleLimitChange} disabled={loading} aria-label="图谱返回数量">
          {GRAPH_LIMIT_OPTIONS.map((option) => (
            <option key={option} value={option}>
              {option} 条
            </option>
          ))}
        </select>
        <button type="button" onClick={() => loadGraph(keyword, limit)} disabled={loading}>
          {loading ? "加载中..." : "查询图谱"}
        </button>
        <span className="graphStats">{stats}</span>
      </div>
      {error ? <div className="graphError">{error}</div> : null}

      <KnowledgeGraphCanvas
        graph={graph}
        selectedNodeId={selected?.id}
        onNodeSelect={(node) => setSelected(node)}
        onOpenDetail={handleOpenDetail}
      />

      {showDetail && <EntityDetailModal entityId={selectedEntityId} onClose={handleCloseDetail} />}
    </section>
  );
}
