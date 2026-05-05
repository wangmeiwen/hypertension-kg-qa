export type ObservabilityLaneKey = "orchestrator" | "graph" | "retrieval";

const GRAPH_PATTERN = /(graph|kg|图谱|知识图谱)/i;
const RETRIEVAL_PATTERN = /(retriev|rag|search|检索|召回)/i;
const DISCLAIMER_PATTERNS = [
  /以上内容仅供参考/u,
  /不构成诊断/u,
  /不能替代专业医生意见/u,
  /请及时就医/u,
  /informational only/i,
  /should not replace/i,
  /urgent medical care/i,
];

// 根据后端观测事件的 lane/actor/title/summary 文本推断展示泳道。
export function classifyObservabilityLane(event: {
  lane?: string;
  actor?: string;
  title?: string;
  summary?: string;
}): ObservabilityLaneKey {
  const value = `${event.lane || ""} ${event.actor || ""} ${event.title || ""} ${event.summary || ""}`;
  if (GRAPH_PATTERN.test(value)) return "graph";
  if (RETRIEVAL_PATTERN.test(value)) return "retrieval";
  return "orchestrator";
}

export function containsMedicalDisclaimer(text: string): boolean {
  // 用中英文常见免责声明片段判断回答是否已包含医疗风险提示。
  const normalized = String(text || "");
  return DISCLAIMER_PATTERNS.some((pattern) => pattern.test(normalized));
}
