const THINK_STATUS_META = [
  { stage: "memory", label: "正在加载记忆", keywords: ["正在加载记忆", "memory", "prepare_session_turn", "build_runtime_context", "ensure_memory_current", "resolve_query"] },
  { stage: "orchestrator", label: "主agent思考中", keywords: ["主agent思考中", "主代理", "orchestrator", "task_submitted", "task_running", "解析输入", "路由", "任务拆解", "门控"] },
  { stage: "subagent", label: "子agent正在检索", keywords: ["子agent正在检索", "subagent", "子代理", "检索", "kg", "rag", "search", "retrieval", "调度选中任务", "补检索"] },
  { stage: "answer", label: "答案生成中", keywords: ["答案生成中", "finalized", "orchestrator_done", "task_completed", "终答", "最终回答", "answer"] },
];

const THINK_LABELS = THINK_STATUS_META.map((item) => item.label);

// 将任意状态文本标准化到前端展示的思考阶段标签。
const normalizeThinkStatus = (value, stage = "") => {
  const safeStage = String(stage || "").trim().toLowerCase();
  const byStage = THINK_STATUS_META.find((item) => item.stage === safeStage);
  if (byStage) return byStage.label;

  const text = String(value || "").trim();
  if (!text) return "";
  if (THINK_LABELS.includes(text)) return text;

  const lowered = text.toLowerCase();
  const matched = THINK_STATUS_META.find((item) => item.keywords.some((keyword) => lowered.includes(String(keyword).toLowerCase())));
  return matched?.label || "";
};

// 在状态序列中追加去重后的阶段标签。
export const appendThinkStatus = (statuses, value, stage = "") => {
  const label = normalizeThinkStatus(value, stage);
  if (!label || statuses.includes(label)) return statuses;
  return [...statuses, label];
};

// 将阶段列表格式化为多行文本，供消息 thinkContent 展示。
export const formatThinkStatuses = (statuses) => statuses.filter(Boolean).join("\n");

// 从 thinkContent 提取并归并有效阶段标签。
const getSimplifiedThinkStatuses = (thinkContent) => {
  const text = String(thinkContent || "").trim();
  if (!text) return [];

  const rows = text
    .split(/\n+/)
    .map((line) => line.trim())
    .filter(Boolean);
  const candidates = rows.length ? rows : [text];

  let statuses = [];
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

// 获取当前 thinkContent 的最新阶段标签。
export const getLatestThinkStatus = (thinkContent) => {
  const statuses = getSimplifiedThinkStatuses(thinkContent);
  if (!statuses.length) return "";
  return statuses[statuses.length - 1];
};
