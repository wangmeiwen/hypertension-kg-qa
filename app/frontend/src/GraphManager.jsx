import React, { useState, useEffect } from "react";
import {
  getGraphLabels,
  getGraphRelationshipTypes,
  getGraphNodes,
  getGraphNode,
  fetchGraph,
  createGraphNode,
  updateGraphNode,
  deleteGraphNode,
  createGraphRelationship,
  deleteGraphRelationship,
  executeCypherQuery,
  extractAndImportGraph,
  embedGraphToMilvus,
  getGraphEmbedStatus
} from "./api";
import KnowledgeGraphCanvas, { DEFAULT_GRAPH_LIMIT, GRAPH_LIMIT_OPTIONS } from "./KnowledgeGraphCanvas";

export default function GraphManager() {
  // 状态管理
  const [labels, setLabels] = useState([]);
  const [relTypes, setRelTypes] = useState([]);
  const [nodes, setNodes] = useState([]);
  const [totalNodes, setTotalNodes] = useState(0);
  const [selectedNode, setSelectedNode] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [graphPreview, setGraphPreview] = useState({ nodes: [], links: [] });
  const [graphPreviewLoading, setGraphPreviewLoading] = useState(false);
  const [graphLimit, setGraphLimit] = useState(DEFAULT_GRAPH_LIMIT);
  const [selectedPreviewNode, setSelectedPreviewNode] = useState(null);

  // 筛选条件
  const [selectedLabel, setSelectedLabel] = useState("");
  const [searchKeyword, setSearchKeyword] = useState("");
  const [currentPage, setCurrentPage] = useState(0);
  const pageSize = 20;

  // 模态框状态
  const [showNodeModal, setShowNodeModal] = useState(false);
  const [showRelModal, setShowRelModal] = useState(false);
  const [showCypherModal, setShowCypherModal] = useState(false);
  const [showEmbedModal, setShowEmbedModal] = useState(false);
  const [showImportModal, setShowImportModal] = useState(false);
  const [editingNode, setEditingNode] = useState(null);

  // 表单数据
  const [nodeForm, setNodeForm] = useState({ label: "", properties: {} });
  const [relForm, setRelForm] = useState({ sourceId: "", targetId: "", relType: "", properties: {} });
  const [cypherQuery, setCypherQuery] = useState("");
  const [cypherResult, setCypherResult] = useState(null);

  // 嵌入状态
  const [embedStatus, setEmbedStatus] = useState(null);
  const [embedLoading, setEmbedLoading] = useState(false);
  const [selectedLabelsForEmbed, setSelectedLabelsForEmbed] = useState([]);
  const [embedResult, setEmbedResult] = useState(null);
  const [importLoading, setImportLoading] = useState(false);
  const [selectedImportFile, setSelectedImportFile] = useState(null);
  const [importMaxChars, setImportMaxChars] = useState(12000);
  const [importResult, setImportResult] = useState(null);

  // 初始化加载
  useEffect(() => {
    loadLabels();
    loadRelTypes();
    loadNodes();
    loadGraphPreview("", DEFAULT_GRAPH_LIMIT);
  }, []);

  // 加载节点标签
  async function loadLabels() {
    try {
      const data = await getGraphLabels();
      setLabels(data);
    } catch (err) {
      setError(err.message);
    }
  }

  // 加载关系类型
  async function loadRelTypes() {
    try {
      const data = await getGraphRelationshipTypes();
      setRelTypes(data);
    } catch (err) {
      setError(err.message);
    }
  }

  // 加载节点列表
  async function loadNodes() {
    setLoading(true);
    setError("");
    try {
      const result = await getGraphNodes({
        label: selectedLabel,
        keyword: searchKeyword,
        limit: pageSize,
        offset: currentPage * pageSize
      });
      setNodes(result.data);
      setTotalNodes(result.total);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function loadGraphPreview(nextKeyword = searchKeyword, nextLimit = graphLimit) {
    setGraphPreviewLoading(true);
    try {
      const data = await fetchGraph(nextKeyword, nextLimit);
      setGraphPreview({ nodes: data.nodes || [], links: data.links || [] });
      setSelectedPreviewNode(null);
    } catch (err) {
      setError(err.message);
      setGraphPreview({ nodes: [], links: [] });
    } finally {
      setGraphPreviewLoading(false);
    }
  }

  // 筛选条件变化时重新加载
  useEffect(() => {
    setCurrentPage(0);
    loadNodes();
    loadGraphPreview(searchKeyword, graphLimit);
  }, [selectedLabel, searchKeyword]);

  // 页码变化时重新加载
  useEffect(() => {
    loadNodes();
  }, [currentPage]);

  // 查看节点详情
  async function handleViewNode(nodeId) {
    setLoading(true);
    try {
      const data = await getGraphNode(nodeId);
      setSelectedNode(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  // 创建节点
  async function handleCreateNode(e) {
    e.preventDefault();
    setLoading(true);
    try {
      await createGraphNode(nodeForm.label, nodeForm.properties);
      setSuccess("节点创建成功");
      setShowNodeModal(false);
      setNodeForm({ label: "", properties: {} });
      loadNodes();
      loadGraphPreview(searchKeyword, graphLimit);
      setTimeout(() => setSuccess(""), 3000);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  // 更新节点
  async function handleUpdateNode(e) {
    e.preventDefault();
    setLoading(true);
    try {
      await updateGraphNode(editingNode.id, editingNode.properties);
      setSuccess("节点更新成功");
      setShowNodeModal(false);
      setEditingNode(null);
      loadNodes();
      loadGraphPreview(searchKeyword, graphLimit);
      if (selectedNode && selectedNode.node.id === editingNode.id) {
        handleViewNode(editingNode.id);
      }
      setTimeout(() => setSuccess(""), 3000);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  // 删除节点
  async function handleDeleteNode(nodeId) {
    if (!window.confirm("确定要删除此节点及其所有关系吗？此操作不可恢复。")) {
      return;
    }
    setLoading(true);
    try {
      await deleteGraphNode(nodeId);
      setSuccess("节点删除成功");
      setSelectedNode(null);
      loadNodes();
      loadGraphPreview(searchKeyword, graphLimit);
      setTimeout(() => setSuccess(""), 3000);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  // 创建关系
  async function handleCreateRel(e) {
    e.preventDefault();
    setLoading(true);
    try {
      await createGraphRelationship(
        parseInt(relForm.sourceId),
        parseInt(relForm.targetId),
        relForm.relType,
        relForm.properties
      );
      setSuccess("关系创建成功");
      setShowRelModal(false);
      setRelForm({ sourceId: "", targetId: "", relType: "", properties: {} });
      if (selectedNode) {
        handleViewNode(selectedNode.node.id);
      }
      loadGraphPreview(searchKeyword, graphLimit);
      setTimeout(() => setSuccess(""), 3000);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  // 删除关系
  async function handleDeleteRel(relId) {
    if (!window.confirm("确定要删除此关系吗？")) {
      return;
    }
    setLoading(true);
    try {
      await deleteGraphRelationship(relId);
      setSuccess("关系删除成功");
      if (selectedNode) {
        handleViewNode(selectedNode.node.id);
      }
      loadGraphPreview(searchKeyword, graphLimit);
      setTimeout(() => setSuccess(""), 3000);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  // 执行 Cypher 查询
  async function handleExecuteCypher(e) {
    e.preventDefault();
    setLoading(true);
    try {
      const result = await executeCypherQuery(cypherQuery);
      setCypherResult(result);
    } catch (err) {
      setError(err.message);
      setCypherResult(null);
    } finally {
      setLoading(false);
    }
  }

  // 加载嵌入状态
  async function loadEmbedStatus() {
    try {
      const data = await getGraphEmbedStatus();
      setEmbedStatus(data);
    } catch (err) {
      console.error("加载嵌入状态失败:", err);
    }
  }

  // 打开嵌入模态框
  function openEmbedModal() {
    loadEmbedStatus();
    setSelectedLabelsForEmbed([]);
    setEmbedResult(null);
    setShowEmbedModal(true);
  }

  // 执行嵌入
  async function handleEmbedToMilvus() {
    if (!window.confirm("确定要将知识图谱嵌入到 Milvus 吗？这可能需要一些时间。")) {
      return;
    }
    setEmbedLoading(true);
    setError("");
    try {
      const labelsToEmbed = selectedLabelsForEmbed.length > 0 ? selectedLabelsForEmbed : null;
      const result = await embedGraphToMilvus(labelsToEmbed, 100);
      setEmbedResult(result);
      setSuccess("知识图谱嵌入成功！");
      loadEmbedStatus();
      setTimeout(() => setSuccess(""), 5000);
    } catch (err) {
      setError(err.message);
    } finally {
      setEmbedLoading(false);
    }
  }

  // 切换标签选择
  function toggleLabelForEmbed(label) {
    setSelectedLabelsForEmbed(prev => {
      if (prev.includes(label)) {
        return prev.filter(l => l !== label);
      } else {
        return [...prev, label];
      }
    });
  }

  function openImportModal() {
    setSelectedImportFile(null);
    setImportResult(null);
    setShowImportModal(true);
  }

  function handleImportFileChange(e) {
    const file = e.target.files[0];
    setSelectedImportFile(file || null);
  }

  async function handleExtractAndImport(e) {
    e.preventDefault();
    if (!selectedImportFile) {
      setError("请先选择文件");
      return;
    }
    setImportLoading(true);
    setError("");
    try {
      const result = await extractAndImportGraph(selectedImportFile, importMaxChars);
      setImportResult(result);
      setSuccess("文件抽取并写入图谱成功");
      await Promise.all([loadLabels(), loadRelTypes(), loadNodes()]);
      await loadGraphPreview(searchKeyword, graphLimit);
      setTimeout(() => setSuccess(""), 5000);
    } catch (err) {
      setError(err.message);
    } finally {
      setImportLoading(false);
    }
  }

  // 打开编辑节点模态框
  function openEditNode(node) {
    setEditingNode({
      id: node.id,
      properties: { ...node.properties }
    });
    setShowNodeModal(true);
  }

  // 添加属性字段
  function addProperty(formType) {
    const key = window.prompt("请输入属性名:");
    if (key) {
      if (formType === "node") {
        setNodeForm(prev => ({
          ...prev,
          properties: { ...prev.properties, [key]: "" }
        }));
      } else if (formType === "edit") {
        setEditingNode(prev => ({
          ...prev,
          properties: { ...prev.properties, [key]: "" }
        }));
      } else if (formType === "rel") {
        setRelForm(prev => ({
          ...prev,
          properties: { ...prev.properties, [key]: "" }
        }));
      }
    }
  }

  // 删除属性字段
  function removeProperty(formType, key) {
    if (formType === "node") {
      setNodeForm(prev => {
        const newProps = { ...prev.properties };
        delete newProps[key];
        return { ...prev, properties: newProps };
      });
    } else if (formType === "edit") {
      setEditingNode(prev => {
        const newProps = { ...prev.properties };
        delete newProps[key];
        return { ...prev, properties: newProps };
      });
    } else if (formType === "rel") {
      setRelForm(prev => {
        const newProps = { ...prev.properties };
        delete newProps[key];
        return { ...prev, properties: newProps };
      });
    }
  }

  const totalPages = Math.ceil(totalNodes / pageSize);

  function handleGraphLimitChange(event) {
    const nextLimit = Number(event.target.value);
    setGraphLimit(nextLimit);
    loadGraphPreview(searchKeyword, nextLimit);
  }

  return (
    <div className="graph-manager">
      {/* 消息提示 */}
      {error && <div className="alert alert-error">{error}</div>}
      {success && <div className="alert alert-success">{success}</div>}

      {/* 工具栏 */}
      <div className="graph-toolbar">
        <div className="graph-filters">
          <select
            value={selectedLabel}
            onChange={(e) => setSelectedLabel(e.target.value)}
            className="graph-select"
          >
            <option value="">所有标签</option>
            {labels.map(label => (
              <option key={label} value={label}>{label}</option>
            ))}
          </select>
          <input
            type="text"
            placeholder="搜索节点名称..."
            value={searchKeyword}
            onChange={(e) => setSearchKeyword(e.target.value)}
            className="graph-search-input"
          />
          <button onClick={loadNodes} className="btn btn-secondary" disabled={loading}>
            刷新
          </button>
        </div>
        <div className="graph-actions">
          <button
            onClick={() => setShowNodeModal(true)}
            className="btn btn-primary"
          >
            + 创建节点
          </button>
          <button
            onClick={() => setShowRelModal(true)}
            className="btn btn-secondary"
          >
            + 创建关系
          </button>
          <button
            onClick={() => setShowCypherModal(true)}
            className="btn btn-info"
          >
            Cypher查询
          </button>
          <button
            onClick={openEmbedModal}
            className="btn btn-success"
          >
            嵌入Milvus
          </button>
          <button
            onClick={openImportModal}
            className="btn btn-primary"
          >
            文件抽取入图谱
          </button>
        </div>
      </div>

      <div className="graph-preview-section">
        <div className="panel-header graph-preview-header">
          <div>
            <h3>图谱预览</h3>
            <p>复用当前搜索词和标签筛选，默认展示多个分散关系簇，避免全量节点挤在一起。</p>
          </div>
          <div className="graph-preview-controls">
            <select
              value={graphLimit}
              onChange={handleGraphLimitChange}
              className="graph-select"
              disabled={graphPreviewLoading}
              aria-label="后台图谱预览返回数量"
            >
              {GRAPH_LIMIT_OPTIONS.map((option) => (
                <option key={option} value={option}>
                  {option} 条
                </option>
              ))}
            </select>
            <button
              type="button"
              onClick={() => loadGraphPreview(searchKeyword, graphLimit)}
              className="btn btn-secondary"
              disabled={graphPreviewLoading}
            >
              {graphPreviewLoading ? "加载中..." : "刷新预览"}
            </button>
          </div>
        </div>
        <KnowledgeGraphCanvas
          graph={graphPreview}
          className="kgCanvasCompact"
          labelFilter={selectedLabel}
          selectedNodeId={selectedPreviewNode?.id}
          onNodeSelect={(node) => setSelectedPreviewNode(node)}
          onOpenDetail={(entityId) => handleViewNode(entityId)}
          emptyText={graphPreviewLoading ? "图谱加载中..." : "暂无可预览的图谱数据"}
        />
      </div>

      {/* 主内容区 */}
      <div className="graph-content">
        {/* 节点列表 */}
        <div className="graph-nodes-panel">
          <div className="panel-header">
            <h3>节点列表 ({totalNodes})</h3>
          </div>
          <div className="nodes-table-wrapper">
            <table className="nodes-table">
              <thead>
                <tr>
                  <th>ID</th>
                  <th>标签</th>
                  <th>名称</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {nodes.map(node => (
                  <tr
                    key={node.id}
                    className={selectedNode?.node?.id === node.id ? "selected" : ""}
                    onClick={() => handleViewNode(node.id)}
                  >
                    <td>{node.id}</td>
                    <td>{node.labels?.join(", ")}</td>
                    <td>{node.properties?.name || node.properties?.title || "-"}</td>
                    <td>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          openEditNode(node);
                        }}
                        className="btn btn-sm btn-edit"
                      >
                        编辑
                      </button>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          handleDeleteNode(node.id);
                        }}
                        className="btn btn-sm btn-danger"
                      >
                        删除
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {/* 分页 */}
          {totalPages > 1 && (
            <div className="pagination">
              <button
                onClick={() => setCurrentPage(p => Math.max(0, p - 1))}
                disabled={currentPage === 0}
                className="btn btn-sm"
              >
                上一页
              </button>
              <span className="page-info">
                第 {currentPage + 1} / {totalPages} 页
              </span>
              <button
                onClick={() => setCurrentPage(p => Math.min(totalPages - 1, p + 1))}
                disabled={currentPage >= totalPages - 1}
                className="btn btn-sm"
              >
                下一页
              </button>
            </div>
          )}
        </div>

        {/* 节点详情 */}
        {selectedNode && (
          <div className="graph-detail-panel">
            <div className="panel-header">
              <h3>节点详情</h3>
              <button
                onClick={() => setSelectedNode(null)}
                className="btn-close"
              >
                ×
              </button>
            </div>
            <div className="detail-content">
              <div className="detail-section">
                <h4>基本信息</h4>
                <div className="detail-item">
                  <label>ID:</label>
                  <span>{selectedNode.node.id}</span>
                </div>
                <div className="detail-item">
                  <label>标签:</label>
                  <span>{selectedNode.node.labels?.join(", ")}</span>
                </div>
                {Object.entries(selectedNode.node.properties).map(([key, value]) => (
                  <div className="detail-item" key={key}>
                    <label>{key}:</label>
                    <span>{typeof value === "object" ? JSON.stringify(value) : String(value)}</span>
                  </div>
                ))}
              </div>

              <div className="detail-section">
                <h4>入边关系 ({selectedNode.relationships.incoming.length})</h4>
                {selectedNode.relationships.incoming.length === 0 ? (
                  <p className="no-data">无入边关系</p>
                ) : (
                  <ul className="rel-list">
                    {selectedNode.relationships.incoming.map((rel, idx) => (
                      <li key={idx} className="rel-item">
                        <span className="rel-source">{rel.source_name}</span>
                        <span className="rel-type">→ {rel.rel_type} →</span>
                        <span className="rel-target">当前节点</span>
                        <button
                          onClick={() => handleDeleteRel(rel.rel_id)}
                          className="btn btn-xs btn-danger"
                        >
                          删除
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <div className="detail-section">
                <h4>出边关系 ({selectedNode.relationships.outgoing.length})</h4>
                {selectedNode.relationships.outgoing.length === 0 ? (
                  <p className="no-data">无出边关系</p>
                ) : (
                  <ul className="rel-list">
                    {selectedNode.relationships.outgoing.map((rel, idx) => (
                      <li key={idx} className="rel-item">
                        <span className="rel-source">当前节点</span>
                        <span className="rel-type">→ {rel.rel_type} →</span>
                        <span className="rel-target">{rel.target_name}</span>
                        <button
                          onClick={() => handleDeleteRel(rel.rel_id)}
                          className="btn btn-xs btn-danger"
                        >
                          删除
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* 创建/编辑节点模态框 */}
      {showNodeModal && (
        <div className="modal-overlay" onClick={() => setShowNodeModal(false)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3>{editingNode ? "编辑节点" : "创建节点"}</h3>
              <button onClick={() => { setShowNodeModal(false); setEditingNode(null); }} className="btn-close">×</button>
            </div>
            <form onSubmit={editingNode ? handleUpdateNode : handleCreateNode}>
              <div className="modal-body">
                {!editingNode && (
                  <div className="form-group">
                    <label>节点标签:</label>
                    <select
                      value={nodeForm.label}
                      onChange={(e) => setNodeForm({ ...nodeForm, label: e.target.value })}
                      required
                    >
                      <option value="">选择标签</option>
                      {labels.map(label => (
                        <option key={label} value={label}>{label}</option>
                      ))}
                    </select>
                  </div>
                )}
                <div className="form-group">
                  <label>属性:</label>
                  <div className="properties-list">
                    {Object.entries(editingNode ? editingNode.properties : nodeForm.properties).map(([key, value]) => (
                      <div key={key} className="property-row">
                        <span className="property-key">{key}:</span>
                        <input
                          type="text"
                          value={value}
                          onChange={(e) => {
                            if (editingNode) {
                              setEditingNode({
                                ...editingNode,
                                properties: { ...editingNode.properties, [key]: e.target.value }
                              });
                            } else {
                              setNodeForm({
                                ...nodeForm,
                                properties: { ...nodeForm.properties, [key]: e.target.value }
                              });
                            }
                          }}
                        />
                        <button
                          type="button"
                          onClick={() => removeProperty(editingNode ? "edit" : "node", key)}
                          className="btn btn-xs btn-danger"
                        >
                          删除
                        </button>
                      </div>
                    ))}
                  </div>
                  <button
                    type="button"
                    onClick={() => addProperty(editingNode ? "edit" : "node")}
                    className="btn btn-sm btn-secondary"
                  >
                    + 添加属性
                  </button>
                </div>
              </div>
              <div className="modal-footer">
                <button type="button" onClick={() => { setShowNodeModal(false); setEditingNode(null); }} className="btn btn-secondary">
                  取消
                </button>
                <button type="submit" className="btn btn-primary" disabled={loading}>
                  {loading ? "保存中..." : "保存"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* 创建关系模态框 */}
      {showRelModal && (
        <div className="modal-overlay" onClick={() => setShowRelModal(false)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3>创建关系</h3>
              <button onClick={() => setShowRelModal(false)} className="btn-close">×</button>
            </div>
            <form onSubmit={handleCreateRel}>
              <div className="modal-body">
                <div className="form-group">
                  <label>源节点ID:</label>
                  <input
                    type="number"
                    value={relForm.sourceId}
                    onChange={(e) => setRelForm({ ...relForm, sourceId: e.target.value })}
                    required
                    placeholder="输入源节点ID"
                  />
                </div>
                <div className="form-group">
                  <label>目标节点ID:</label>
                  <input
                    type="number"
                    value={relForm.targetId}
                    onChange={(e) => setRelForm({ ...relForm, targetId: e.target.value })}
                    required
                    placeholder="输入目标节点ID"
                  />
                </div>
                <div className="form-group">
                  <label>关系类型:</label>
                  <select
                    value={relForm.relType}
                    onChange={(e) => setRelForm({ ...relForm, relType: e.target.value })}
                    required
                  >
                    <option value="">选择关系类型</option>
                    {relTypes.map(type => (
                      <option key={type} value={type}>{type}</option>
                    ))}
                  </select>
                </div>
                <div className="form-group">
                  <label>属性 (可选):</label>
                  <div className="properties-list">
                    {Object.entries(relForm.properties).map(([key, value]) => (
                      <div key={key} className="property-row">
                        <span className="property-key">{key}:</span>
                        <input
                          type="text"
                          value={value}
                          onChange={(e) => setRelForm({
                            ...relForm,
                            properties: { ...relForm.properties, [key]: e.target.value }
                          })}
                        />
                        <button
                          type="button"
                          onClick={() => removeProperty("rel", key)}
                          className="btn btn-xs btn-danger"
                        >
                          删除
                        </button>
                      </div>
                    ))}
                  </div>
                  <button
                    type="button"
                    onClick={() => addProperty("rel")}
                    className="btn btn-sm btn-secondary"
                  >
                    + 添加属性
                  </button>
                </div>
              </div>
              <div className="modal-footer">
                <button type="button" onClick={() => setShowRelModal(false)} className="btn btn-secondary">
                  取消
                </button>
                <button type="submit" className="btn btn-primary" disabled={loading}>
                  {loading ? "创建中..." : "创建"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Cypher查询模态框 */}
      {showCypherModal && (
        <div className="modal-overlay" onClick={() => setShowCypherModal(false)}>
          <div className="modal-content modal-large" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3>Cypher 查询 (只读)</h3>
              <button onClick={() => setShowCypherModal(false)} className="btn-close">×</button>
            </div>
            <form onSubmit={handleExecuteCypher}>
              <div className="modal-body">
                <div className="form-group">
                  <label>查询语句:</label>
                  <textarea
                    value={cypherQuery}
                    onChange={(e) => setCypherQuery(e.target.value)}
                    rows={6}
                    placeholder="例如: MATCH (n:Disease) RETURN n LIMIT 10"
                    className="cypher-textarea"
                  />
                  <p className="form-hint">注意: 出于安全考虑，只允许执行只读查询（MATCH, RETURN等）</p>
                </div>
                {cypherResult && (
                  <div className="form-group">
                    <label>查询结果:</label>
                    <pre className="cypher-result">
                      {JSON.stringify(cypherResult, null, 2)}
                    </pre>
                  </div>
                )}
              </div>
              <div className="modal-footer">
                <button type="button" onClick={() => setShowCypherModal(false)} className="btn btn-secondary">
                  关闭
                </button>
                <button type="submit" className="btn btn-primary" disabled={loading}>
                  {loading ? "执行中..." : "执行"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* 嵌入Milvus模态框 */}
      {showEmbedModal && (
        <div className="modal-overlay" onClick={() => setShowEmbedModal(false)}>
          <div className="modal-content modal-large" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3>知识图谱嵌入 Milvus</h3>
              <button onClick={() => setShowEmbedModal(false)} className="btn-close">×</button>
            </div>
            <div className="modal-body">
              {/* 当前状态 */}
              {embedStatus && (
                <div className="embed-status-section">
                  <h4>当前嵌入状态</h4>
                  <div className="embed-stats">
                    <div className="stat-item">
                      <span className="stat-value">{embedStatus.total_kg_documents || 0}</span>
                      <span className="stat-label">总文档数</span>
                    </div>
                    <div className="stat-item">
                      <span className="stat-value">{embedStatus.entities_count || 0}</span>
                      <span className="stat-label">实体数</span>
                    </div>
                    <div className="stat-item">
                      <span className="stat-value">{embedStatus.relationships_count || 0}</span>
                      <span className="stat-label">关系数</span>
                    </div>
                  </div>
                  {embedStatus.label_distribution && Object.keys(embedStatus.label_distribution).length > 0 && (
                    <div className="label-distribution">
                      <h5>标签分布</h5>
                      <div className="label-tags">
                        {Object.entries(embedStatus.label_distribution).map(([label, count]) => (
                          <span key={label} className="label-tag">
                            {label}: {count}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* 标签选择 */}
              <div className="embed-labels-section">
                <h4>选择要嵌入的标签（可选）</h4>
                <p className="form-hint">不选择则嵌入所有标签的实体</p>
                <div className="label-checkboxes">
                  {labels.map(label => (
                    <label key={label} className="label-checkbox">
                      <input
                        type="checkbox"
                        checked={selectedLabelsForEmbed.includes(label)}
                        onChange={() => toggleLabelForEmbed(label)}
                      />
                      <span>{label}</span>
                    </label>
                  ))}
                </div>
                {selectedLabelsForEmbed.length > 0 && (
                  <p className="selected-labels">
                    已选择: {selectedLabelsForEmbed.join(", ")}
                  </p>
                )}
              </div>

              {/* 嵌入结果 */}
              {embedResult && (
                <div className="embed-result-section">
                  <h4>嵌入结果</h4>
                  <div className="alert alert-success">{embedResult.message}</div>
                  <div className="embed-details">
                    <p><strong>实体统计:</strong></p>
                    <ul>
                      {Object.entries(embedResult.stats.entities).map(([label, count]) => (
                        <li key={label}>{label}: {count} 个</li>
                      ))}
                    </ul>
                    <p><strong>关系数:</strong> {embedResult.stats.relationships} 个</p>
                    <p><strong>总文档数:</strong> {embedResult.stats.total_documents} 个</p>
                    <p><strong>时间戳:</strong> {embedResult.timestamp}</p>
                  </div>
                </div>
              )}
            </div>
            <div className="modal-footer">
              <button type="button" onClick={() => setShowEmbedModal(false)} className="btn btn-secondary">
                关闭
              </button>
              <button
                type="button"
                onClick={handleEmbedToMilvus}
                className="btn btn-success"
                disabled={embedLoading}
              >
                {embedLoading ? "嵌入中..." : "开始嵌入"}
              </button>
            </div>
          </div>
        </div>
      )}
      {showImportModal && (
        <div className="modal-overlay" onClick={() => setShowImportModal(false)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3>文件抽取并写入知识图谱</h3>
              <button onClick={() => setShowImportModal(false)} className="btn-close">×</button>
            </div>
            <form onSubmit={handleExtractAndImport}>
              <div className="modal-body">
                <div className="form-group">
                  <label>上传文件</label>
                  <input
                    type="file"
                    accept=".txt,.md,.pdf,.docx,.doc,.pptx,.ppt,.html,.htm"
                    onChange={handleImportFileChange}
                    disabled={importLoading}
                  />
                  <p className="form-hint">支持 txt、md、pdf、docx、pptx、html</p>
                </div>
                <div className="form-group">
                  <label>抽取最大字符数</label>
                  <input
                    type="number"
                    min={1000}
                    max={50000}
                    value={importMaxChars}
                    onChange={(e) => setImportMaxChars(Number(e.target.value))}
                    disabled={importLoading}
                  />
                </div>
                {importResult && (
                  <div className="embed-result-section">
                    <h4>导入结果</h4>
                    <div className="alert alert-success">{importResult.message}</div>
                    <div className="embed-details">
                      <p><strong>文件名:</strong> {importResult.file_name}</p>
                      <p><strong>解析文本长度:</strong> {importResult.text_length}</p>
                      <p><strong>抽取实体数:</strong> {importResult.entity_count}</p>
                      <p><strong>抽取关系数:</strong> {importResult.relation_count}</p>
                      <p><strong>节点批次数:</strong> {importResult.node_batches}</p>
                      <p><strong>关系批次数:</strong> {importResult.relation_batches}</p>
                    </div>
                  </div>
                )}
              </div>
              <div className="modal-footer">
                <button type="button" onClick={() => setShowImportModal(false)} className="btn btn-secondary">
                  关闭
                </button>
                <button type="submit" className="btn btn-primary" disabled={importLoading || !selectedImportFile}>
                  {importLoading ? "处理中..." : "开始抽取并写入"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
