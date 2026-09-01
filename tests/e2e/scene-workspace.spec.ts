import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const READY_NAME = "e2e-gate-ready";

async function readyProjectId(request: APIRequestContext): Promise<string> {
  const listed = await request.get("http://127.0.0.1:8000/api/v1/projects");
  expect(listed.ok()).toBeTruthy();
  const projects = await listed.json() as { id: string; name: string }[];
  const ready = projects.find((item) => item.name === READY_NAME);
  expect(ready, "offline seed must include e2e-gate-ready").toBeTruthy();
  return ready!.id;
}

async function screenshot(page: Page, name: string) {
  await page.screenshot({
    path: test.info().outputPath(name),
    fullPage: true,
  });
}

test.describe("场景资产工作区（离线假数据）", () => {
  test("列表、新建、变体、归档与剧本绑定", async ({ page, request }) => {
    const forbidden: string[] = [];
    page.on("request", (outgoing) => {
      const path = new URL(outgoing.url()).pathname;
      if (/\/(verify|discover|balance|generate|candidates)$/.test(path) && outgoing.method() === "POST") {
        forbidden.push(`${outgoing.method()} ${path}`);
      }
    });

    const id = await readyProjectId(request);
    const sceneName = `林间木屋 ${Date.now()}`;
    await page.goto(`/projects/${id}/assets/scenes`);
    await expect(page.getByRole("link", { name: "场景资产" })).toHaveAttribute("aria-current", "page");
    await expect(page.getByRole("button", { name: "新建场景" })).toBeVisible();
    await screenshot(page, "scene-list.png");

    await page.getByRole("button", { name: "新建场景" }).click();
    const createDialog = page.getByRole("dialog", { name: "新建场景资产" });
    await expect(createDialog).toBeVisible();
    await createDialog.getByLabel("场景名称").fill(sceneName);
    await createDialog.getByLabel("场景描述").fill("壁炉在正北墙面");
    await createDialog.getByLabel("室内或室外").selectOption("true");
    await createDialog.getByRole("button", { name: "保存" }).click();
    await expect(page.getByRole("option", { name: new RegExp(sceneName) })).toBeVisible();
    await expect(page.getByRole("heading", { name: new RegExp(sceneName) })).toBeVisible();

    await page.getByRole("button", { name: "添加变体" }).click();
    const variantDialog = page.getByRole("dialog", { name: "添加环境变体" });
    await variantDialog.getByLabel("变体名称").fill("暴雨黄昏");
    await variantDialog.getByLabel("变体时间").selectOption("dusk");
    await variantDialog.getByLabel("变体天气").fill("rain");
    await variantDialog.getByRole("button", { name: "保存变体" }).click();
    await expect(page.getByText(/暴雨黄昏/)).toBeVisible();
    await screenshot(page, "scene-detail-variants.png");

    await page.getByRole("link", { name: /漫画剧本/ }).click();
    await expect(page).toHaveURL(new RegExp(`/projects/${id}/script$`));
    const sceneSelect = page.getByLabel("选择场景资产");
    await expect(sceneSelect).toBeVisible();
    await expect(sceneSelect).toContainText(sceneName);
    await sceneSelect.selectOption({ label: `${sceneName} · 室内` });
    await expect(page.getByLabel("选择环境变体")).toBeEnabled();
    await screenshot(page, "scene-picker.png");
    expect(forbidden).toEqual([]);
  });
});
