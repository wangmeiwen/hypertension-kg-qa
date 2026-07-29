import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";

import AdminDocumentsWorkspace from "./AdminDocumentsWorkspace";
import { renderWithProviders } from "../../../test/testUtils";

const graphClientMocks = vi.hoisted(() => ({
  getDocumentStats: vi.fn(),
  getRecentTextEmbeddingJobs: vi.fn(),
  getTextEmbeddingJobStatus: vi.fn(),
  submitTextEmbeddingJob: vi.fn(),
}));

vi.mock("../../../shared/api/graphClient", () => ({
  getDocumentStats: graphClientMocks.getDocumentStats,
  getRecentTextEmbeddingJobs: graphClientMocks.getRecentTextEmbeddingJobs,
  getTextEmbeddingJobStatus: graphClientMocks.getTextEmbeddingJobStatus,
  submitTextEmbeddingJob: graphClientMocks.submitTextEmbeddingJob,
}));

describe("AdminDocumentsWorkspace", () => {
  // 用可控 API mock 覆盖上传、任务轮询、详情和统计四条主要路径。
  beforeEach(() => {
    vi.clearAllMocks();
    graphClientMocks.getDocumentStats.mockResolvedValue({ row_count: 12 });
    graphClientMocks.getRecentTextEmbeddingJobs.mockResolvedValue([
      {
        job_id: "job-1",
        status: "succeeded",
        progress_text: "已完成",
        created_at: "2026-01-01T00:00:00Z",
        result: { file_name: "guide.pdf", chunks: 3 },
      },
    ]);
    graphClientMocks.getTextEmbeddingJobStatus.mockResolvedValue({
      job_id: "job-1",
      status: "queued",
    });
    graphClientMocks.submitTextEmbeddingJob.mockResolvedValue({
      job_id: "job-1",
      status: "queued",
    });
  });

  it("submits a document embedding job", async () => {
    const user = userEvent.setup();
    const view = renderWithProviders(<AdminDocumentsWorkspace />);

    const file = new File(["hello"], "guide.pdf", { type: "application/pdf" });
    const input = view.container.querySelector('input[type="file"]') as HTMLInputElement | null;
    if (!input) throw new Error("file input not found");
    await user.upload(input, file);
    await user.click(screen.getByRole("button", { name: "提交" }));

    await waitFor(() => expect(graphClientMocks.submitTextEmbeddingJob).toHaveBeenCalledWith(file));
  });

  it("keeps submit disabled until a file is selected", () => {
    renderWithProviders(<AdminDocumentsWorkspace />);

    expect(screen.getByRole("button", { name: "提交" })).toBeDisabled();
  });

  it("opens a recent job and renders detail", async () => {
    const user = userEvent.setup();
    graphClientMocks.getTextEmbeddingJobStatus.mockResolvedValue({
      job_id: "job-1",
      status: "succeeded",
      progress_text: "已完成 3 个分块",
      result: { file_name: "guide.pdf", chunks: 3 },
    });

    renderWithProviders(<AdminDocumentsWorkspace />);

    await user.click(await screen.findByRole("button", { name: /guide\.pdf/ }));

    expect(await screen.findByText("已完成 3 个分块")).toBeVisible();
    expect(screen.getByText(/chunks/)).toBeVisible();
  });

  it("shows document stats", async () => {
    renderWithProviders(<AdminDocumentsWorkspace />);

    expect(await screen.findByText(/row_count/)).toBeVisible();
    expect(screen.getByText(/12/)).toBeVisible();
  });
});
