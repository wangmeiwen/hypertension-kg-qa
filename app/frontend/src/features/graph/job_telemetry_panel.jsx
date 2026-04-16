import {
  isJobRunning,
  getJobStageLabel,
  formatTaskTime,
  formatDuration,
  formatCount,
  getGraphImportTextProgress,
  getStageElapsedSeconds,
  getJobWarnings,
  getPausedReason,
  getChunkLimit,
  getEmbedBatchSize,
  getEmbedLabelsText,
  getCacheInfo,
  getParallelInfo,
  formatLaneStatuses,
} from "./job_utils";

// 图谱任务遥测信息面板，兼容导入任务与嵌入任务。
export default function JobTelemetryPanel({ job, warningLimit = 3 }) {
  if (!job) return null;

  if (job.job_type === "graph_embed_milvus") {
    const warnings = getJobWarnings(job);
    const stageElapsed = getStageElapsedSeconds(job);
    const batchSize = getEmbedBatchSize(job);
    return (
      <div className="embed-details">
        <p><strong>当前进度:</strong> {job.progress_text || "等待后台处理"}</p>
        <p><strong>当前阶段:</strong> {getJobStageLabel(job.stage)}</p>
        <p><strong>阶段详情:</strong> {job.stage_detail || "-"}</p>
        <p><strong>嵌入批大小:</strong> {batchSize > 0 ? formatCount(batchSize) : "-"}</p>
        <p><strong>嵌入标签:</strong> {getEmbedLabelsText(job)}</p>
        <p><strong>阶段已运行:</strong> {stageElapsed == null ? "-" : formatDuration(stageElapsed)}</p>
        <p><strong>最近心跳:</strong> {formatTaskTime(job.last_heartbeat_at)}</p>
        <p><strong>创建时间:</strong> {formatTaskTime(job.created_at)}</p>
        <p><strong>完成时间:</strong> {formatTaskTime(job.finished_at)}</p>
        <p><strong>任务ID:</strong> {job.job_id}</p>
        {warnings.length > 0 && (
          <div className="graph-job-warnings">
            <strong>阶段提醒:</strong>
            <ul>
              {warnings.slice(-warningLimit).map((warning, index) => (
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
            {warnings.slice(-warningLimit).map((warning, index) => (
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
