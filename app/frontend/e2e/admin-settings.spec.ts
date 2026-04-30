import { expect, test } from "@playwright/test";

import { loginAdmin } from "./helpers";

test("系统设置展示运行时信息", async ({ page }) => {
  await page.goto("/admin/settings");
  await loginAdmin(page);

  await expect(page.getByText("运行时开关")).toBeVisible();
  await expect(page.getByText("test_mode").first()).toBeVisible();
  await expect(page.getByText("后端地址")).toBeVisible();
});
