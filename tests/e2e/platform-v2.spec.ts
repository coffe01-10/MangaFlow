import { expect, test, type APIRequestContext } from "@playwright/test";

async function projectId(request: APIRequestContext): Promise<string> {
  const listed = await request.get("http://127.0.0.1:8000/api/v1/projects");
  expect(listed.ok()).toBeTruthy();
  const projects = await listed.json() as { id: string }[];
  if (projects[0]) return projects[0].id;
  const created = await request.post("http://127.0.0.1:8000/api/v1/projects", {
    data: { name: "浏览器验收项目" },
  });
  expect(created.ok()).toBeTruthy();
  return (await created.json() as { id: string }).id;
}

test("首页、帮助和设置使用统一的真实导航", async ({ page }) => {
  await page.goto("/");
  const globalNavigation = page.getByRole("navigation", { name: "全局导航" });
  await expect(globalNavigation).toBeVisible();
  await expect(page.locator("aside.rail")).toHaveCount(0);

  await globalNavigation.getByRole("link", { name: "设置", exact: true }).click();
  await expect(page).toHaveURL(/\/settings$/);
  await expect(page.getByText("系统设置与运行诊断", { exact: true })).toBeVisible();

  await page.goBack();
  await page.getByRole("navigation", { name: "全局导航" }).getByRole("link", { name: "帮助", exact: true }).click();
  await expect(page).toHaveURL(/\/help$/);
  await expect(page.getByRole("heading", { name: /把复杂的漫画生产/ })).toBeVisible();
});

test("项目阶段可深链、后退、刷新并进入真实工作流", async ({ page, request }) => {
  const id = await projectId(request);
  await page.goto(`/projects/${id}/source`);
  await expect(page.getByRole("link", { name: /原作与修订/ })).toHaveAttribute("aria-current", "page");

  await page.getByRole("link", { name: /参考资产/ }).click();
  await expect(page).toHaveURL(new RegExp(`/projects/${id}/assets$`));
  await page.goBack();
  await expect(page).toHaveURL(new RegExp(`/projects/${id}/source$`));
  await page.reload();
  await expect(page.getByRole("link", { name: /原作与修订/ })).toHaveAttribute("aria-current", "page");

  await page.getByRole("link", { name: /在工作流中查看/ }).click();
  await expect(page).toHaveURL(new RegExp(`/projects/${id}/workflow$`));
  await expect(page.getByText("流程编排", { exact: true })).toBeVisible();
  await expect(page.locator(".react-flow")).toBeVisible();
  await expect(page.getByRole("button", { name: /发布/ })).toBeEnabled();
});
