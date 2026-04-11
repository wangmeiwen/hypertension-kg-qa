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
  submitGraphExtractImport,
  submitGraphEmbedMilvus,
  getGraphEmbedStatus,
  getGraphJobStatus,
  getRecentGraphJobs
} from "./api";
import KnowledgeGraphCanvas, { DEFAULT_GRAPH_LIMIT, GRAPH_LIMIT_OPTIONS } from "./KnowledgeGraphCanvas";

// 模块职责：提供图谱后台管理能力（节点、关系、查询、导入和嵌入）。
export default function GraphManager() {
  const IMPORT_FILE_SIZE_LIMIT = 50 * 1024 * 1024;
  const GRAPH_JOB_POLL_MS = 20000;
  const JOB_STATUS_QUEUED = "queued";
  const JOB_STATUS_RUNNING = "running";
  const JOB_STATUS_PAUSED = "paused";
  const JOB_STATUS_SUCCEEDED = "succeeded";
  const JOB_STATUS_FAILED = "failed";

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
  const [selectedImportFile, setSelectedImportFile] = useState(null);
  const [importResult, setImportResult] = useState(null);
  const [activeImportJob, setActiveImportJob] = useState(null);
  const [activeEmbedJob, setActiveEmbedJob] = useState(null);
  const [recentJobs, setRecentJobs] = useState([]);
  const [isSubmittingImport, setIsSubmittingImport] = useState(false);
  const [isSubmittingEmbed, setIsSubmittingEmbed] = useState(false);

  // 初始化加载
  useEffect(() => {
    loadLabels();
    loadRelTypes();
    loadNodes();
    loadGraphPreview("", DEFAULT_GRAPH_LIMIT);
    loadRecentJobs();
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

  // 拉取图谱预览数据，供画布快速浏览使用。
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

  async function loadRecentJobs() {
    try {
      const data = await getRecentGraphJobs(8);
      setRecentJobs(data);
    } catch (err) {
      console.error("加载最近图谱任务失败:", err);
    }
  }

  // 打开嵌入模态框
  function openEmbedModal() {
    loadEmbedStatus();
    setSelectedLabelsForEmbed([]);
    setShowEmbedModal(true);
  }

  function isJobRunning(job) {
    return job && [JOB_STATUS_QUEUED, JOB_STATUS_RUNNING].includes(job.status);
  }

  function getJobStatusLabel(job) {
    if (!job) return "";
    if (job.status === JOB_STATUS_QUEUED) return "排队中";
    if (job.status === JOB_STATUS_RUNNING) return "处理中";
    if (job.status === JOB_STATUS_PAUSED) return "已暂停";
    if (job.status === JOB_STATUS_SUCCEEDED) return "已完成";
    if (job.status === JOB_STATUS_FAILED) return "失败";
    return job.status;
  }

  function getJobTypeLabel(jobType) {
    if (jobType === "graph_extract_import") return "文件抽取入图谱";
    if (jobType === "graph_embed_milvus") return "嵌入 Milvus";
    return jobType || "未知任务";
  }

  function getJobStageLabel(stage) {
    const labelMap = {
      validate_file: "文件校验",
      parse_document: "文档解析",
      ocr_images: "图片 OCR",
      kg_primary_extract: "主抽取",
      kg_omission_judge: "漏抽判定",
      kg_supplement_extract: "补抽",
      build_neo4j_queries: "构建 Neo4j 查询",
      write_neo4j: "写入 Neo4j",
      finalize: "整理结果",
      read_graph: "读取图谱"
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

  function formatCount(value) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return "-";
    return numeric.toLocaleString("zh-CN");
  }

  function getGraphImportTextProgress(job) {
    if (!job || job.job_type !== "graph_extract_import") return null;
    const totalChars = Number(job.total_chars || job.result?.parsed_text_length || 0);
    if (!totalChars) return null;
    const processedChars = Math.min(
      totalChars,
      Number(
        job.processed_chars ??
        job.result?.processed_text_length ??
        (job.status === JOB_STATUS_SUCCEEDED ? totalChars : 0)
      ) || 0
    );
    return {
      totalChars,
      processedChars,
      totalChunks: Number(job.total_chunks || job.result?.chunk_count || 0) || 0,
      completedChunks: Math.min(
        Number(job.completed_chunks || 0) || 0,
        Number(job.total_chunks || job.result?.chunk_count || 0) || 0,
      ),
      currentChunkIndex: Number(job.current_chunk_index || 0) || 0,
      currentChunkChars: Number(job.current_chunk_chars || 0) || 0,
    };
  }

  function getStageElapsedSeconds(job) {
    if (!job?.stage_started_at) return null;
    const startedAt = new Date(job.stage_started_at);
    if (Number.isNaN(startedAt.getTime())) return null;
    const referenceText = isJobRunning(job) ? null : (job.finished_at || job.last_heartbeat_at);
    const referenceTime = referenceText ? new Date(referenceText).getTime() : Date.now();
    return Math.max(0, Math.floor((referenceTime - startedAt.getTime()) / 1000));
  }

  function getJobWarnings(job) {
    if (!Array.isArray(job?.warnings)) return [];
    return job.warnings;
  }

  function getPausedReason(job) {
    if (!job || job.status !== JOB_STATUS_PAUSED) return "";
    return (
      job?.result?.pause_reason ||
      job?.result?.last_error ||
      job?.error ||
      ""
    );
  }

  function getChunkLimit(job) {
    const value = Number(job?.chunk_limit || job?.result?.chunk_limit || 0);
    return Number.isFinite(value) && value > 0 ? value : 0;
  }

  function getCacheInfo(job) {
    return {
      cacheUsed: Boolean(job?.cache_used ?? job?.result?.cache_used),
      cacheInvalidatedReason: job?.cache_invalidated_reason || job?.result?.cache_invalidated_reason || "",
      resumeCompatible: job?.resume_compatible ?? job?.result?.resume_compatible,
    };
  }

  function getParallelInfo(job) {
    const parallelism = Number(job?.parallelism || job?.result?.parallelism || 0) || 0;
    const activeLanes = Number(job?.active_lanes || job?.result?.active_lanes || 0) || 0;
    const currentAttempt = Number(job?.current_attempt || job?.result?.current_attempt || 0) || 0;
    const maxAttempts = Number(job?.max_attempts || job?.result?.max_attempts || 0) || 0;
    const laneStatuses = Array.isArray(job?.lane_statuses)
      ? job.lane_statuses
      : (Array.isArray(job?.result?.lane_statuses) ? job.result.lane_statuses : []);
    return {
      parallelism,
      activeLanes,
      currentAttempt,
      maxAttempts,
      laneStatuses,
    };
  }

  function formatLaneStatuses(job) {
    const { laneStatuses } = getParallelInfo(job);
    if (!laneStatuses.length) return "";
    return laneStatuses.map((lane) => {
      const label = lane?.label || `lane${lane?.lane_id || "?"}`;
      const status = lane?.status || "unknown";
      const chunkIndex = Number(lane?.current_chunk_index || 0) || 0;
      const attempt = Number(lane?.current_attempt || 0) || 0;
      const suffix = chunkIndex > 0 ? `，块 ${chunkIndex}` : "";
      const retryText = attempt > 0 ? `，重试 ${attempt}/${getParallelInfo(job).maxAttempts || 0}` : "";
      return `${label}：${status}${suffix}${retryText}`;
    }).join("；");
  }

  function renderJobTelemetry(job) {
    if (!job) return null;
    const warnings = getJobWarnings(job);
    const stageElapsed = getStageElapsedSeconds(job);
    const textProgress = getGraphImportTextProgress(job);
    const resumeChunk = Number(job.resumed_from_chunk || job.result?.resumed_from_chunk || 0) || 0;
    const pausedReason = getPausedReason(job);
    const chunkLimit = getChunkLimit(job);
    const { cacheUsed, cacheInvalidatedReason, resumeCompatible } = getCacheInfo(job);
    const { parallelism, activeLanes, currentAttempt, maxAttempts } = getParallelInfo(job);
    const laneStatusSummary = formatLaneStatuses(job);
    return (
      <div className="embed-details">
        <p><strong>当前进度:</strong> {job.progress_text || "等待后台处理"}</p>
        <p><strong>当前阶段:</strong> {getJobStageLabel(job.stage)}</p>
        <p><strong>阶段详情:</strong> {job.stage_detail || "-"}</p>
        {pausedReason && (
          <p><strong>暂停原因:</strong> {pausedReason}</p>
        )}
        {chunkLimit > 0 && (
          <p><strong>当前分块大小:</strong> {formatCount(chunkLimit)} 字</p>
        )}
        {parallelism > 0 && (
          <p><strong>并发抽取:</strong> {parallelism} 路（当前可用 {activeLanes} 路）</p>
        )}
        {maxAttempts > 0 && (
          <p><strong>当前重试:</strong> {currentAttempt > 0 ? `${currentAttempt}/${maxAttempts}` : `0/${maxAttempts}`}</p>
        )}
        {laneStatusSummary && (
          <p><strong>Lane 状态:</strong> {laneStatusSummary}</p>
        )}
        <p><strong>本地缓存:</strong> {cacheUsed ? "已命中" : "未命中"}</p>
        {cacheInvalidatedReason && (
          <p><strong>缓存/断点处理:</strong> {cacheInvalidatedReason}</p>
        )}
        {resumeCompatible === false && (
          <p><strong>断点兼容性:</strong> 旧断点与当前分块策略不兼容，已重新建任务</p>
        )}
        {job.is_resumed && (
          <p><strong>续跑状态:</strong> {resumeChunk > 0 ? `已自动从第 ${resumeChunk} 块继续` : "已自动从未完成块继续"}</p>
        )}
        {textProgress && (
          <>
            <p><strong>总字数:</strong> {formatCount(textProgress.totalChars)}</p>
            <p><strong>已处理字数:</strong> {formatCount(textProgress.processedChars)}</p>
            <p><strong>分块进度:</strong> {textProgress.totalChunks > 0 ? `${textProgress.completedChunks}/${textProgress.totalChunks}` : "-"}</p>
          </>
        )}
        <p><strong>阶段已运行:</strong> {stageElapsed == null ? "-" : formatDuration(stageElapsed)}</p>
        <p><strong>最近心跳:</strong> {formatTaskTime(job.last_heartbeat_at)}</p>
        <p><strong>任务ID:</strong> {job.job_id}</p>
        {warnings.length > 0 && (
          <div className="graph-job-warnings">
            <strong>阶段提醒:</strong>
            <ul>
              {warnings.slice(-3).map((warning, index) => (
                <li key={`${warning.timestamp || "warning"}-${index}`}>
                  {warning.message}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    );
  }

  function buildJobSummary(job) {
    const result = job?.result || {};
    const textProgress = getGraphImportTextProgress(job);
    if (job?.job_type === "graph_extract_import" && job?.status === JOB_STATUS_PAUSED) {
      return getPausedReason(job) || result?.message || "已暂停，可重新提交同文件继续";
    }
    if (job?.job_type === "graph_extract_import" && job?.status === JOB_STATUS_SUCCEEDED) {
      return `实体 ${result.entity_count || 0} 个，关系 ${result.relation_count || 0} 条，解析后文本长度 ${result.parsed_text_length || 0}`;
    }
    if (job?.job_type === "graph_extract_import" && textProgress) {
      return `已处理 ${formatCount(textProgress.processedChars)} / ${formatCount(textProgress.totalChars)} 字`;
    }
    if (job?.job_type === "graph_embed_milvus" && job?.status === JOB_STATUS_SUCCEEDED) {
      const totalDocuments = result?.stats?.total_documents || 0;
      const relationships = result?.stats?.relationships || 0;
      return `总文档 ${totalDocuments} 条，关系 ${relationships} 条`;
    }
    if (job?.status === JOB_STATUS_FAILED) {
      return job?.error ? String(job.error).split("\n")[0] : "任务执行失败";
    }
    return job?.progress_text || "等待后台处理";
  }

  function getImportActionLabel() {
    if (isSubmittingImport) return "提交中...";
    if (isJobRunning(activeImportJob)) return "后台处理中...";
    if (activeImportJob?.status === JOB_STATUS_SUCCEEDED) return "再次导入";
    if (activeImportJob?.status === JOB_STATUS_PAUSED) return "重新提交继续";
    if (activeImportJob?.status === JOB_STATUS_FAILED) return "重新导入";
    return "开始抽取并写入";
  }

  function getEmbedActionLabel() {
    if (isSubmittingEmbed) return "提交中...";
    if (isJobRunning(activeEmbedJob)) return "后台嵌入中...";
    if (activeEmbedJob?.status === JOB_STATUS_SUCCEEDED) return "再次嵌入";
    if (activeEmbedJob?.status === JOB_STATUS_FAILED) return "重新嵌入";
    return "开始嵌入";
  }

  async function refreshGraphSurfaces() {
    await Promise.all([loadLabels(), loadRelTypes(), loadNodes(), loadEmbedStatus()]);
    await loadGraphPreview(searchKeyword, graphLimit);
    await loadRecentJobs();
  }

  useEffect(() => {
    if (!activeImportJob || !isJobRunning(activeImportJob)) {
      return undefined;
    }
    const timer = window.setInterval(async () => {
      try {
        const nextJob = await getGraphJobStatus(activeImportJob.job_id);
        setActiveImportJob(nextJob);
        if (nextJob.status === JOB_STATUS_SUCCEEDED) {
          setImportResult(nextJob.result || null);
          setSuccess("文件抽取并写入图谱成功");
          await refreshGraphSurfaces();
          window.setTimeout(() => setSuccess(""), 5000);
        } else if (nextJob.status === JOB_STATUS_PAUSED) {
          setImportResult(nextJob.result || null);
          setSuccess(nextJob.result?.message || "任务已暂停，可重新提交同文件继续");
          await refreshGraphSurfaces();
          window.setTimeout(() => setSuccess(""), 6000);
        } else if (nextJob.status === JOB_STATUS_FAILED) {
          setError(nextJob.error || "文件抽取导入失败");
        }
      } catch (err) {
        setError(err.message);
      }
    }, GRAPH_JOB_POLL_MS);
    return () => window.clearInterval(timer);
  }, [activeImportJob?.job_id, activeImportJob?.status, searchKeyword, graphLimit]);

  useEffect(() => {
    if (!activeEmbedJob || !isJobRunning(activeEmbedJob)) {
      return undefined;
    }
    const timer = window.setInterval(async () => {
      try {
        const nextJob = await getGraphJobStatus(activeEmbedJob.job_id);
        setActiveEmbedJob(nextJob);
        if (nextJob.status === JOB_STATUS_SUCCEEDED) {
          setEmbedResult(nextJob.result || null);
          setSuccess("知识图谱嵌入成功！");
          await refreshGraphSurfaces();
          window.setTimeout(() => setSuccess(""), 5000);
        } else if (nextJob.status === JOB_STATUS_FAILED) {
          setError(nextJob.error || "知识图谱嵌入失败");
        }
      } catch (err) {
        setError(err.message);
      }
    }, GRAPH_JOB_POLL_MS);
    return () => window.clearInterval(timer);
  }, [activeEmbedJob?.job_id, activeEmbedJob?.status, searchKeyword, graphLimit]);

  useEffect(() => {
    if (!recentJobs.some((job) => isJobRunning(job))) {
      return undefined;
    }
    const timer = window.setInterval(() => {
      loadRecentJobs();
    }, GRAPH_JOB_POLL_MS);
    return () => window.clearInterval(timer);
  }, [recentJobs]);

  // 执行嵌入
  async function handleEmbedToMilvus() {
    if (!window.confirm("确定要将知识图谱嵌入到 Milvus 吗？这可能需要一些时间。")) {
      return;
    }
    setEmbedLoading(true);
    setIsSubmittingEmbed(true);
    setError("");
    try {
      const labelsToEmbed = selectedLabelsForEmbed.length > 0 ? selectedLabelsForEmbed : null;
      const job = await submitGraphEmbedMilvus(labelsToEmbed, 100);
      setActiveEmbedJob(job);
      setEmbedResult(null);
      await loadRecentJobs();
      setSuccess("嵌入任务已提交到后台");
      setTimeout(() => setSuccess(""), 5000);
    } catch (err) {
      setError(err.message);
    } finally {
      setEmbedLoading(false);
      setIsSubmittingEmbed(false);
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

  // 打开文件抽取入图谱弹窗并重置状态。
  function openImportModal() {
    setError("");
    setShowImportModal(true);
  }

  // 处理抽取入图谱的本地文件选择。
  function handleImportFileChange(e) {
    const file = e.target.files[0];
    if (file && file.size > IMPORT_FILE_SIZE_LIMIT) {
      setSelectedImportFile(null);
      setError("文件大小不能超过 50MB");
      return;
    }
    setError("");
    setImportResult(null);
    if (!isJobRunning(activeImportJob)) {
      setActiveImportJob(null);
    }
    setSelectedImportFile(file || null);
  }

  // 执行文档抽取并写入图谱流程。
  async function handleExtractAndImport(e) {
    e.preventDefault();
    if (!selectedImportFile) {
      setError("请先选择文件");
      return;
    }
    setError("");
    setIsSubmittingImport(true);
    try {
      const job = await submitGraphExtractImport(selectedImportFile);
      setActiveImportJob(job);
      setImportResult(null);
      await loadRecentJobs();
      setSuccess(job.message || "抽取导入任务已提交到后台");
      setTimeout(() => setSuccess(""), 5000);
    } catch (err) {
      setError(err.message);
    } finally {
      setIsSubmittingImport(false);
    }
  }

  function handleSuspendImportToBackground() {
    setShowImportModal(false);
    setSuccess("抽取导入任务已挂起到后台，可在最近任务中查看进度");
    window.setTimeout(() => setSuccess(""), 5000);
  }

  function handleSuspendEmbedToBackground() {
    setShowEmbedModal(false);
    setSuccess("嵌入任务已挂起到后台，可在最近任务中查看进度");
    window.setTimeout(() => setSuccess(""), 5000);
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

  // 切换图谱预览返回数量并刷新预览。
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

      <div className="graph-tasks-section">
        <div className="panel-header">
          <div>
            <h3>最近任务</h3>
            <p className="graph-task-caption">关闭弹窗后，图谱导入和嵌入任务仍会继续在后台执行。</p>
          </div>
          <button type="button" onClick={loadRecentJobs} className="btn btn-secondary">
            刷新任务
          </button>
        </div>
        {recentJobs.length === 0 ? (
          <div className="graph-task-empty">暂无最近任务</div>
        ) : (
          <div className="graph-task-list">
            {recentJobs.map((job) => {
              const textProgress = getGraphImportTextProgress(job);
              const resumeChunk = Number(job.resumed_from_chunk || job.result?.resumed_from_chunk || 0) || 0;
              const pausedReason = getPausedReason(job);
              const chunkLimit = getChunkLimit(job);
              const { cacheUsed, cacheInvalidatedReason, resumeCompatible } = getCacheInfo(job);
              return (
                <div key={job.job_id} className="graph-task-card">
                  <div className="graph-task-header">
                    <div>
                      <div className="graph-task-title">{getJobTypeLabel(job.job_type)}</div>
                      <div className="graph-task-meta">任务ID：{job.job_id}</div>
                    </div>
                    <div className={`job-status-chip status-${job.status}`}>
                      {getJobStatusLabel(job)}
                    </div>
                  </div>
                  <div className="graph-task-body">
                    <p><strong>当前进度：</strong>{job.progress_text || "等待后台处理"}</p>
                    <p><strong>当前阶段：</strong>{getJobStageLabel(job.stage)}</p>
                    <p><strong>阶段详情：</strong>{job.stage_detail || "-"}</p>
                    {pausedReason && (
                      <p><strong>暂停原因：</strong>{pausedReason}</p>
                    )}
                    {chunkLimit > 0 && (
                      <p><strong>当前分块大小：</strong>{formatCount(chunkLimit)} 字</p>
                    )}
                    <p><strong>本地缓存：</strong>{cacheUsed ? "已命中" : "未命中"}</p>
                    {cacheInvalidatedReason && (
                      <p><strong>缓存/断点处理：</strong>{cacheInvalidatedReason}</p>
                    )}
                    {resumeCompatible === false && (
                      <p><strong>断点兼容性：</strong>旧断点与当前分块策略不兼容，已重新建任务</p>
                    )}
                    {job.is_resumed && (
                      <p><strong>续跑状态：</strong>{resumeChunk > 0 ? `已自动从第 ${resumeChunk} 块继续` : "已自动从未完成块继续"}</p>
                    )}
                    {textProgress && (
                      <>
                        <p><strong>总字数：</strong>{formatCount(textProgress.totalChars)}</p>
                        <p><strong>已处理字数：</strong>{formatCount(textProgress.processedChars)}</p>
                        <p><strong>分块进度：</strong>{textProgress.totalChunks > 0 ? `${textProgress.completedChunks}/${textProgress.totalChunks}` : "-"}</p>
                      </>
                    )}
                    <p><strong>阶段已运行：</strong>{getStageElapsedSeconds(job) == null ? "-" : formatDuration(getStageElapsedSeconds(job))}</p>
                    <p><strong>最近心跳：</strong>{formatTaskTime(job.last_heartbeat_at)}</p>
                    <p><strong>创建时间：</strong>{formatTaskTime(job.created_at)}</p>
                    <p><strong>完成时间：</strong>{formatTaskTime(job.finished_at)}</p>
                    <p><strong>结果摘要：</strong>{buildJobSummary(job)}</p>
                    {getJobWarnings(job).length > 0 && (
                      <div className="graph-job-warnings">
                        <strong>阶段提醒：</strong>
                        <ul>
                          {getJobWarnings(job).slice(-2).map((warning, index) => (
                            <li key={`${warning.timestamp || "warning"}-${index}`}>{warning.message}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
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
                    {activeEmbedJob && (
                      <div className={`job-status-chip status-${activeEmbedJob.status}`}>
                        {getJobStatusLabel(activeEmbedJob)}
                        {activeEmbedJob.progress_text ? `：${activeEmbedJob.progress_text}` : ""}
                      </div>
                    )}
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
              {activeEmbedJob && !embedResult && (
                <div className="embed-result-section">
                  <h4>嵌入任务状态</h4>
                  <div className={`job-status-chip status-${activeEmbedJob.status}`}>
                    {getJobStatusLabel(activeEmbedJob)}
                  </div>
                  {renderJobTelemetry(activeEmbedJob)}
                </div>
              )}
            </div>
              <div className="modal-footer">
                <button type="button" onClick={() => setShowEmbedModal(false)} className="btn btn-secondary">
                  关闭
                </button>
                {isJobRunning(activeEmbedJob) ? (
                  <>
                    <button
                      type="button"
                      className="btn btn-success"
                      disabled
                    >
                      正在嵌入...
                    </button>
                    <button
                      type="button"
                      onClick={handleSuspendEmbedToBackground}
                      className="btn btn-primary"
                    >
                      挂起到后台
                    </button>
                  </>
                ) : (
                  <button
                    type="button"
                    onClick={handleEmbedToMilvus}
                    className="btn btn-success"
                    disabled={embedLoading || isSubmittingEmbed}
                  >
                    {getEmbedActionLabel()}
                  </button>
                )}
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
                    accept=".txt,.md,.pdf,.docx,.doc,.pptx,.ppt,.html,.htm,.jpg,.jpeg,.png"
                    onChange={handleImportFileChange}
                    disabled={isJobRunning(activeImportJob)}
                  />
                  <p className="form-hint">支持 txt、md、pdf、docx、pptx、html、jpg、jpeg、png</p>
                  <p className="form-hint">文件大小不能超过 50MB</p>
                  <p className="form-hint">JPG/JPEG/PNG 图片会在 OCR 前自动压缩到模型可处理范围</p>
                </div>
                {activeImportJob && (
                  <div className="task-progress-panel">
                    <div className={`job-status-chip status-${activeImportJob.status}`}>
                      {getJobStatusLabel(activeImportJob)}
                    </div>
                    {renderJobTelemetry(activeImportJob)}
                  </div>
                )}
                {importResult && (
                  <div className="embed-result-section">
                    <h4>导入结果</h4>
                    <div className="alert alert-success">{importResult.message}</div>
                    <div className="embed-details">
                      <p><strong>文件名:</strong> {importResult.file_name}</p>
                      <p><strong>解析器:</strong> {importResult.parser_used || "-"}</p>
                      <p><strong>OCR 引擎:</strong> {importResult.ocr_engine || "-"}</p>
                      <p><strong>解析后文本长度:</strong> {importResult.parsed_text_length}</p>
                      <p className="form-hint">表示 OCR/解析后实际提取出的文本字符数，不代表图片大小或版面复杂度</p>
                      <p><strong>抽取实体数:</strong> {importResult.entity_count}</p>
                      <p><strong>抽取关系数:</strong> {importResult.relation_count}</p>
                      <p><strong>节点批次数:</strong> {importResult.node_batches}</p>
                      <p><strong>关系批次数:</strong> {importResult.relation_batches}</p>
                      {importResult.parse_warning && <p><strong>解析提醒:</strong> {importResult.parse_warning}</p>}
                    </div>
                  </div>
                )}
              </div>
              <div className="modal-footer">
                <button type="button" onClick={() => setShowImportModal(false)} className="btn btn-secondary">
                  关闭
                </button>
                {isJobRunning(activeImportJob) ? (
                  <>
                    <button
                      type="button"
                      className="btn btn-primary"
                      disabled
                    >
                      正在抽取并写入...
                    </button>
                    <button
                      type="button"
                      onClick={handleSuspendImportToBackground}
                      className="btn btn-success"
                    >
                      挂起到后台
                    </button>
                  </>
                ) : (
                  <button
                    type="submit"
                    className="btn btn-primary"
                    disabled={isSubmittingImport || !selectedImportFile}
                  >
                    {getImportActionLabel()}
                  </button>
                )}
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
