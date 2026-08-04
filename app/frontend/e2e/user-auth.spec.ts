import { expect, test } from "@playwright/test";

test("用户可以注册、访问聊天并退出登录", async ({ page }) => {
  await page.goto("/register");
  await page.getByLabel("用户名").fill("new_user");
  await page.getByLabel("密码", { exact: true }).fill("password1");
  await page.getByLabel("确认密码").fill("password1");
  await page.getByRole("button", { name: "注册并登录" }).click();

  await expect(page).toHaveURL(/\/chat$/);
  await expect(page.getByRole("textbox", { name: "问题输入" })).toBeVisible();
  await page.getByRole("button", { name: "退出登录" }).click();
  await expect(page).toHaveURL(/\/login$/);

  await page.goto("/chat");
  await expect(page).toHaveURL(/\/login$/);
});
