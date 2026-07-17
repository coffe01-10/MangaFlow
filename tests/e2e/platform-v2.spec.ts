import { expect, test, type APIRequestContext } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

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
  await expect(page).toHaveURL(new RegExp(`/projects/${id}/assets/characters$`));
  await expect(page.getByRole("link", { name: "人物设定" })).toHaveAttribute("aria-current", "page");
  await page.goBack();
  await expect(page).toHaveURL(new RegExp(`/projects/${id}/source$`));
  await page.reload();
  await expect(page.getByRole("link", { name: /原作与修订/ })).toHaveAttribute("aria-current", "page");

  await page.getByRole("link", { name: /在工作流中查看/ }).click();
  await expect(page).toHaveURL(new RegExp(`/projects/${id}/workflow$`));
  await expect(page.getByText("流程编排", { exact: true })).toBeVisible();
  await expect(page.locator(".react-flow")).toBeVisible();
  await expect(page.getByRole("button", { name: "发布", exact: true })).toBeEnabled();
});

test("核心路由遵守首屏 API 请求预算", async ({ page, request }) => {
  const id = await projectId(request);
  let apiRequests = 0;
  let apiPaths: string[] = [];
  page.on("request", (outgoing) => {
    if (
      outgoing.url().includes("/api/v1/")
      && ["fetch", "xhr"].includes(outgoing.resourceType())
    ) {
      apiRequests += 1;
      apiPaths.push(new URL(outgoing.url()).pathname);
    }
  });

  for (const [path, budget] of [
    ["/", 3],
    [`/projects/${id}/assets/characters`, 6],
    [`/projects/${id}/storyboard`, 6],
    [`/projects/${id}/jobs`, 6],
    [`/projects/${id}/generate`, 8],
  ] as const) {
    await page.goto("about:blank");
    apiRequests = 0;
    apiPaths = [];
    await page.goto(path);
    await page.waitForLoadState("networkidle");
    expect(apiRequests, `${path} 首屏 API 请求数：${apiPaths.join("、")}`).toBeLessThanOrEqual(budget);
  }
});

test("核心页面没有严重或致命 Axe 问题", async ({ page, request }) => {
  const id = await projectId(request);
  for (const path of [
    "/",
    `/projects/${id}/assets/characters`,
    `/projects/${id}/storyboard`,
    `/projects/${id}/generate`,
    `/projects/${id}/jobs`,
    `/projects/${id}/workflow`,
    "/settings",
  ]) {
    await page.goto(path);
    await page.waitForLoadState("networkidle");
    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();
    const blocking = results.violations.filter((item) =>
      ["serious", "critical"].includes(item.impact ?? ""),
    );
    expect(
      blocking,
      `${path}：${blocking.map((item) => `${item.id}(${item.nodes.length})`).join("、")}`,
    ).toEqual([]);
  }
});
