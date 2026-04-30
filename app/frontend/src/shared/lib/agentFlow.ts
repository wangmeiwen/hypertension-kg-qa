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
  const normalized = String(text || "");
  return DISCLAIMER_PATTERNS.some((pattern) => pattern.test(normalized));
}
