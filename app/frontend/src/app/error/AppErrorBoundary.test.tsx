import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AppErrorBoundary from "./AppErrorBoundary";
import { renderWithProviders } from "../../test/testUtils";

vi.mock("../../shared/lib/observability", () => ({
  reportFrontendError: vi.fn(),
}));

// 主动抛出渲染异常，用于验证 ErrorBoundary 的捕获与恢复 UI。
function BrokenComponent(): ReactElement {
  throw new Error("测试渲染异常");
}

describe("AppErrorBoundary", () => {
  beforeEach(() => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
  });

  it("captures render errors and exposes recovery actions", async () => {
    const user = userEvent.setup();

    renderWithProviders(
      <AppErrorBoundary>
        <BrokenComponent />
      </AppErrorBoundary>,
    );

    expect(screen.getByRole("alert")).toHaveTextContent("页面发生异常");
    expect(screen.getByText("测试渲染异常")).toBeVisible();

    await user.click(screen.getByRole("button", { name: "重试" }));
    expect(screen.getByRole("alert")).toBeVisible();
  });
});
