import { fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import MessageComposer from "./MessageComposer";
import { renderWithProviders } from "../../test/testUtils";

describe("MessageComposer", () => {
  it("edits draft text and sends the message", async () => {
    const user = userEvent.setup();
    const onDraftChange = vi.fn();
    const onSend = vi.fn();

    renderWithProviders(
      <MessageComposer
        draft=""
        loading={false}
        stopping={false}
        selectedFile={null}
        filePreview={null}
        onDraftChange={onDraftChange}
        onFileChange={vi.fn()}
        onClearFile={vi.fn()}
        onSend={onSend}
        onStop={vi.fn()}
      />,
    );

    await user.type(screen.getByRole("textbox", { name: "问题输入" }), "高血压怎么管理？");
    await user.click(screen.getByRole("button", { name: "发送" }));

    expect(onDraftChange).toHaveBeenCalled();
    expect(onSend).toHaveBeenCalledTimes(1);
  });

  it("shows attachment preview and supports removal", async () => {
    const user = userEvent.setup();
    const file = new File(["hello"], "guide.pdf", { type: "application/pdf" });
    const onClearFile = vi.fn();

    renderWithProviders(
      <MessageComposer
        draft=""
        loading={false}
        stopping={false}
        selectedFile={file}
        filePreview={null}
        onDraftChange={vi.fn()}
        onFileChange={vi.fn()}
        onClearFile={onClearFile}
        onSend={vi.fn()}
        onStop={vi.fn()}
      />,
    );

    expect(screen.getByText("guide.pdf")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "移除附件" }));
    expect(onClearFile).toHaveBeenCalledTimes(1);
  });

  it("calls file change and exposes stop action while streaming", async () => {
    const user = userEvent.setup();
    const onFileChange = vi.fn();
    const onStop = vi.fn();

    const view = renderWithProviders(
      <MessageComposer
        draft="问题"
        loading
        stopping={false}
        selectedFile={null}
        filePreview={null}
        onDraftChange={vi.fn()}
        onFileChange={onFileChange}
        onClearFile={vi.fn()}
        onSend={vi.fn()}
        onStop={onStop}
      />,
    );

    const input = view.container.querySelector('input[type="file"]') as HTMLInputElement | null;
    if (!input) throw new Error("file input not found");
    fireEvent.change(input, { target: { files: [new File(["x"], "a.txt")] } });
    await user.click(screen.getByRole("button", { name: "停止" }));

    expect(onFileChange).toHaveBeenCalledTimes(1);
    expect(onStop).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "发送" })).toBeDisabled();
  });
});
