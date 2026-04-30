import { expect, test } from "@playwright/test";

import { loginAdmin } from "./helpers";

test("后台图谱管理支持节点、关系、Cypher、导入和向量化", async ({ page }) => {
  await page.goto("/admin/graph");
  await loginAdmin(page);

  await expect(page.getByRole("heading", { name: "图谱管理" })).toBeVisible();
  await expect(page.getByText("高血压")).toBeVisible();

  await page.getByRole("button", { name: "创建节点" }).first().click();
  await page.getByPlaceholder("节点标签").fill("Guideline");
  await page.getByRole("dialog").getByRole("textbox").last().fill('{\n  "name": "2026 指南"\n}');
  await page.getByRole("dialog").getByRole("button", { name: "保存" }).click();
  await expect(page.getByText("节点已创建。")).toBeVisible();

  await page.getByRole("button", { name: "创建关系" }).click();
  await page.getByPlaceholder("源节点 ID").fill("100");
  await page.getByPlaceholder("目标节点 ID").fill("101");
  await page.getByPlaceholder("关系类型").fill("RELATED_TO");
  await page.getByRole("dialog").getByRole("button", { name: "保存" }).click();
  await expect(page.getByText("关系已创建。")).toBeVisible();

  await page.getByRole("button", { name: "执行 Cypher" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "提交" }).click();
  await expect(page.getByText('"label": "Disease"')).toBeVisible();
  await page.getByRole("dialog").getByRole("button", { name: "取消" }).click();

  await page.locator('input[type="file"]').first().setInputFiles({
    name: "graph-import.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("高血压 图谱导入 测试"),
  });
  await page.getByRole("button", { name: "导入图谱" }).click();
  await expect(page.getByText("图谱导入任务已提交。").first()).toBeVisible();

  await page.getByRole("button", { name: "图谱向量化" }).last().click();
  await expect(page.getByText("图谱向量化任务已提交。").first()).toBeVisible();
});
