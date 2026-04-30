import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";

import MessageThread from "./MessageThread";
import { renderWithProviders } from "../../test/testUtils";

describe("MessageThread", () => {
  it("renders disclaimer styling and triggers resend for user messages", async () => {
    const user = userEvent.setup();
    const onCopy = vi.fn();
    const onRollback = vi.fn();
    const onResend = vi.fn();

    renderWithProviders(
      <MessageThread
        loading={false}
        messages={[
          { id: 1, role: "user", content: "高血压需要长期监测吗？" },
          { id: 2, role: "assistant", content: "以上内容仅供参考，不构成诊断或治疗建议。" },
        ]}
        onCopy={onCopy}
        onRollback={onRollback}
        onResend={onResend}
      />,
    );

    expect(screen.getByText(/不构成诊断或治疗建议/).closest("p")).toHaveClass("medical-disclaimer-block");
    await user.click(screen.getByRole("button", { name: "重新发送" }));
    expect(onResend).toHaveBeenCalledTimes(1);
  });

  it("renders markdown blocks from assistant messages", () => {
    renderWithProviders(
      <MessageThread
        loading={false}
        messages={[
          {
            id: 1,
            role: "assistant",
            content: [
              "## 治疗要点",
              "",
              "- **监测血压**",
              "- 使用 `ACEI`",
              "",
              "| 药物 | 说明 |",
              "| --- | --- |",
              "| 氨氯地平 | 钙通道阻滞剂 |",
              "",
              "> 需要结合个体情况。",
              "",
              "参考 [指南](https://example.com)。",
            ].join("\n"),
          },
        ]}
        onCopy={vi.fn()}
        onRollback={vi.fn()}
        onResend={vi.fn()}
      />,
    );

    expect(screen.getByText("治疗要点").closest("div")).toHaveClass("md-h2");
    expect(screen.getByText("监测血压").closest("li")).toBeVisible();
    expect(screen.getByText("ACEI").tagName).toBe("CODE");
    expect(screen.getByRole("table")).toBeVisible();
    expect(screen.getByText("需要结合个体情况。").closest("blockquote")).toHaveClass("md-quote");
    expect(screen.getByRole("link", { name: "指南" })).toHaveAttribute("href", "https://example.com");
  });
});
