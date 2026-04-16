export const JOB_STATUS_QUEUED = "queued";
export const JOB_STATUS_RUNNING = "running";
export const JOB_STATUS_PAUSED = "paused";
export const JOB_STATUS_SUCCEEDED = "succeeded";
export const JOB_STATUS_FAILED = "failed";

export const isJobRunning = (job) => job && [JOB_STATUS_QUEUED, JOB_STATUS_RUNNING].includes(job.status);

export const getJobStatusLabel = (job) => {
  if (!job) return "";
  if (job.status === JOB_STATUS_QUEUED) return "排队中";
  if (job.status === JOB_STATUS_RUNNING) return "处理中";
  if (job.status === JOB_STATUS_PAUSED) return "已暂停";
  if (job.status === JOB_STATUS_SUCCEEDED) return "已完成";
  if (job.status === JOB_STATUS_FAILED) return "失败";
  return job.status;
};

export const getJobTypeLabel = (jobType) => {
  if (jobType === "graph_extract_import") return "文件抽取入图谱";
  if (jobType === "graph_embed_milvus") return "嵌入 Milvus";
  return jobType || "未知任务";
};

export const getJobStageLabel = (stage) => {
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
};

export const formatTaskTime = (isoText) => {
  if (!isoText) return "-";
  const date = new Date(isoText);
  if (Number.isNaN(date.getTime())) return isoText;
  return date.toLocaleString("zh-CN", { hour12: false });
};

export const formatDuration = (seconds) => {
  const safeSeconds = Math.max(0, Number(seconds) || 0);
  const hours = Math.floor(safeSeconds / 3600);
  const minutes = Math.floor((safeSeconds % 3600) / 60);
  const remainSeconds = Math.floor(safeSeconds % 60);
  if (hours > 0) return `${hours}小时 ${minutes}分 ${remainSeconds}秒`;
  if (minutes > 0) return `${minutes}分 ${remainSeconds}秒`;
  return `${remainSeconds}秒`;
};

export const formatCount = (value) => {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "-";
  return numeric.toLocaleString("zh-CN");
};

export const getGraphImportTextProgress = (job) => {
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
};

export const getStageElapsedSeconds = (job) => {
  if (!job?.stage_started_at) return null;
  const startedAt = new Date(job.stage_started_at);
  if (Number.isNaN(startedAt.getTime())) return null;
  const referenceText = isJobRunning(job) ? null : (job.finished_at || job.last_heartbeat_at);
  const referenceTime = referenceText ? new Date(referenceText).getTime() : Date.now();
  return Math.max(0, Math.floor((referenceTime - startedAt.getTime()) / 1000));
};

export const getJobWarnings = (job) => {
  if (!Array.isArray(job?.warnings)) return [];
  return job.warnings;
};

export const getPausedReason = (job) => {
  if (!job || job.status !== JOB_STATUS_PAUSED) return "";
  return (
    job?.result?.pause_reason ||
    job?.result?.last_error ||
    job?.error ||
    ""
  );
};

export const getChunkLimit = (job) => {
  const value = Number(job?.chunk_limit || job?.result?.chunk_limit || 0);
  return Number.isFinite(value) && value > 0 ? value : 0;
};

export const getEmbedBatchSize = (job) => {
  const value = Number(job?.batch_size || 0);
  return Number.isFinite(value) && value > 0 ? value : 0;
};

export const getEmbedLabelsText = (job) => {
  const labels = Array.isArray(job?.labels)
    ? job.labels.map((item) => String(item || "").trim()).filter(Boolean)
    : [];
  return labels.length > 0 ? labels.join(", ") : "全部标签";
};

export const getCacheInfo = (job) => ({
  cacheUsed: Boolean(job?.cache_used ?? job?.result?.cache_used),
  cacheInvalidatedReason: job?.cache_invalidated_reason || job?.result?.cache_invalidated_reason || "",
  resumeCompatible: job?.resume_compatible ?? job?.result?.resume_compatible,
});

export const getParallelInfo = (job) => {
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
};

export const formatLaneStatuses = (job) => {
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
};

export const buildJobSummary = (job) => {
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
    const entities = result?.stats?.entities_count || 0;
    const relationships = result?.stats?.relationships_count || 0;
    return `总文档 ${totalDocuments} 条，实体 ${entities} 个，关系 ${relationships} 条`;
  }
  if (job?.status === JOB_STATUS_FAILED) {
    return job?.error ? String(job.error).split("\n")[0] : "任务执行失败";
  }
  return job?.progress_text || "等待后台处理";
};
