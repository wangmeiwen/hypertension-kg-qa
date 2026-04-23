interface ThinkStatusMeta {
  stage: string;
  label: string;
  keywords: string[];
}

const THINK_STATUS_META: ThinkStatusMeta[] = [
  {
    stage: "memory",
    label: "加载会话记忆",
    keywords: ["loading session memory", "memory", "prepare_session_turn", "build_runtime_context", "ensure_memory_current", "resolve_query"],
  },
  {
    stage: "orchestrator",
    label: "执行主编排流程",
    keywords: ["running orchestrator", "orchestrator", "task_submitted", "task_running", "route", "planner"],
  },
  {
    stage: "subagent",
    label: "检索支持上下文",
    keywords: ["retrieving support context", "subagent", "kg", "rag", "search", "retrieval"],
  },
  {
    stage: "answer",
    label: "组织最终回答",
    keywords: ["composing answer", "finalized", "orchestrator_done", "task_completed", "answer"],
  },
];

const THINK_LABELS = THINK_STATUS_META.map((item) => item.label);

const normalizeThinkStatus = (value: string, stage = ""): string => {
  const safeStage = String(stage || "").trim().toLowerCase();
  const byStage = THINK_STATUS_META.find((item) => item.stage === safeStage);
  if (byStage) return byStage.label;

  const text = String(value || "").trim();
  if (!text) return "";
  if (THINK_LABELS.includes(text)) return text;

  const lowered = text.toLowerCase();
  const matched = THINK_STATUS_META.find((item) => (
    item.keywords.some((keyword) => lowered.includes(String(keyword).toLowerCase()))
  ));
  return matched?.label || "";
};

export const appendThinkStatus = (statuses: string[], value: string, stage = ""): string[] => {
  const label = normalizeThinkStatus(value, stage);
  if (!label || statuses.includes(label)) return statuses;
  return [...statuses, label];
};

export const formatThinkStatuses = (statuses: string[]): string => statuses.filter(Boolean).join("\n");

const getSimplifiedThinkStatuses = (thinkContent: string): string[] => {
  const text = String(thinkContent || "").trim();
  if (!text) return [];

  const rows = text
    .split(/\n+/)
    .map((line) => line.trim())
    .filter(Boolean);
  const candidates = rows.length ? rows : [text];

  let statuses: string[] = [];
  candidates.forEach((row) => {
    const direct = normalizeThinkStatus(row);
    if (direct) {
      statuses = appendThinkStatus(statuses, direct);
    }
  });

  THINK_STATUS_META.forEach((item) => {
    if (item.keywords.some((keyword) => text.toLowerCase().includes(String(keyword).toLowerCase()))) {
      statuses = appendThinkStatus(statuses, item.label);
    }
  });
  return statuses;
};

export const getLatestThinkStatus = (thinkContent: string): string => {
  const statuses = getSimplifiedThinkStatuses(thinkContent);
  if (!statuses.length) return "";
  return statuses[statuses.length - 1];
};
