import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import AppLayout from "./AppLayout";
import i18n from "../../shared/lib/i18n";

describe("AppLayout", () => {
  it("renders workspace navigation and outlet content", () => {
    const queryClient = new QueryClient();

    render(
      <I18nextProvider i18n={i18n}>
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={["/chat"]}>
            <Routes>
              <Route path="/" element={<AppLayout />}>
                <Route path="chat" element={<div>chat-content</div>} />
              </Route>
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>
      </I18nextProvider>,
    );

    expect(screen.getByText("HypertensionKG")).toBeTruthy();
    expect(screen.getByText("问答")).toBeTruthy();
    expect(screen.getByText("chat-content")).toBeTruthy();
  });
});
