import React, { useState } from "react";
import { doclingParseDocument, doclingParseAndEmbed } from "./api";

// 模块职责：提供 Docling 文档解析入口，支持仅解析与解析后嵌入两种流程。
export default function DoclingParser() {
  const FILE_SIZE_LIMIT = 50 * 1024 * 1024;
  const [selectedFile, setSelectedFile] = useState(null);
  const [parsing, setParsing] = useState(false);
  const [embedding, setEmbedding] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const [activeTab, setActiveTab] = useState("markdown");

  const allowedFormats = [
    ".pdf", ".docx", ".doc", ".pptx", ".ppt",
    ".html", ".htm", ".txt", ".md",
    ".jpg", ".jpeg", ".png"
  ];

  // 校验用户选择文件格式并重置结果状态。
  function handleFileSelect(e) {
    const file = e.target.files[0];
    if (file) {
      const ext = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
      if (!allowedFormats.includes(ext)) {
        setError(`不支持的文件格式: ${ext}。支持的格式: ${allowedFormats.join(", ")}`);
        setSelectedFile(null);
        return;
      }
      if (file.size > FILE_SIZE_LIMIT) {
        setError("文件大小不能超过 50MB");
        setSelectedFile(null);
        return;
      }
      setSelectedFile(file);
      setError("");
      setResult(null);
    }
  }

  // 执行纯解析流程，返回文本和结构化元数据。
  async function handleParse() {
    if (!selectedFile) return;
    setParsing(true);
    setError("");
    try {
      const data = await doclingParseDocument(selectedFile);
      setResult(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setParsing(false);
    }
  }

  // 执行解析并嵌入流程，解析结果直接进入向量检索链路。
  async function handleParseAndEmbed() {
    if (!selectedFile) return;
    setEmbedding(true);
    setError("");
    try {
      const data = await doclingParseAndEmbed(selectedFile);
      setResult(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setEmbedding(false);
    }
  }

  return (
    <div className="docling-parser">
      <div className="docling-header">
        <h3>Docling 文档解析</h3>
        <p className="docling-desc">
          使用 IBM Docling 解析多种格式文档（PDF、DOCX、PPTX、HTML等），
          提取文档布局、表格、图片等丰富内容
        </p>
      </div>

      {error && <div className="alert alert-error">{error}</div>}

      {/* 文件上传区域 */}
      <div className="upload-section docling-upload">
        <div className="upload-info">
          <p><strong>支持的格式：</strong></p>
          <div className="format-tags">
            <span className="format-tag">PDF</span>
            <span className="format-tag">DOCX</span>
            <span className="format-tag">PPTX</span>
            <span className="format-tag">HTML</span>
            <span className="format-tag">TXT</span>
            <span className="format-tag">Markdown</span>
            <span className="format-tag">JPG</span>
            <span className="format-tag">JPEG</span>
            <span className="format-tag">PNG</span>
          </div>
        </div>

        <div className="upload-area">
          <input
            type="file"
            id="docling-file-input"
            accept={allowedFormats.join(",")}
            onChange={handleFileSelect}
            disabled={parsing || embedding}
          />
          <label htmlFor="docling-file-input" className="file-input-label">
            选择文件
          </label>
          {selectedFile && (
            <div className="selected-file">
              <span className="file-name">{selectedFile.name}</span>
              <span className="file-size">({(selectedFile.size / 1024).toFixed(1)} KB)</span>
            </div>
          )}
        </div>
        <p className="form-hint">文件大小不能超过 50MB，JPG/JPEG/PNG 图片会在 OCR 前自动压缩。</p>

        <div className="upload-actions">
          <button
            className="btn btn-primary"
            onClick={handleParse}
            disabled={!selectedFile || parsing || embedding}
          >
            {parsing ? "解析中..." : "仅解析"}
          </button>
          <button
            className="btn btn-success"
            onClick={handleParseAndEmbed}
            disabled={!selectedFile || parsing || embedding}
          >
            {embedding ? "处理中..." : "解析并嵌入"}
          </button>
        </div>
      </div>

      {/* 解析结果 */}
      {result && (
        <div className="docling-result">
          <div className="result-header">
            <h4>解析结果</h4>
            <div className="result-tabs">
              {result.markdown && (
                <button
                  className={activeTab === "markdown" ? "active" : ""}
                  onClick={() => setActiveTab("markdown")}
                >
                  Markdown
                </button>
              )}
              {result.text && (
                <button
                  className={activeTab === "text" ? "active" : ""}
                  onClick={() => setActiveTab("text")}
                >
                  纯文本
                </button>
              )}
              <button
                className={activeTab === "meta" ? "active" : ""}
                onClick={() => setActiveTab("meta")}
              >
                元数据
              </button>
            </div>
          </div>

          <div className="result-content">
            {/* Markdown 内容 */}
            {activeTab === "markdown" && result.markdown && (
              <div className="markdown-content">
                <pre>{result.markdown}</pre>
              </div>
            )}

            {/* 纯文本内容 */}
            {activeTab === "text" && result.text && (
              <div className="text-content">
                <pre>{result.text}</pre>
              </div>
            )}

            {/* 元数据 */}
            {activeTab === "meta" && (
              <div className="meta-content">
                <div className="meta-grid">
                  <div className="meta-item">
                    <label>文件名:</label>
                    <span>{result.filename}</span>
                  </div>
                  <div className="meta-item">
                    <label>格式:</label>
                    <span>{result.format}</span>
                  </div>
                  <div className="meta-item">
                    <label>页数:</label>
                    <span>{result.pages?.length || 0}</span>
                  </div>
                  <div className="meta-item">
                    <label>表格数:</label>
                    <span>{result.tables_count}</span>
                  </div>
                  <div className="meta-item">
                    <label>图片数:</label>
                    <span>{result.images_count}</span>
                  </div>
                  {result.chunks_count !== undefined && (
                    <div className="meta-item">
                      <label>文本块数:</label>
                      <span>{result.chunks_count}</span>
                    </div>
                  )}
                </div>

                {/* 页面详情 */}
                {result.pages && result.pages.length > 0 && (
                  <div className="pages-detail">
                    <h5>页面详情</h5>
                    <table className="pages-table">
                      <thead>
                        <tr>
                          <th>页码</th>
                          <th>尺寸</th>
                          <th>单元格数</th>
                          <th>元素数</th>
                        </tr>
                      </thead>
                      <tbody>
                        {result.pages.map((page, idx) => (
                          <tr key={idx}>
                            <td>{page.page_no}</td>
                            <td>{page.size ? `${page.size[0]} x ${page.size[1]}` : "-"}</td>
                            <td>{page.cells}</td>
                            <td>{page.items}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}

                {/* 完整元数据 */}
                {result.metadata && (
                  <div className="full-metadata">
                    <h5>完整元数据</h5>
                    <pre>{JSON.stringify(result.metadata, null, 2)}</pre>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
