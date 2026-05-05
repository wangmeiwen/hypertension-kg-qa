import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import styles from "./AdminDocumentsWorkspace.module.css";
import type { GraphJob } from "../../../shared/api/contracts";
import { getDocumentStats, getRecentTextEmbeddingJobs, getTextEmbeddingJobStatus, submitTextEmbeddingJob } from "../../../shared/api/graphClient";
import { formatDateTime } from "../../../shared/lib/format";
import { reportFrontendError } from "../../../shared/lib/observability";
import { queryKeys } from "../../../shared/queries/queryKeys";
import { Badge, Button, Card, InlineNotice, Input, VirtualList } from "../../../shared/ui/primitives";

function getStatusTone(status: string): "default" | "danger" | "success" {
  // 将后台任务状态映射到 Badge 颜色语义。
  if (status === "failed" || status === "cancelled") return "danger";
  if (status === "succeeded") return "success";
  return "default";
}

export default function AdminDocumentsWorkspace() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [feedback, setFeedback] = useState("");

  const statsQuery = useQuery({
    queryKey: queryKeys.admin.documentStats,
    queryFn: getDocumentStats,
  });
  const jobsQuery = useQuery({
    queryKey: queryKeys.admin.documentJobs,
    queryFn: () => getRecentTextEmbeddingJobs(12),
    // 仅在存在排队或运行中的任务时轮询，完成后停止刷新。
    refetchInterval: (query) =>
      Array.isArray(query.state.data) && (query.state.data as GraphJob[]).some((job) => job.status === "queued" || job.status === "running") ? 2500 : false,
  });
  const detailQuery = useQuery({
    queryKey: queryKeys.admin.documentJob(selectedJobId),
    queryFn: () => getTextEmbeddingJobStatus(selectedJobId as string),
    enabled: Boolean(selectedJobId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "queued" || status === "running" ? 2500 : false;
    },
  });

  const submitMutation = useMutation({
    mutationFn: () => submitTextEmbeddingJob(selectedFile as File),
    onSuccess: async (job) => {
      // 提交后选中新任务并刷新任务列表与向量库统计。
      setFeedback(t("admin.documentsWorkspace.submitSuccess"));
      setSelectedJobId(job.job_id);
      setSelectedFile(null);
      await queryClient.invalidateQueries({ queryKey: queryKeys.admin.documentJobs });
      await queryClient.invalidateQueries({ queryKey: queryKeys.admin.documentStats });
    },
    onError: (error) => {
      reportFrontendError(error, "document_submit");
      setFeedback(error instanceof Error ? error.message : String(error));
    },
  });

  const detailJson = useMemo(() => JSON.stringify(detailQuery.data?.result || detailQuery.data || {}, null, 2), [detailQuery.data]);
  const statsJson = useMemo(() => JSON.stringify(statsQuery.data || {}, null, 2), [statsQuery.data]);
  const jobs = jobsQuery.data || [];
  // 任务卡片在虚拟列表和普通列表路径下复用。
  const renderJob = (job: GraphJob) => (
    <button
      type="button"
      className={`${styles.jobCard} ${selectedJobId === job.job_id ? styles.jobCardActive : ""}`.trim()}
      onClick={() => setSelectedJobId(job.job_id)}
    >
      <div className={styles.jobHeader}>
        <strong>{job.result?.file_name ? String(job.result.file_name) : job.job_id}</strong>
        <Badge tone={getStatusTone(String(job.status))}>{String(job.status)}</Badge>
      </div>
      <div className="muted">{job.progress_text || job.message || "-"}</div>
      <div className={styles.jobMeta}>
        <span>{formatDateTime(job.created_at)}</span>
        <span>{job.stage || "-"}</span>
      </div>
    </button>
  );

  return (
    <div className={styles.workspace}>
      {feedback ? <InlineNotice tone="info">{feedback}</InlineNotice> : null}

      <div className={styles.grid}>
        <div className={styles.primaryColumn}>
          <Card title={t("admin.documentsWorkspace.uploadTitle")}>
            <div className={styles.stack}>
              <div className="muted">{t("admin.documentsWorkspace.supportedFormats")}</div>
              <Input type="file" onChange={(event) => setSelectedFile(event.target.files?.[0] || null)} />
              {selectedFile ? (
                <div className={styles.selectedFile}>
                  <strong>{t("admin.documentsWorkspace.selectedFile")}:</strong> {selectedFile.name}
                </div>
              ) : null}
              <Button onClick={() => selectedFile && void submitMutation.mutateAsync()} disabled={!selectedFile}>
                {t("common.submit")}
              </Button>
            </div>
          </Card>

          <Card title={t("admin.documentsWorkspace.jobsTitle")}>
            <div>
              {jobs.length ? (
                <VirtualList
                  items={jobs}
                  className={styles.jobsList}
                  estimateSize={112}
                  threshold={100}
                  maxHeight={560}
                  getKey={(job) => job.job_id}
                  renderItem={renderJob}
                />
              ) : (
                <div className="muted">{t("admin.documentsWorkspace.noJobs")}</div>
              )}
            </div>
          </Card>
        </div>

        <div className={styles.secondaryColumn}>
          <Card title={t("admin.documentsWorkspace.statsTitle")}>
            <div className={styles.mono}>{statsJson}</div>
          </Card>

          <Card title={t("admin.documentsWorkspace.detailTitle")}>
            <div className={styles.stack}>
              {detailQuery.data ? (
                <>
                  <div className={styles.jobHeader}>
                    <strong>{detailQuery.data.job_id}</strong>
                    <Badge tone={getStatusTone(String(detailQuery.data.status))}>{String(detailQuery.data.status)}</Badge>
                  </div>
                  <div className="muted">{detailQuery.data.progress_text || detailQuery.data.message || "-"}</div>
                  <div className={styles.metaRow}>
                    <span>{formatDateTime(detailQuery.data.created_at)}</span>
                    <span>{detailQuery.data.stage || "-"}</span>
                  </div>
                  <div className={styles.mono}>{detailJson}</div>
                </>
              ) : (
                <div className="muted">{t("common.empty")}</div>
              )}
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}
