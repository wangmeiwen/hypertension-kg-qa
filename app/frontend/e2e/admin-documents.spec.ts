import { expect, test } from "@playwright/test";

import { loginAdmin } from "./helpers";

test("文档向量化支持文件提交和任务详情", async ({ page }) => {
  await page.goto("/admin/documents");
  await loginAdmin(page);

  await expect(page.getByRole("heading", { name: "文档向量化" })).toBeVisible();
  await expect(page.getByRole("button", { name: "提交" })).toBeDisabled();

  await page.locator('input[type="file"]').first().setInputFiles({
    name: "document.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("高血压随访与降压治疗"),
  });
  await page.getByRole("button", { name: "提交" }).click();
  await expect(page.getByText("任务已提交到后台。").first()).toBeVisible();

  await expect(page.getByRole("button", { name: /document\.txt/ })).toBeVisible();
  await page.getByRole("button", { name: /document\.txt/ }).click();
  await expect(page.getByText(/doc-job-/)).toBeVisible();
});
