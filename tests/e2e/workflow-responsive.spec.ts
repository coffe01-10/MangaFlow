import { expect, test } from "@playwright/test";

// #790 回归钉：窄视口下工具栏控件与运行页脚必须可达——
// 选择器/保存/校验/导入/导出/发布不被裁切或遮挡，运行页脚在滚动中
// 始终钉在视口底部（sticky 生效而不是被 .studio 的 overflow 吞掉），
// 保存提示条不得覆盖换行后的顶栏控件。
test("窄视口下工作流编辑控件与运行页脚保持可达", async ({ page, request }) => {
  const created = await request.post("http://127.0.0.1:8000/api/v1/projects", {
    data: { name: "窄视口工作流" },
  });
  expect(created.ok()).toBeTruthy();
  const id = ((await created.json()) as { id: string }).id;

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`/projects/${id}/workflow`);
  await expect(page.getByText("流程编排", { exact: true })).toBeVisible();
  await expect(page.locator(".react-flow__node").first()).toBeVisible();

  const withinViewport = async (box: { x: number; y: number; width: number; height: number }, what: string) => {
    expect(box.x, `${what} 不应在视口左侧之外`).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width, `${what} 不应溢出 390px 视口右侧`).toBeLessThanOrEqual(390);
  };

  // 顶栏编辑控件全部可达且不横向溢出。
  for (const name of ["保存", "校验", "导出", "发布"]) {
    const control = page.getByRole("button", { name, exact: true });
    await expect(control).toBeVisible();
    await withinViewport((await control.boundingBox())!, `「${name}」按钮`);
  }
  const importLabel = page.getByText("导入", { exact: true });
  await expect(importLabel).toBeVisible();
  await withinViewport((await importLabel.boundingBox())!, "「导入」入口");

  // 工作流选择器可见且未换出视口。
  const selector = page.locator("header select").first();
  await expect(selector).toBeVisible();
  await withinViewport((await selector.boundingBox())!, "工作流选择器");

  // 页面不产生横向滚动。
  const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
  expect(scrollWidth).toBeLessThanOrEqual(390);

  // 拖动节点触发自动保存，提示条在文档流中位于顶栏之下，不覆盖控件。
  const node = page.locator(".react-flow__node").first();
  const nodeBox = (await node.boundingBox())!;
  await page.mouse.move(nodeBox.x + 36, nodeBox.y + 12);
  await page.mouse.down();
  await page.mouse.move(nodeBox.x + 116, nodeBox.y + 12, { steps: 6 });
  await page.mouse.up();
  const notice = page.getByRole("status").filter({ hasText: "草稿已保存" });
  await expect(notice).toBeVisible({ timeout: 10_000 });
  const noticeBox = (await notice.boundingBox())!;
  const saveBox = (await page.getByRole("button", { name: "保存", exact: true }).boundingBox())!;
  expect(noticeBox.y, "保存提示条不得覆盖顶栏控件").toBeGreaterThanOrEqual(saveBox.y + saveBox.height);

  // 运行页脚钉在视口底部，滚动整页叠放内容时仍然可达。
  const runButton = page.getByRole("button", { name: "运行工作流", exact: true });
  const pinned = async () => {
    await expect(runButton).toBeVisible();
    const box = (await runButton.boundingBox())!;
    expect(box.y, "运行页脚应钉在视口内").toBeGreaterThanOrEqual(0);
    expect(box.y + box.height, "运行页脚应钉在视口底部").toBeLessThanOrEqual(844);
    expect(box.y + box.height, "运行页脚应在视口底部附近").toBeGreaterThan(700);
  };
  await pinned();
  await page.mouse.wheel(0, 500);
  await page.waitForTimeout(300);
  await pinned();
});
