import { expect, type Page } from "@playwright/test";

export async function loginUser(page: Page) {
  await page.goto("/login");
  await page.getByLabel("用户名").fill("test_user");
  await page.getByLabel("密码").fill("password1");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page).toHaveURL(/\/chat$/);
}

export async function loginAdmin(page: Page) {
  const adminNav = page.getByRole("link", { name: "会话管理" });
  const loginButton = page.getByRole("button", { name: "管理员登录" }).first();
  if (!(await adminNav.isVisible().catch(() => false))) {
    await expect(loginButton).toBeVisible();
    await loginButton.click();
    await page.getByLabel("用户名").fill("admin");
    await page.getByLabel("密码").fill("admin123");
    await page.getByRole("dialog").getByRole("button", { name: "管理员登录" }).click();
  }
  await expect(adminNav).toBeVisible();
}

export async function seedChatTurn(page: Page, question = "请给我一条高血压随访建议") {
  await loginUser(page);
  const composer = page.getByRole("textbox", { name: "问题输入" });
  await composer.fill(question);
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.getByText("基于当前测试知识库")).toBeVisible();
}
