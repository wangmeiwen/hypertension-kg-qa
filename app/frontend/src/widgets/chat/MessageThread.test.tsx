import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";

import MessageThread from "./MessageThread";
import { renderWithProviders } from "../../test/testUtils";

describe("MessageThread", () => {
  // 覆盖医学免责声明样式、用户消息动作和 Markdown 块级渲染。
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

    expect(screen.getByText(/不构成诊断或治疗建议/).closest("p")).toHaveClass(
      "medical-disclaimer-block",
    );
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
    expect(screen.getByRole("link", { name: "指南" })).toHaveAttribute(
      "href",
      "https://example.com",
    );
  });

  it("renders italic disclaimer markdown without visible asterisks", () => {
    renderWithProviders(
      <MessageThread
        loading={false}
        messages={[
          {
            id: 1,
            role: "assistant",
            content:
              "*以上内容仅供参考，不构成诊断或治疗建议，不能替代专业医生意见；如有不适或紧急情况，请及时就医。 *",
          },
        ]}
        onCopy={vi.fn()}
        onRollback={vi.fn()}
        onResend={vi.fn()}
      />,
    );

    const disclaimer = screen.getByText(/以上内容仅供参考/);
    expect(disclaimer.tagName).toBe("EM");
    expect(disclaimer.closest("p")).toHaveClass("medical-disclaimer-block");
    expect(screen.queryByText(/\*以上内容仅供参考/)).not.toBeInTheDocument();
  });

  it("shows think status only for the latest streaming assistant message", () => {
    const props = {
      messages: [
        {
          id: 1,
          role: "assistant" as const,
          content: "历史回答",
          thinkContent: "主agent思考中答案生成中",
        },
        { id: 2, role: "user" as const, content: "继续提问" },
        { id: 3, role: "assistant" as const, content: "", thinkContent: "执行主编排流程" },
      ],
      onCopy: vi.fn(),
      onRollback: vi.fn(),
      onResend: vi.fn(),
    };
    const { rerender } = renderWithProviders(<MessageThread loading={true} {...props} />);

    expect(screen.queryByText("主agent思考中答案生成中")).not.toBeInTheDocument();
    expect(screen.getByText("执行主编排流程")).toBeVisible();

    rerender(<MessageThread loading={false} {...props} />);

    expect(screen.queryByText("执行主编排流程")).not.toBeInTheDocument();
  });
});
