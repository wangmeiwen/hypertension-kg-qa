import { formatDurationMs, truncateText } from "./format";

describe("format helpers", () => {
  // 覆盖表格和观测面板会复用的基础格式化工具。
  it("formats milliseconds into seconds when needed", () => {
    expect(formatDurationMs(450)).toBe("450 ms");
    expect(formatDurationMs(1530)).toBe("1.53 s");
  });

  it("truncates long text safely", () => {
    expect(truncateText("abcdefghij", 6)).toBe("abcdef...");
    expect(truncateText("abc", 6)).toBe("abc");
  });
});
