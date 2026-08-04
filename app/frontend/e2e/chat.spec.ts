import { expect, test } from "@playwright/test";

import { loginUser } from "./helpers";

test("聊天支持新建、发送、重发、回滚和重命名", async ({ page }) => {
  await loginUser(page);

  await page.getByRole("button", { name: "新建会话" }).first().click();
  await expect(page.getByText("新建会话").first()).toBeVisible();

  const composer = page.getByRole("textbox", { name: "问题输入" });
  await composer.fill("高血压患者需要注意什么？");
  await page.getByRole("button", { name: "发送" }).click();

  await expect(page.getByText("基于当前测试知识库")).toBeVisible();
  await expect(page.getByText("以上内容仅供参考，不构成诊断或治疗建议").first()).toBeVisible();

  await page.getByRole("button", { name: "重新发送" }).first().click();
  await expect(composer).toHaveValue("高血压患者需要注意什么？");

  await page.getByRole("button", { name: "回滚到此问题" }).first().click();
  await expect(composer).toHaveValue("高血压患者需要注意什么？");

  await page.locator('[data-testid^="session-rename-button-"]').first().click();
  const renameInput = page.locator('[data-testid^="session-rename-input-"]').first();
  await renameInput.fill("血压管理测试会话");
  await renameInput.blur();
  await expect(page.getByText("血压管理测试会话")).toBeVisible();
});
