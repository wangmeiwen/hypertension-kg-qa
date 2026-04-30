import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ErrorState, PageSkeleton, VirtualList } from "./primitives";
import { renderWithProviders } from "../../test/testUtils";

describe("shared ui primitives", () => {
  it("renders page skeleton and error state primitives", () => {
    renderWithProviders(
      <>
        <PageSkeleton />
        <ErrorState title="错误标题" description="错误描述" />
      </>,
    );

    expect(screen.getByRole("status", { name: "页面加载中" })).toBeVisible();
    expect(screen.getByRole("alert")).toHaveTextContent("错误标题");
  });

  it("virtualizes long lists while keeping small lists simple", () => {
    const items = Array.from({ length: 160 }, (_, index) => `项目 ${index}`);

    renderWithProviders(
      <VirtualList
        items={items}
        estimateSize={48}
        threshold={20}
        getKey={(item) => item}
        renderItem={(item) => <div>{item}</div>}
      />,
    );

    expect(screen.getByTestId("virtual-list")).toBeVisible();
    expect(screen.queryByText("项目 159")).not.toBeInTheDocument();
  });
});
