import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import RouteFallback from "./RouteFallback";
import { renderWithProviders } from "../../test/testUtils";

describe("RouteFallback", () => {
  it("renders localized page skeleton instead of English loading text", () => {
    renderWithProviders(<RouteFallback />);

    expect(screen.getByRole("status", { name: "页面加载中" })).toBeVisible();
    expect(screen.queryByText(/Loading workspace/i)).not.toBeInTheDocument();
  });
});
