import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import styles from "./AdminPages.module.css";
import { getAdminSessionMessages } from "../../shared/api/adminClient";
import type { FlowEvent } from "../../shared/api/contracts";
import {
  getAdminAgentObservabilityRuns,
  getAdminAgentObservabilitySummary,
  getAdminSessionObservability,
} from "../../shared/api/observabilityClient";
import { classifyObservabilityLane } from "../../shared/lib/agentFlow";
import { formatDateTime, formatDurationMs, truncateText } from "../../shared/lib/format";
import { queryKeys } from "../../shared/queries/queryKeys";
import { Button, Card, DataTable, Modal, PageHeader, VirtualList } from "../../shared/ui/primitives";
import AdminSubnav from "../../widgets/admin/AdminSubnav";

function EventDetailPanel({ event, t }: { event: FlowEvent; t: (key: string) => string }) {
  // 单个 Agent 事件详情面板展示模型输入/输出和工具调用观测字段。
  const detail = event.detail || {};
  return (
    <div className={styles.modalSection}>
      <h3 style={{ margin: 0 }}>{event.title || event.stage || "-"}</h3>
      <div className="muted">{event.summary || "-"}</div>
      <div className={styles.twoColumn}>
        <Card title={t("admin.stage")}>
          <div className="stack">
            <div>{event.stage || "-"}</div>
            <div>{t("admin.eventTime")}: {formatDateTime(event.event_time)}</div>
            <div>{t("admin.actor")}: {event.actor || "-"}</div>
          </div>
        </Card>
        <Card title={t("admin.summary")}>
          <div className="stack">
            <div>{event.summary || "-"}</div>
            <div>Tool calls: {detail.tool_call_count ?? 0}</div>
          </div>
        </Card>
      </div>
      <Card title={t("admin.modelInput")}>
        <div className={styles.mono}>{detail.model_input_prompt || "-"}</div>
      </Card>
      <Card title={t("admin.modelOutput")}>
        <div className={styles.mono}>{detail.model_output_text || "-"}</div>
      </Card>
      <Card title={t("admin.toolInputs")}>
        <div className={styles.mono}>{detail.tool_call_inputs || "-"}</div>
      </Card>
      <Card title={t("admin.toolResults")}>
        <div className={styles.mono}>{detail.tool_call_results || "-"}</div>
      </Card>
    </div>
  );
}

export default function AdminObservabilityPage() {
  const { t } = useTranslation();
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [selectedTurnId, setSelectedTurnId] = useState<string | null>(null);
  const [selectedEvent, setSelectedEvent] = useState<FlowEvent | null>(null);

  const summaryQuery = useQuery({
    queryKey: queryKeys.admin.observabilitySummary,
    queryFn: () => getAdminAgentObservabilitySummary(300),
  });
  const runsQuery = useQuery({
    queryKey: queryKeys.admin.observabilityRuns(80),
    queryFn: () => getAdminAgentObservabilityRuns(80),
  });
  const turnsQuery = useQuery({
    queryKey: queryKeys.admin.sessionObservability(selectedSessionId),
    queryFn: () => getAdminSessionObservability(selectedSessionId as string),
    enabled: Boolean(selectedSessionId),
  });
  const messagesQuery = useQuery({
    queryKey: queryKeys.admin.sessionMessages(selectedSessionId),
    queryFn: () => getAdminSessionMessages(selectedSessionId as string),
    enabled: Boolean(selectedSessionId),
  });

  const groupedRuns = useMemo(() => {
    // 后端按 run 返回观测记录，前端按 session 聚合成表格行。
    const grouped = new Map<string, { latest: string; turns: number; llm: number; tools: number; tokens: number }>();
    for (const run of runsQuery.data || []) {
      const sessionId = String(run.session_id || "").trim();
      if (!sessionId) continue;
      const previous = grouped.get(sessionId) || { latest: "", turns: 0, llm: 0, tools: 0, tokens: 0 };
      grouped.set(sessionId, {
        latest: String(run.timestamp || previous.latest),
        turns: previous.turns + 1,
        llm: previous.llm + Number(run.llm_calls || 0),
        tools: previous.tools + Number(run.tool_calls || 0),
        tokens: previous.tokens + Number(run.total_tokens || 0),
      });
    }
    return Array.from(grouped.entries()).map(([sessionId, value]) => ({ sessionId, ...value }));
  }, [runsQuery.data]);

  const selectedTurn = turnsQuery.data?.find((turn) => turn.turn_id === selectedTurnId) || turnsQuery.data?.[0];
  // 将 flow_events 拆分到主控、图谱和检索三条泳道展示。
  const lanes = {
    orchestrator: selectedTurn?.flow_events.filter((event) => classifyObservabilityLane(event) === "orchestrator") || [],
    graph: selectedTurn?.flow_events.filter((event) => classifyObservabilityLane(event) === "graph") || [],
    retrieval: selectedTurn?.flow_events.filter((event) => classifyObservabilityLane(event) === "retrieval") || [],
  };

  const rows = groupedRuns.map((session) => [
    session.sessionId,
    formatDateTime(session.latest),
    String(session.turns),
    String(session.llm),
    String(session.tools),
    String(session.tokens),
    <Button key={session.sessionId} tone="secondary" onClick={() => { setSelectedSessionId(session.sessionId); setSelectedTurnId(null); }}>
      {t("admin.observability")}
    </Button>,
  ]);

  return (
    <div className={styles.layout}>
      <PageHeader eyebrow={t("admin.eyebrow")} title={t("admin.observability")} description={t("admin.description")} />
      <AdminSubnav />

      <div className={styles.statsGrid}>
        <Card title={t("admin.observabilityStats.runs")}>
          <strong>{summaryQuery.data?.total_runs ?? 0}</strong>
        </Card>
        <Card title={t("admin.observabilityStats.llmCalls")}>
          <strong>{summaryQuery.data?.total_llm_calls ?? 0}</strong>
        </Card>
        <Card title={t("admin.observabilityStats.toolCalls")}>
          <strong>{summaryQuery.data?.total_tool_calls ?? 0}</strong>
        </Card>
        <Card title={t("admin.observabilityStats.tokens")}>
          <strong>{summaryQuery.data?.total_tokens ?? 0}</strong>
        </Card>
      </div>

      <Card title={t("admin.observabilitySummary")}>
        <DataTable
          columns={[
            t("admin.table.session"),
            t("admin.table.latest"),
            t("admin.table.turns"),
            t("admin.observabilityStats.llmCalls"),
            t("admin.observabilityStats.toolCalls"),
            t("admin.observabilityStats.tokens"),
            t("admin.table.open"),
          ]}
          rows={rows}
        />
      </Card>

      <Modal open={Boolean(selectedSessionId)} onClose={() => { setSelectedSessionId(null); setSelectedEvent(null); }}>
        <div className={styles.modalSection}>
          <h2 style={{ margin: 0 }}>{t("admin.observability")}</h2>
          <div className="muted">{truncateText(messagesQuery.data?.session.title || selectedSessionId || "", 64)}</div>
          <div className="inlineMeta">
            {(turnsQuery.data || []).map((turn) => (
              <Button
                key={turn.turn_id}
                tone={selectedTurn?.turn_id === turn.turn_id ? "primary" : "secondary"}
                onClick={() => setSelectedTurnId(turn.turn_id)}
              >
                {truncateText(turn.question || turn.turn_id, 24)}
              </Button>
            ))}
          </div>
          {selectedTurn ? (
            <>
              <Card title={selectedTurn.question || selectedTurn.turn_id}>
                <div className="stack">
                  <div className="muted">{formatDateTime(selectedTurn.started_at)}</div>
                  <div>{truncateText(selectedTurn.answer_digest || "-", 180)}</div>
                  <div>{formatDurationMs(selectedTurn.duration_ms)}</div>
                </div>
              </Card>
              <div className={styles.laneGrid}>
                {(["orchestrator", "graph", "retrieval"] as const).map((lane) => (
                  <div key={lane} className={styles.lane}>
                    <strong>{t(`admin.flowLanes.${lane}`)}</strong>
                    {lanes[lane].length ? (
                      <VirtualList
                        items={lanes[lane]}
                        className="stack"
                        estimateSize={104}
                        threshold={100}
                        maxHeight={420}
                        getKey={(event) => event.event_id || `${lane}-${event.title || event.stage || ""}`}
                        renderItem={(event) => (
                        <button
                          type="button"
                          className={styles.eventItem}
                          onClick={() => setSelectedEvent(event)}
                        >
                          <div>
                            <strong>{event.title || event.stage || "-"}</strong>
                          </div>
                          <div className="muted">{event.summary || "-"}</div>
                          <div className="muted">{formatDateTime(event.event_time)}</div>
                        </button>
                        )}
                      />
                    ) : (
                      <div className="muted">-</div>
                    )}
                  </div>
                ))}
              </div>
            </>
          ) : null}
        </div>
      </Modal>

      <Modal open={Boolean(selectedEvent)} onClose={() => setSelectedEvent(null)}>
        {selectedEvent ? <EventDetailPanel event={selectedEvent} t={t} /> : null}
      </Modal>
    </div>
  );
}
