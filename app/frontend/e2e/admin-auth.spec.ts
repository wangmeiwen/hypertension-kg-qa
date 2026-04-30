import { expect, test } from "@playwright/test";

import { loginAdmin } from "./helpers";

test("管理员错误密码会显示失败提示", async ({ page }) => {
  await page.goto("/admin/sessions");

  await page.getByRole("button", { name: "管理员登录" }).click();
  await page.getByLabel("用户名").fill("admin");
  await page.getByLabel("密码").fill("wrong-password");
  await page.getByRole("dialog").getByRole("button", { name: "管理员登录" }).click();

  await expect(page.getByText("用户名或密码错误")).toBeVisible();
});

test("管理员正确登录后进入后台", async ({ page }) => {
  await page.goto("/admin/sessions");
  await loginAdmin(page);

  await expect(page.getByRole("heading", { name: "管理后台" })).toBeVisible();
});
