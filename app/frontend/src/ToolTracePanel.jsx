import { useState } from "react";

// 模块职责：展示工具调用轨迹，支持按步骤展开查看输入输出。
export default function ToolTracePanel({ traces, isLoading }) {
  const [expandedSteps, setExpandedSteps] = useState(new Set());

  // 切换某一步骤的展开/收起状态。
  const toggleStep = (step) => {
    const newExpanded = new Set(expandedSteps);
    if (newExpanded.has(step)) {
      newExpanded.delete(step);
    } else {
      newExpanded.add(step);
    }
    setExpandedSteps(newExpanded);
  };

  // 为不同工具类型返回对应图标。
  const getToolIcon = (toolName) => {
    switch (toolName) {
      case "kg_query":
        return (
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="12" cy="12" r="3" />
            <path d="M12 1v6m0 6v6m4.22-10.22l4.24-4.24M6.34 6.34L2.1 2.1m17.8 17.8l-4.24-4.24M6.34 17.66l-4.24 4.24M23 12h-6m-6 0H1m20.24-4.24l-4.24 4.24M6.34 6.34l-4.24-4.24" />
          </svg>
        );
      case "rag_search":
        return (
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="11" cy="11" r="8" />
            <path d="m21 21-4.35-4.35" />
          </svg>
        );
      default:
        return (
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" />
          </svg>
        );
    }
  };

  // 工具英文标识映射为中文展示名。
  const getToolName = (toolName) => {
    const names = {
      kg_query: "知识图谱查询",
      rag_search: "向量检索"
    };
    return names[toolName] || toolName;
  };

  if (!traces || traces.length === 0) {
    return null;
  }

  return (
    <div className="tool-trace-panel">
      <div className="tool-trace-header">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" />
        </svg>
        <span>工具调用记录 ({traces.length})</span>
        {isLoading && <span className="loading-indicator">思考中...</span>}
      </div>

      <div className="tool-trace-list">
        {traces.map((trace, index) => (
          <div
            key={index}
            className={`tool-trace-item ${trace.action} ${expandedSteps.has(trace.step) ? "expanded" : ""}`}
          >
            <div
              className="tool-trace-summary"
              onClick={() => trace.action === "tool" && toggleStep(trace.step)}
            >
              <div className="tool-trace-step">步骤 {trace.step}</div>
              
              {trace.action === "tool" ? (
                <>
                  <div className="tool-trace-icon">{getToolIcon(trace.tool_name)}</div>
                  <div className="tool-trace-name">{getToolName(trace.tool_name)}</div>
                  <div className="tool-trace-expand">
                    {expandedSteps.has(trace.step) ? "▼" : "▶"}
                  </div>
                </>
              ) : (
                <>
                  <div className="tool-trace-icon answer-icon">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
                    </svg>
                  </div>
                  <div className="tool-trace-name">生成回答</div>
                </>
              )}
            </div>

            {trace.action === "tool" && expandedSteps.has(trace.step) && (
              <div className="tool-trace-details">
                <div className="tool-detail-section">
                  <h4>输入参数</h4>
                  <pre className="tool-code">
                    {JSON.stringify(trace.tool_input, null, 2)}
                  </pre>
                </div>

                <div className="tool-detail-section">
                  <h4>返回结果</h4>
                  <pre className="tool-code">
                    {JSON.stringify(trace.tool_result, null, 2)}
                  </pre>
                </div>
              </div>
            )}

            {trace.action === "answer" && (
              <div className="tool-trace-answer">
                {trace.content}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
