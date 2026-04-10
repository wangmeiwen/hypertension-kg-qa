import React, { useEffect, useState } from "react";
import {
  submitTextEmbeddingJob,
  getTextEmbeddingJobStatus,
  getRecentTextEmbeddingJobs,
} from "./api";

// 模块职责：提供文本嵌入入口，支持后台任务提交、挂起与最近任务查看。
export default function DoclingParser() {
  const FILE_SIZE_LIMIT = 50 * 1024 * 1024;
  const JOB_POLL_MS = 20000;
  const JOB_STATUS_QUEUED = "queued";
  const JOB_STATUS_RUNNING = "running";
  const JOB_STATUS_SUCCEEDED = "succeeded";
  const JOB_STATUS_FAILED = "failed";

  const [selectedFile, setSelectedFile] = useState(null);
  const [activeJob, setActiveJob] = useState(null);
  const [isActiveJobSuspended, setIsActiveJobSuspended] = useState(false);
  const [recentJobs, setRecentJobs] = useState([]);
  const [submitting, setSubmitting] = useState(false);
  const [success, setSuccess] = useState("");
  const [error, setError] = useState("");

  const allowedFormats = [
    ".pdf", ".docx", ".doc", ".pptx", ".ppt",
    ".html", ".htm", ".txt", ".md",
    ".jpg", ".jpeg", ".png"
  ];

  useEffect(() => {
    loadRecentJobs();
  }, []);

  useEffect(() => {
    if (!activeJob || !isJobRunning(activeJob)) {
      return undefined;
    }
    const timer = window.setInterval(async () => {
      try {
        const nextJob = await getTextEmbeddingJobStatus(activeJob.job_id);
        setActiveJob(nextJob);
        if (nextJob.status === JOB_STATUS_SUCCEEDED) {
          setIsActiveJobSuspended(false);
          setSuccess("文本嵌入任务执行成功");
          await loadRecentJobs();
          window.setTimeout(() => setSuccess(""), 5000);
        } else if (nextJob.status === JOB_STATUS_FAILED) {
          setIsActiveJobSuspended(false);
          setError(nextJob.error || "文本嵌入任务执行失败");
          await loadRecentJobs();
        }
      } catch (err) {
        setError(err.message);
      }
    }, JOB_POLL_MS);
    return () => window.clearInterval(timer);
  }, [activeJob?.job_id, activeJob?.status]);

  useEffect(() => {
    if (!recentJobs.some((job) => isJobRunning(job))) {
      return undefined;
    }
    const timer = window.setInterval(() => {
      loadRecentJobs();
    }, JOB_POLL_MS);
    return () => window.clearInterval(timer);
  }, [recentJobs]);

  function isJobRunning(job) {
    return job && [JOB_STATUS_QUEUED, JOB_STATUS_RUNNING].includes(job.status);
  }

  function getJobStatusLabel(job) {
    if (!job) return "";
    if (job.status === JOB_STATUS_QUEUED) return "排队中";
    if (job.status === JOB_STATUS_RUNNING) return "处理中";
    if (job.status === JOB_STATUS_SUCCEEDED) return "已完成";
    if (job.status === JOB_STATUS_FAILED) return "失败";
    return job.status;
  }

  function getStageLabel(stage) {
    const labelMap = {
      validate_file: "文件校验",
      parse_document: "文档解析",
      ocr_images: "图片 OCR",
      segment_text: "文本切分",
      embed_vector_store: "写入文本向量数据库",
      finalize: "整理结果",
    };
    return labelMap[stage] || stage || "-";
  }

  function formatTaskTime(isoText) {
    if (!isoText) return "-";
    const date = new Date(isoText);
    if (Number.isNaN(date.getTime())) return isoText;
    return date.toLocaleString("zh-CN", { hour12: false });
  }

  function formatDuration(seconds) {
    const safeSeconds = Math.max(0, Number(seconds) || 0);
    const hours = Math.floor(safeSeconds / 3600);
    const minutes = Math.floor((safeSeconds % 3600) / 60);
    const remainSeconds = Math.floor(safeSeconds % 60);
    if (hours > 0) return `${hours}小时 ${minutes}分 ${remainSeconds}秒`;
    if (minutes > 0) return `${minutes}分 ${remainSeconds}秒`;
    return `${remainSeconds}秒`;
  }

  function getStageElapsedSeconds(job) {
    if (!job?.stage_started_at) return null;
    const startedAt = new Date(job.stage_started_at);
    if (Number.isNaN(startedAt.getTime())) return null;
    const referenceText = isJobRunning(job) ? null : (job.finished_at || job.last_heartbeat_at);
    const referenceTime = referenceText ? new Date(referenceText).getTime() : Date.now();
    return Math.max(0, Math.floor((referenceTime - startedAt.getTime()) / 1000));
  }

  function getWarnings(job) {
    if (!Array.isArray(job?.warnings)) return [];
    return job.warnings;
  }

  function getActionLabel() {
    if (submitting) return "提交中...";
    if (isJobRunning(activeJob)) return "后台处理中...";
    if (activeJob?.status === JOB_STATUS_SUCCEEDED) return "再次嵌入";
    if (activeJob?.status === JOB_STATUS_FAILED) return "重新嵌入";
    return "嵌入到文本向量数据库";
  }

  function buildJobSummary(job) {
    const result = job?.result || {};
    if (job?.status === JOB_STATUS_SUCCEEDED) {
      return `格式 ${result.format || "-"}，页数 ${result.pages || 0}，子块 ${result.sub_chunks_count || 0}，Milvus 行数 ${result.milvus_row_count || 0}`;
    }
    if (job?.status === JOB_STATUS_FAILED) {
      return job?.error ? String(job.error).split("\n")[0] : "任务执行失败";
    }
    return job?.progress_text || "等待后台处理";
  }

  async function loadRecentJobs() {
    try {
      const data = await getRecentTextEmbeddingJobs(8);
      setRecentJobs(data);
    } catch (err) {
      console.error("加载最近文本嵌入任务失败:", err);
    }
  }

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
      if (!isJobRunning(activeJob)) {
        setActiveJob(null);
        setIsActiveJobSuspended(false);
      }
    }
  }

  async function handleSubmit() {
    if (!selectedFile) {
      setError("请先选择文件");
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      const job = await submitTextEmbeddingJob(selectedFile);
      setActiveJob(job);
      setIsActiveJobSuspended(false);
      await loadRecentJobs();
      setSuccess("文本嵌入任务已提交到后台");
      window.setTimeout(() => setSuccess(""), 5000);
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  }

  function handleSuspendToBackground() {
    setIsActiveJobSuspended(true);
    loadRecentJobs();
    setSuccess("文本嵌入任务已挂起到后台，可在最近任务中查看进度");
    window.setTimeout(() => setSuccess(""), 5000);
  }

  function renderJobTelemetry(job) {
    if (!job) return null;
    const warnings = getWarnings(job);
    const stageElapsed = getStageElapsedSeconds(job);
    return (
      <div className="embed-details">
        <p><strong>当前进度:</strong> {job.progress_text || "等待后台处理"}</p>
        <p><strong>当前阶段:</strong> {getStageLabel(job.stage)}</p>
        <p><strong>阶段详情:</strong> {job.stage_detail || "-"}</p>
        <p><strong>阶段已运行:</strong> {stageElapsed == null ? "-" : formatDuration(stageElapsed)}</p>
        <p><strong>最近心跳:</strong> {formatTaskTime(job.last_heartbeat_at)}</p>
        <p><strong>任务ID:</strong> {job.job_id}</p>
        {warnings.length > 0 && (
          <div className="graph-job-warnings">
            <strong>阶段提醒:</strong>
            <ul>
              {warnings.slice(-3).map((warning, index) => (
                <li key={`${warning.timestamp || "warning"}-${index}`}>{warning.message}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="docling-parser">
      <div className="docling-header">
        <h3>文本嵌入</h3>
        <p className="docling-desc">
          使用 Docling 解析多种格式文档并写入文本向量数据库，支持后台任务挂起、阶段跟踪和最近任务查看
        </p>
      </div>

      {error && <div className="alert alert-error">{error}</div>}
      {success && <div className="alert alert-success">{success}</div>}

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
            disabled={isJobRunning(activeJob)}
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

        {activeJob && !isActiveJobSuspended && (
          <div className="task-progress-panel">
            <div className={`job-status-chip status-${activeJob.status}`}>
              {getJobStatusLabel(activeJob)}
            </div>
            {renderJobTelemetry(activeJob)}
          </div>
        )}

        {activeJob?.status === JOB_STATUS_SUCCEEDED && activeJob?.result && !isActiveJobSuspended && (
          <div className="embed-result-section">
            <h4>嵌入结果</h4>
            <div className="alert alert-success">{activeJob.result.message}</div>
            <div className="embed-details">
              <p><strong>文件名:</strong> {activeJob.result.file_name}</p>
              <p><strong>格式:</strong> {activeJob.result.format || "-"}</p>
              <p><strong>页数:</strong> {activeJob.result.pages || 0}</p>
              <p><strong>表格数:</strong> {activeJob.result.tables || 0}</p>
              <p><strong>图片数:</strong> {activeJob.result.images || 0}</p>
              <p><strong>父块数:</strong> {activeJob.result.parent_chunks_count || 0}</p>
              <p><strong>子块数:</strong> {activeJob.result.sub_chunks_count || 0}</p>
              <p><strong>Milvus 行数:</strong> {activeJob.result.milvus_row_count || 0}</p>
            </div>
          </div>
        )}

        <div className="upload-actions">
          {isJobRunning(activeJob) && isActiveJobSuspended ? (
            <button className="btn btn-secondary" type="button" disabled>
              任务已挂起到后台
            </button>
          ) : isJobRunning(activeJob) ? (
            <>
              <button className="btn btn-success" type="button" disabled>
                后台处理中...
              </button>
              <button className="btn btn-primary" type="button" onClick={handleSuspendToBackground}>
                挂起到后台
              </button>
            </>
          ) : (
            <button
              className="btn btn-success"
              type="button"
              onClick={handleSubmit}
              disabled={!selectedFile || submitting}
            >
              {getActionLabel()}
            </button>
          )}
        </div>
      </div>

      <div className="graph-tasks-section">
        <div className="panel-header">
          <div>
            <h3>最近任务</h3>
            <p className="graph-task-caption">文本嵌入任务在后台继续执行，关闭当前聚焦后仍可在这里查看进度。</p>
          </div>
          <button type="button" onClick={loadRecentJobs} className="btn btn-secondary">
            刷新任务
          </button>
        </div>
        {recentJobs.length === 0 ? (
          <div className="graph-task-empty">暂无最近任务</div>
        ) : (
          <div className="graph-task-list">
            {recentJobs.map((job) => (
              <div key={job.job_id} className="graph-task-card">
                <div className="graph-task-header">
                  <div>
                    <div className="graph-task-title">文本嵌入任务</div>
                    <div className="graph-task-meta">任务ID：{job.job_id}</div>
                  </div>
                  <div className={`job-status-chip status-${job.status}`}>
                    {getJobStatusLabel(job)}
                  </div>
                </div>
                <div className="graph-task-body">
                  <p><strong>当前进度：</strong>{job.progress_text || "等待后台处理"}</p>
                  <p><strong>当前阶段：</strong>{getStageLabel(job.stage)}</p>
                  <p><strong>阶段详情：</strong>{job.stage_detail || "-"}</p>
                  <p><strong>阶段已运行：</strong>{getStageElapsedSeconds(job) == null ? "-" : formatDuration(getStageElapsedSeconds(job))}</p>
                  <p><strong>最近心跳：</strong>{formatTaskTime(job.last_heartbeat_at)}</p>
                  <p><strong>创建时间：</strong>{formatTaskTime(job.created_at)}</p>
                  <p><strong>完成时间：</strong>{formatTaskTime(job.finished_at)}</p>
                  <p><strong>结果摘要：</strong>{buildJobSummary(job)}</p>
                  {getWarnings(job).length > 0 && (
                    <div className="graph-job-warnings">
                      <strong>阶段提醒：</strong>
                      <ul>
                        {getWarnings(job).slice(-2).map((warning, index) => (
                          <li key={`${warning.timestamp || "warning"}-${index}`}>{warning.message}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
