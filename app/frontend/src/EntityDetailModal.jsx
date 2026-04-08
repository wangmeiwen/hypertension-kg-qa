import { useState, useEffect } from "react";
import { getEntityDetail } from "./api";

// 模块职责：展示单个实体的属性、入边关系和出边关系。
export default function EntityDetailModal({ entityId, onClose }) {
  const [entityData, setEntityData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [activeTab, setActiveTab] = useState("info");

  useEffect(() => {
    if (entityId) {
      loadEntityDetail();
    }
  }, [entityId]);

  // 拉取实体详情并更新弹窗数据状态。
  const loadEntityDetail = async () => {
    setLoading(true);
    setError("");
    try {
      const data = await getEntityDetail(entityId);
      setEntityData(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  // 根据实体标签返回展示色。
  const getLabelColor = (label) => {
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

  if (!entityId) return null;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal entity-detail-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h3>实体详情</h3>
          <button className="close-btn" onClick={onClose}>×</button>
        </div>

        {loading && (
          <div className="entity-loading">
            <div className="loading-spinner"></div>
            <p>加载中...</p>
          </div>
        )}

        {error && <div className="entity-error">{error}</div>}

        {entityData && !loading && (
          <div className="entity-content">
            {/* 实体基本信息 */}
            <div className="entity-header">
              <div className="entity-name">{entityData.entity.name}</div>
              <div className="entity-labels">
                {entityData.entity.labels.map((label, idx) => (
                  <span
                    key={idx}
                    className="entity-label"
                    style={{ backgroundColor: getLabelColor(label) }}
                  >
                    {label}
                  </span>
                ))}
              </div>
            </div>

            {/* 标签切换 */}
            <div className="entity-tabs">
              <button
                className={activeTab === "info" ? "active" : ""}
                onClick={() => setActiveTab("info")}
              >
                基本信息
              </button>
              <button
                className={activeTab === "incoming" ? "active" : ""}
                onClick={() => setActiveTab("incoming")}
              >
                入边关系 ({entityData.relationships.incoming.length})
              </button>
              <button
                className={activeTab === "outgoing" ? "active" : ""}
                onClick={() => setActiveTab("outgoing")}
              >
                出边关系 ({entityData.relationships.outgoing.length})
              </button>
            </div>

            {/* 基本信息标签 */}
            {activeTab === "info" && (
              <div className="entity-tab-content">
                <div className="entity-id">ID: {entityData.entity.id}</div>
                {Object.keys(entityData.entity.properties).length > 0 ? (
                  <div className="entity-properties">
                    <h4>属性</h4>
                    {Object.entries(entityData.entity.properties).map(([key, value]) => (
                      <div key={key} className="property-item">
                        <span className="property-key">{key}:</span>
                        <span className="property-value">
                          {typeof value === "object" ? JSON.stringify(value) : String(value)}
                        </span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="no-data">暂无其他属性</div>
                )}
              </div>
            )}

            {/* 入边关系标签 */}
            {activeTab === "incoming" && (
              <div className="entity-tab-content">
                {entityData.relationships.incoming.length > 0 ? (
                  <div className="relationships-list">
                    {entityData.relationships.incoming.map((rel, idx) => (
                      <div key={idx} className="relationship-item incoming">
                        <div className="rel-header">
                          <span className="rel-entity">{rel.source_name}</span>
                          <span className="rel-arrow">→</span>
                          <span className="rel-type">{rel.rel_type}</span>
                          <span className="rel-arrow">→</span>
                          <span className="rel-entity current">{entityData.entity.name}</span>
                        </div>
                        {Object.keys(rel.rel_props).length > 0 && (
                          <div className="rel-props">
                            {Object.entries(rel.rel_props).map(([k, v]) => (
                              <span key={k} className="rel-prop">
                                {k}: {typeof v === "object" ? JSON.stringify(v) : String(v)}
                              </span>
                            ))}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="no-data">暂无入边关系</div>
                )}
              </div>
            )}

            {/* 出边关系标签 */}
            {activeTab === "outgoing" && (
              <div className="entity-tab-content">
                {entityData.relationships.outgoing.length > 0 ? (
                  <div className="relationships-list">
                    {entityData.relationships.outgoing.map((rel, idx) => (
                      <div key={idx} className="relationship-item outgoing">
                        <div className="rel-header">
                          <span className="rel-entity current">{entityData.entity.name}</span>
                          <span className="rel-arrow">→</span>
                          <span className="rel-type">{rel.rel_type}</span>
                          <span className="rel-arrow">→</span>
                          <span className="rel-entity">{rel.target_name}</span>
                        </div>
                        {Object.keys(rel.rel_props).length > 0 && (
                          <div className="rel-props">
                            {Object.entries(rel.rel_props).map(([k, v]) => (
                              <span key={k} className="rel-prop">
                                {k}: {typeof v === "object" ? JSON.stringify(v) : String(v)}
                              </span>
                            ))}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="no-data">暂无出边关系</div>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
