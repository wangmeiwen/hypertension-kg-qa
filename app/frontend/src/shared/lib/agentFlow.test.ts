import { classifyObservabilityLane, containsMedicalDisclaimer } from "./agentFlow";

describe("agentFlow helpers", () => {
  it("classifies graph and retrieval lanes with Chinese and English keywords", () => {
    expect(classifyObservabilityLane({ lane: "graph-agent" })).toBe("graph");
    expect(classifyObservabilityLane({ actor: "图谱代理" })).toBe("graph");
    expect(classifyObservabilityLane({ title: "检索支持上下文" })).toBe("retrieval");
    expect(classifyObservabilityLane({ summary: "retrieval stage" })).toBe("retrieval");
    expect(classifyObservabilityLane({ actor: "planner" })).toBe("orchestrator");
  });

  it("matches Chinese and English medical disclaimers", () => {
    expect(containsMedicalDisclaimer("以上内容仅供参考，不构成诊断或治疗建议。")).toBe(true);
    expect(containsMedicalDisclaimer("The response is informational only and should not replace urgent medical care.")).toBe(true);
    expect(containsMedicalDisclaimer("普通段落")).toBe(false);
  });
});
