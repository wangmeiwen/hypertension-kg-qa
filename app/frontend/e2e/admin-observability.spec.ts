import { expect, test } from "@playwright/test";

import { loginAdmin, seedChatTurn } from "./helpers";

test("Agent 观测支持按会话、轮次和泳道查看事件详情", async ({ page }) => {
  await seedChatTurn(page);

  await page.goto("/admin/observability");
  await loginAdmin(page);

  await page.getByRole("button", { name: "Agent 观测" }).first().click();
  await expect(page.getByText("主控编排").first()).toBeVisible();
  await expect(page.getByText("图谱代理").first()).toBeVisible();
  await expect(page.getByText("检索代理").first()).toBeVisible();

  await page.locator('button[type="button"]').filter({ hasText: "主控编排" }).first().click();
  await expect(page.getByText("模型输入", { exact: true })).toBeVisible();
  await expect(page.getByText("工具结果", { exact: true })).toBeVisible();
});
