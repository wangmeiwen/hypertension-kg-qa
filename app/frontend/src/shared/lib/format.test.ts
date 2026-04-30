import { formatDurationMs, truncateText } from "./format";

describe("format helpers", () => {
  it("formats milliseconds into seconds when needed", () => {
    expect(formatDurationMs(450)).toBe("450 ms");
    expect(formatDurationMs(1530)).toBe("1.53 s");
  });

  it("truncates long text safely", () => {
    expect(truncateText("abcdefghij", 6)).toBe("abcdef...");
    expect(truncateText("abc", 6)).toBe("abc");
  });
});
